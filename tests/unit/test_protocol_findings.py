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
