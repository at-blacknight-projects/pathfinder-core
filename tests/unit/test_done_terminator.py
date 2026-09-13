# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The $DONE reply terminator, and the deep read it makes worth doing.

All of this is measured against a Core PRO, not taken from the manual - the
manual also documents $ACK, which turned out to acknowledge an invalid enum, an
unknown property and a read-only property, and is deliberately unused.
"""
import time
import unittest

from loader import logs, sapv2


WRITER = "Logs#0.UdpSysLogWriter#alloy_site1"


class TestStripping(unittest.TestCase):
    """The terminator lands INSIDE the last property unless it is stripped."""

    def test_it_would_otherwise_be_folded_into_the_last_value(self):
        # The exact shape read off the device: appended after the last
        # property, space-separated rather than comma-separated.
        line = ('indi %s.MessageLogSettings#0 Lwrp=Both, '
                'FriendlyName="MessageLogSettings#0" $DONE' % WRITER)
        props = sapv2.parse_indi(line)[WRITER + ".MessageLogSettings#0"]
        self.assertEqual(props["FriendlyName"], "MessageLogSettings#0")
        self.assertEqual(props["Lwrp"], "Both")

    def test_a_subscription_expression_survives_intact(self):
        # The one that would have hurt: a corrupted Subscription compares
        # unequal to the desired expression on every run, forever, and the
        # module would rewrite it every time and still never converge.
        expression = "sub Devices#0 Connected $MAX_DEPTH=-1"
        line = ('indi %s.LogSubscription#1001 SubscriptionTypeId=1001, '
                'Subscription="%s" $DONE' % (WRITER, expression))
        props = sapv2.parse_indi(line)[WRITER + ".LogSubscription#1001"]
        self.assertEqual(props["Subscription"], expression)

    def test_stripping_is_harmless_when_absent(self):
        self.assertEqual(sapv2.strip_done("indi Logs#0 A=1"), "indi Logs#0 A=1")

    def test_a_dollar_token_inside_a_value_is_not_mistaken_for_it(self):
        # $MAX_DEPTH=-1 appears inside real subscription expressions.
        line = 'indi %s.LogSubscription#1 Subscription="sub X $MAX_DEPTH=-1"' % WRITER
        props = sapv2.parse_indi(line)[WRITER + ".LogSubscription#1"]
        self.assertEqual(props["Subscription"], "sub X $MAX_DEPTH=-1")


class TestDetection(unittest.TestCase):
    """A reply counts as terminated only once the line carrying it is complete."""

    def test_complete_line_is_detected(self):
        self.assertTrue(sapv2.has_done('indi Logs#0 A=1 $DONE\r\n'))

    def test_a_partial_line_is_not(self):
        # A chunk boundary landing inside the token must not end the read - the
        # rest of the reply would be truncated and silently lost.
        self.assertFalse(sapv2.has_done('indi Logs#0 A=1 $DONE'))

    def test_absent_is_not_detected(self):
        self.assertFalse(sapv2.has_done('indi Logs#0 A=1\r\n'))
        self.assertFalse(sapv2.has_done(""))

    def test_the_empty_reply_form_is_terminated_too(self):
        # An unknown path answers `indi NONE $DONE`, so even a miss returns
        # immediately instead of waiting out the idle gap. Reading a writer
        # that does not exist is the hot path of every create.
        self.assertTrue(sapv2.has_done("indi NONE $DONE\r\n"))
        self.assertEqual(sapv2.parse_indi("indi NONE $DONE\r\n"), {})


class FakeSocket(object):
    """Serves one queued reply per command, and records what was sent."""

    def __init__(self, replies):
        self.pending = list(replies)
        self.ready = []
        self.sent = []
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, data):
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        self.sent.append(text)
        # Nothing answers a bare newline, which is what connect() sends to
        # clear the device's line parser. A fake that replies to it would hide
        # the very stall this is here to catch.
        if text.strip() and self.pending:
            self.ready.append(self.pending.pop(0))

    def recv(self, _n):
        if self.ready:
            return self.ready.pop(0)
        raise sapv2.socket.timeout()

    def close(self):
        pass

    def commands(self):
        return [c.strip() for c in self.sent if c.strip()]


def client_with(replies, **kwargs):
    client = sapv2.SapV2Client(host="unused", idle_timeout=0.01,
                               read_timeout=0.05, **kwargs)
    client._sock = FakeSocket(replies)
    return client


class TestReadsRequestIt(unittest.TestCase):
    def test_every_read_asks_for_the_terminator(self):
        client = client_with([b'indi Logs#0 A=1 $DONE\r\n'])
        client.get("Logs#0")
        self.assertEqual(client._sock.commands(), ["get Logs#0 $DONE"])

    def test_a_listing_asks_too(self):
        client = client_with([b'indi Logs#0.X#1 $DONE\r\n'])
        client.children("Logs#0")
        self.assertEqual(client._sock.commands(), ["get Logs#0. $DONE"])

    def test_a_deep_read_that_returns_the_object_is_one_command(self):
        client = client_with([b'indi Logs#0 A=1 $DONE\r\n'])
        client.tree("Logs#0")
        self.assertEqual(client._sock.commands(), ["get Logs#0 $MAX_DEPTH=-1 $DONE"])

    def test_max_depth_excludes_the_object_so_a_second_read_fills_it_in(self):
        # Measured: $MAX_DEPTH means strictly BELOW the path, so the reply
        # names only the children. Left uncompensated the caller finds nothing
        # at its own path and concludes the object does not exist - silently,
        # and then plans to create something that is already there.
        client = client_with([b'indi Logs#0.Child#1 A=1 $DONE\r\n',
                              b'indi Logs#0 B=2 $DONE\r\n'])
        tree = client.tree("Logs#0")
        self.assertEqual(client._sock.commands(),
                         ["get Logs#0 $MAX_DEPTH=-1 $DONE", "get Logs#0 $DONE"])
        self.assertEqual(tree["Logs#0"], {"B": "2"})
        self.assertEqual(tree["Logs#0.Child#1"], {"A": "1"})

    def test_a_genuinely_absent_object_stays_absent(self):
        client = client_with([b'indi NONE $DONE\r\n', b'indi NONE $DONE\r\n'])
        self.assertEqual(client.tree("Logs#0.UdpSysLogWriter#nope"), {})

    def test_writes_do_not_ask(self):
        # Measured: a write returns nothing whether or not it is requested, and
        # nothing whether or not the write was accepted.
        client = client_with([])
        client.set("Logs#0", [("SkipCleanLogs", "False")])
        self.assertEqual(client._sock.commands(),
                         ["set Logs#0 SkipCleanLogs=False"])


class TestFallback(unittest.TestCase):
    """A firmware that ignores the modifier must still work, just slower."""

    def test_a_reply_without_it_disables_it_and_retries(self):
        client = client_with([b'indi Logs#0 A=1\r\n', b'indi Logs#0 A=1\r\n'])
        self.assertEqual(client.get("Logs#0"), {"A": "1"})
        self.assertFalse(client._done_supported)
        self.assertEqual(client._sock.commands(),
                         ["get Logs#0 $DONE", "get Logs#0"])

    def test_it_is_only_probed_once(self):
        client = client_with([b'indi Logs#0 A=1\r\n'] * 4)
        client.get("Logs#0")
        client.get("Logs#0")
        self.assertEqual(client._sock.commands(),
                         ["get Logs#0 $DONE", "get Logs#0", "get Logs#0"])

    def test_the_fallback_is_recorded_in_the_transcript(self):
        client = client_with([b'indi Logs#0 A=1\r\n'] * 2)
        client.get("Logs#0")
        self.assertTrue(any("falling back" in entry.get("command", "")
                            for entry in client.transcript))

    def test_it_can_be_turned_off_up_front(self):
        client = client_with([b'indi Logs#0 A=1\r\n'], use_done=False)
        client.get("Logs#0")
        self.assertEqual(client._sock.commands(), ["get Logs#0"])


class TestWriteTimeout(unittest.TestCase):
    def test_a_write_waits_far_less_than_a_read(self):
        # Writes never answer, so the old 1.5s idle wait was pure cost on every
        # one of them - roughly half the commands in a from-scratch reconcile.
        client = sapv2.SapV2Client(host="unused")
        self.assertLess(client.write_timeout, client.idle_timeout)
        self.assertEqual(client.write_timeout, sapv2.DEFAULT_WRITE_TIMEOUT)


class TestConnectDoesNotStall(unittest.TestCase):
    """Opening a session used to cost 15 seconds before doing anything.

    connect() sends a bare CRLF to clear any partial line left in the device's
    parser, then read the result with a full drain - which waits read_timeout
    for a reply to a newline. Nothing answers a newline, so every module
    invocation opened with the whole ceiling, and a role running two modules
    against one device paid it twice.
    """

    def setUp(self):
        # connect() opens the socket itself, so hand it a fake one.
        self.real_create = sapv2.socket.create_connection
        self.sock = None

        def fake_create(_address, timeout=None):
            return self.sock

        sapv2.socket.create_connection = fake_create

    def tearDown(self):
        sapv2.socket.create_connection = self.real_create

    def _connect(self, replies, **kwargs):
        self.sock = FakeSocket(replies)
        client = sapv2.SapV2Client(host="unused", verify_login=False, **kwargs)
        client.connect("user", "secret")
        return client

    def test_the_priming_newline_is_flushed_not_drained(self):
        start = time.time()
        self._connect([b"login successful\r\n"], read_timeout=30.0)
        # Nowhere near read_timeout. Before this fix it was the whole ceiling.
        self.assertLess(time.time() - start, 2.0)

    def test_an_explicit_rejection_is_still_caught(self):
        self.sock = FakeSocket([b"login failed\r\n"])
        client = sapv2.SapV2Client(host="unused", verify_login=False)
        with self.assertRaises(sapv2.SapV2AuthError):
            client.connect("user", "wrong")

    def test_the_credential_is_never_recorded(self):
        self.sock = FakeSocket([b"login successful\r\n"])
        client = sapv2.SapV2Client(host="unused", verify_login=False)
        client.connect("user", "hunter2")
        rendered = repr(client.transcript)
        self.assertNotIn("hunter2", rendered)
        self.assertIn("<redacted>", rendered)

    def test_silence_is_not_treated_as_a_failed_login(self):
        # The probe is what proves the session, not the login reply - so a
        # firmware that stays quiet must not look like a rejected credential.
        self._connect([])


class DeepReadClient(object):
    """Serves a whole subtree from one tree() call, counting the commands."""

    def __init__(self, tree):
        self._tree = tree
        self.reads = []

    def tree(self, path, depth=-1):
        self.reads.append(("tree", path))
        wanted = sapv2.normalise_path(path)
        return dict((k, v) for k, v in self._tree.items()
                    if sapv2.normalise_path(k) == wanted
                    or sapv2.normalise_path(k).startswith(wanted + "."))

    def get(self, path, prop=None):
        self.reads.append(("get", path))
        return self._tree.get(path, {})

    def children(self, path):
        self.reads.append(("children", path))
        return dict((k, v) for k, v in self._tree.items()
                    if k.startswith(path + ".") and "." not in k[len(path) + 1:])


class TestReadActualIsOneCommand(unittest.TestCase):
    def _tree(self, subscriptions=3):
        tree = {WRITER: {"Name": "alloy_site1",
                         "RemoteEndpointUri": "udp://192.0.2.10:514/"},
                WRITER + ".MessageLogSettings#0": {"Lwrp": "Both"}}
        for i in range(subscriptions):
            tree["%s.LogSubscription#%d" % (WRITER, 1000 + i)] = {
                "SubscriptionTypeId": str(1000 + i),
                "Subscription": "sub Devices#0 Connected",
                "Severity": "Warning", "CustomName": "sub-%d" % i}
        return tree

    def test_the_command_count_does_not_grow_with_subscriptions(self):
        for count in (1, 27, 100):
            client = DeepReadClient(self._tree(count))
            actual = logs.read_actual(client, "alloy_site1")
            self.assertEqual(len(actual["subscriptions"]), count)
            self.assertEqual(client.reads, [("tree", WRITER)],
                             "%d subscriptions should still be one read" % count)

    def test_it_reads_the_same_state_as_before(self):
        client = DeepReadClient(self._tree(2))
        actual = logs.read_actual(client, "alloy_site1")
        self.assertTrue(actual["exists"])
        self.assertEqual(actual["properties"]["RemoteEndpointUri"],
                         "udp://192.0.2.10:514/")
        self.assertEqual(actual["message_log_settings"], {"Lwrp": "Both"})
        self.assertEqual(sorted(actual["subscriptions"]), ["1000", "1001"])
        self.assertEqual(actual["subscriptions"]["1000"]["CustomName"], "sub-0")

    def test_a_missing_writer_is_still_absent(self):
        client = DeepReadClient({})
        self.assertFalse(logs.read_actual(client, "nope")["exists"])

    def test_a_bracket_quoted_name_is_matched(self):
        path = "Logs#0.LogFileWriter#[audit.log]"
        client = DeepReadClient({path: {"Name": "audit.log"},
                                 path + ".MessageLogSettings#0": {"Lwrp": "Both"}})
        actual = logs.read_actual(client, "audit.log", "log_file")
        self.assertTrue(actual["exists"])
        self.assertEqual(actual["message_log_settings"], {"Lwrp": "Both"})

    def test_a_client_without_a_deep_read_still_works(self):
        # The fallback path, for a firmware where $MAX_DEPTH does nothing.
        class Shallow(DeepReadClient):
            def tree(self, path, depth=-1):
                self.reads.append(("tree", path))
                return {}

        client = Shallow(self._tree(2))
        actual = logs.read_actual(client, "alloy_site1")
        self.assertTrue(actual["exists"])
        self.assertEqual(sorted(actual["subscriptions"]), ["1000", "1001"])
        self.assertEqual(actual["message_log_settings"], {"Lwrp": "Both"})
        self.assertIn(("children", WRITER), client.reads)


if __name__ == "__main__":
    unittest.main()
