# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests locking in protocol behaviour measured on a Core PRO.

Each of these encodes something that was originally assumed wrong. They are
here so a future refactor cannot quietly reintroduce the assumption.
"""
import unittest

from loader import sapv2, subtrees


class TestErrorReply(unittest.TestCase):
    """SapV2 does have an explicit error reply for a rejected operation."""

    REPLY = 'error Logs#0 $OP=constructor $STATUS="Unsupported Operation."'

    def test_parsed(self):
        path, op, status = sapv2.parse_error(self.REPLY)
        self.assertEqual(path, "Logs#0")
        self.assertEqual(op, "constructor")
        self.assertEqual(status, "Unsupported Operation.")

    def test_normal_reply_is_not_an_error(self):
        self.assertIsNone(sapv2.parse_error('indi Logs#0 Ready="True"'))

    def test_empty_is_not_an_error(self):
        self.assertIsNone(sapv2.parse_error(""))


class TestEncapsulation(unittest.TestCase):
    """%BeginEncap% is a general payload wrapper, not specific to SecurityJson."""

    def test_stripped(self):
        self.assertEqual(
            sapv2.strip_encapsulation("%BeginEncap%init Users#0.SapUser Username=Admin"),
            "init Users#0.SapUser Username=Admin")

    def test_both_markers_stripped(self):
        self.assertEqual(
            sapv2.strip_encapsulation("%BeginEncap%{\"a\":1}%EndEncap%"),
            '{"a":1}')

    def test_unwrapped_value_is_untouched(self):
        self.assertEqual(sapv2.strip_encapsulation("plain"), "plain")

    def test_none_is_safe(self):
        self.assertIsNone(sapv2.strip_encapsulation(None))


class TestConstructorIsAHiddenProperty(unittest.TestCase):
    """Constructor is a property, not an operator, and is absent from rfs."""

    class FakeClient(sapv2.SapV2Client):
        def __init__(self, reply):
            super(TestConstructorIsAHiddenProperty.FakeClient, self).__init__(
                host="unused")
            self.reply = reply
            self.commands = []

        def execute(self, command, is_write=False):
            self.commands.append(command)
            return self.reply

    def test_requested_by_property_name(self):
        client = self.FakeClient(
            'indi Users#0.SapUser#Admin '
            'Constructor="%BeginEncap%init Users#0.SapUser Username=Admin"')
        value = client.constructor("Users#0.SapUser#Admin")
        # Asked for as a property on a get, not as a verb.
        self.assertEqual(client.commands, ["get Users#0.SapUser#Admin Constructor"])
        # And the encapsulation wrapper is removed for the caller.
        self.assertEqual(value, "init Users#0.SapUser Username=Admin")

    def test_unsupported_type_returns_none(self):
        client = self.FakeClient("indi NONE")
        self.assertIsNone(client.constructor("Logs#0.UdpSysLogWriter#x"))


class TestInertSessionIsDetected(unittest.TestCase):
    """A bad credential can leave the session inert rather than rejected.

    The device does not acknowledge a successful login, so the absence of a
    rejection proves nothing. An inert session accepts commands and returns
    empty for every read, which downstream looks exactly like "the object does
    not exist" - and in check mode would produce a large, fictional plan
    against a device that is actually fine.
    """

    class FakeSocket(object):
        """Replies only AFTER a command is sent.

        The client flushes pending bytes before sending, precisely so a late
        reply to a previous command cannot be read as this one's. A fake that
        queues its reply up front would have that flush eat it, which is the
        fake being wrong rather than the client.
        """

        def __init__(self, replies):
            self.pending = list(replies)
            self.ready = []
            self.sent = []

        def settimeout(self, _t):
            pass

        def sendall(self, data):
            self.sent.append(data)
            if self.pending:
                self.ready.append(self.pending.pop(0))

        def recv(self, _n):
            if self.ready:
                return self.ready.pop(0)
            raise sapv2.socket.timeout()

        def close(self):
            pass

    def _client(self, replies):
        client = sapv2.SapV2Client(host="unused", idle_timeout=0.01,
                                   read_timeout=0.05)
        # connect() would open a real socket; exercise the post-login logic by
        # attaching the socket directly. Nothing is sent here on purpose - a
        # priming write would queue the fake's reply before execute() gets to
        # flush, and the flush would then discard it.
        client._sock = self.FakeSocket(replies)
        return client

    def test_inert_session_raises_auth_error(self):
        client = self._client([])  # every read returns nothing
        with self.assertRaises(sapv2.SapV2AuthError) as ctx:
            if not client.get(client.LOGIN_PROBE_PATH):
                raise sapv2.SapV2AuthError(
                    "connected to %s but the session is inert: reading %s "
                    "returned nothing." % (client.host, client.LOGIN_PROBE_PATH))
        self.assertIn("inert", str(ctx.exception))

    def test_live_session_probe_succeeds(self):
        client = self._client([b'indi System#0 Ready="True"\r\n'])
        self.assertEqual(client.get("System#0"), {"Ready": "True"})

    def test_verify_login_can_be_disabled(self):
        self.assertFalse(
            sapv2.SapV2Client(host="unused", verify_login=False).verify_login)

    def test_verify_login_defaults_on(self):
        self.assertTrue(sapv2.SapV2Client(host="unused").verify_login)


class TestActionPropertiesAreRefused(unittest.TestCase):
    """Write-only properties are RPC calls, not desired state."""

    def setUp(self):
        self.guard = subtrees.SubtreeGuard()

    def test_activate_scene_is_refused(self):
        # Would put a scene to air on every reconcile. Caught by the subtree
        # classification before the property rail is even reached - two
        # independent reasons to refuse, which is the intent.
        with self.assertRaises(sapv2.SapV2GuardError):
            self.guard.assert_writable("Scenes#0.Scene#1", verb="set",
                                       properties=["ActivateScene"])

    def test_action_refused_even_in_a_declarative_subtree(self):
        # The case that isolates the action rail: ClearLogFile lives under
        # Logs#0, which is otherwise fully writable, so only the property
        # check can catch it.
        with self.assertRaises(sapv2.SapV2GuardError) as ctx:
            self.guard.assert_writable("Logs#0", verb="set",
                                       properties=["ClearLogFile"])
        self.assertIn("write-only ACTION", str(ctx.exception))

    def test_force_service_restart_is_refused(self):
        with self.assertRaises(sapv2.SapV2GuardError):
            self.guard.assert_writable("Devices#0.Fusion#x", verb="set",
                                       properties=["ForceServiceRestart"])


class TestAccessSubtreeIsReadOnly(unittest.TestCase):
    """SecurityJson is RO on measured firmware, so this cannot be reconciled."""

    def setUp(self):
        self.guard = subtrees.SubtreeGuard()

    def test_classified_read_only(self):
        entry = self.guard.classify("System#0.Access#0")
        self.assertEqual(entry.kind, subtrees.READ_ONLY)
        self.assertFalse(entry.writable)

    def test_writes_are_refused(self):
        with self.assertRaises(sapv2.SapV2GuardError) as ctx:
            self.guard.assert_writable("System#0.Access#0", verb="set",
                                       properties=["SecurityJson"])
        self.assertIn("read_only", str(ctx.exception))

    def test_user_security_remains_writable(self):
        # The writable equivalent, which is where enforcement belongs.
        self.guard.assert_writable("Users#0.SapUser#Admin.UserSecurity#Admin",
                                   verb="set", properties=["IsAdmin", "SecurityPaths"])


if __name__ == "__main__":
    unittest.main()


class TestReplyMisattribution(unittest.TestCase):
    """A reply must never be attributed to the wrong command.

    Observed on a slower device: `get System#0` returned a reply naming
    Logs#0, because the read timed out before the device began answering and
    the reply then landed against the next command. A reconciler that accepts
    that plans against another object's state.
    """

    def test_get_refuses_a_reply_for_a_different_path(self):
        client = sapv2.SapV2Client(host="unused")

        class Desynced(object):
            def execute(self, command, is_write=False):
                return 'indi Logs#0 Ready="True", SkipCleanLogs="False"'

        client.execute = Desynced().execute
        # Must be empty, NOT Logs#0's properties.
        self.assertEqual(client.get("System#0"), {})

    def test_get_accepts_a_bracket_normalised_form_of_the_same_path(self):
        client = sapv2.SapV2Client(host="unused")
        client.execute = lambda command, is_write=False: (
            'indi Logs#0.LogFileWriter#[a.log] Name="a.log"')
        self.assertEqual(
            client.get("Logs#0.LogFileWriter#[a.log]"), {"Name": "a.log"})

    def test_get_is_case_insensitive_on_the_path_only(self):
        client = sapv2.SapV2Client(host="unused")
        client.execute = lambda command, is_write=False: 'indi system#0 Ready="True"'
        self.assertEqual(client.get("System#0"), {"Ready": "True"})
