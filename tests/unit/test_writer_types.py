# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the four writer types.

Each type differs in ways that were measured on hardware rather than assumed,
and every one of those differences is a way to break a device quietly. These
lock them in.
"""
import unittest

from loader import logs, sapv2


def desired(**kw):
    base = {"name": "w", "type": "udp_syslog", "ip": "192.0.2.10",
            "state": "present", "subscriptions": [], "message_log_settings": {}}
    base.update(kw)
    return base


def absent_actual():
    return {"exists": False, "properties": {}, "subscriptions": {},
            "message_log_settings": {}}


def present_actual(props):
    return {"exists": True, "properties": props, "subscriptions": {},
            "message_log_settings": {}}


class TestPaths(unittest.TestCase):
    def test_each_type_addresses_its_own_sap_type(self):
        self.assertEqual(logs.writer_path("w", "udp_syslog"),
                         "Logs#0.UdpSysLogWriter#w")
        self.assertEqual(logs.writer_path("w", "tcp_client"),
                         "Logs#0.TcpClientWriter#w")
        self.assertEqual(logs.writer_path("w", "tcp_listener"),
                         "Logs#0.TcpListenerWriter#w")
        self.assertEqual(logs.writer_path("w", "log_file"),
                         "Logs#0.LogFileWriter#w")

    def test_log_file_names_contain_dots_and_are_bracket_quoted(self):
        self.assertEqual(logs.writer_path("Connected_Msg.log", "log_file"),
                         "Logs#0.LogFileWriter#[Connected_Msg.log]")

    def test_default_type_is_udp_and_keeps_old_callers_working(self):
        self.assertEqual(logs.writer_path("w"), "Logs#0.UdpSysLogWriter#w")

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(KeyError):
            logs.writer_type("carrier_pigeon")


class TestCreation(unittest.TestCase):
    def test_udp_init_params(self):
        wt = logs.writer_type("udp_syslog")
        self.assertEqual(wt.init_params(desired()),
                         [("name", "w"), ("ip", "192.0.2.10")])

    def test_tcp_listener_init_params_carry_the_port(self):
        wt = logs.writer_type("tcp_listener")
        self.assertEqual(wt.init_params(desired(type="tcp_listener", ip=None, port=1515)),
                         [("name", "w"), ("port", 1515)])

    def test_log_file_init_takes_only_a_name(self):
        wt = logs.writer_type("log_file")
        self.assertEqual(wt.init_params(desired(type="log_file", ip=None, name="x.log")),
                         [("name", "x.log")])

    def test_tcp_client_is_not_creatable(self):
        # Nine init vocabularies were tried on hardware; all no-ops.
        self.assertFalse(logs.writer_type("tcp_client").creatable)

    def test_planning_a_missing_tcp_client_is_blocked_not_attempted(self):
        actions = logs.plan(desired(type="tcp_client", ip=None), absent_actual())
        self.assertEqual([a.kind for a in actions], ["blocked_uncreatable"])
        self.assertIn("cannot be created", actions[0].summary)

    def test_applying_a_blocked_plan_writes_nothing(self):
        class Recorder(object):
            def __init__(self):
                self.writes = []

            def init(self, *a):
                self.writes.append(a)

            def set(self, *a):
                self.writes.append(a)

            def delete(self, *a):
                self.writes.append(a)

        client = Recorder()
        actions = logs.plan(desired(type="tcp_client", ip=None), absent_actual())
        logs.apply_plan(client, desired(type="tcp_client", ip=None), actions)
        self.assertEqual(client.writes, [])


class TestRequiredAndIgnoredParams(unittest.TestCase):
    """Missing or ignored parameters produce broken objects, not errors."""

    def test_udp_requires_ip(self):
        self.assertTrue(logs.validate_writer(desired(ip=None)))

    def test_listener_requires_port(self):
        problems = logs.validate_writer(desired(type="tcp_listener", ip=None))
        self.assertTrue(any("requires 'port'" in p for p in problems))

    def test_listener_rejects_ip_because_the_device_ignores_it(self):
        problems = logs.validate_writer(
            desired(type="tcp_listener", ip="192.0.2.10", port=1515))
        self.assertTrue(any("ignores 'ip'" in p for p in problems))

    def test_log_file_needs_neither(self):
        self.assertEqual(logs.validate_writer(desired(type="log_file", ip=None)), [])

    def test_writable_properties_are_enforced_per_type(self):
        # Listening exists on tcp_listener but not on log_file.
        self.assertEqual(
            logs.validate_writer(desired(type="tcp_listener", ip=None, port=1515,
                                         properties={"Listening": True})), [])
        self.assertTrue(
            logs.validate_writer(desired(type="log_file", ip=None,
                                         properties={"Listening": True})))
        # UdpSysLogWriter has no writable properties at all.
        self.assertTrue(
            logs.validate_writer(desired(properties={"OfflineMaxCacheCount": 5})))


class TestEndpoints(unittest.TestCase):
    def test_listener_endpoint_is_bind_address_not_a_uri(self):
        wt = logs.writer_type("tcp_listener")
        self.assertEqual(wt.endpoint_property, "EndpointId")
        self.assertEqual(wt.endpoint_template({"port": 1515}), "0.0.0.0:1515")

    def test_log_file_has_no_endpoint(self):
        self.assertIsNone(logs.writer_type("log_file").endpoint_property)

    def test_log_file_plan_ignores_endpoint_entirely(self):
        actions = logs.plan(desired(type="log_file", ip=None, name="x.log"),
                            present_actual({"Name": "x.log"}))
        self.assertEqual(actions, [])

    def test_listener_port_change_is_blocked_by_default(self):
        actual = present_actual({"Name": "w", "EndpointId": "0.0.0.0:1515"})
        actions = logs.plan(desired(type="tcp_listener", ip=None, port=9999), actual)
        self.assertEqual([a.kind for a in actions], ["blocked_immutable"])

    def test_listener_port_change_can_be_replaced_because_it_is_creatable(self):
        actual = present_actual({"Name": "w", "EndpointId": "0.0.0.0:1515"})
        actions = logs.plan(desired(type="tcp_listener", ip=None, port=9999), actual,
                            on_immutable_change="replace")
        self.assertEqual(actions[0].kind, "writer_replace")


class TestNeverDeleteWhatCannotBeRecreated(unittest.TestCase):
    """The most important rail added with the new types.

    A TcpClientWriter cannot be created. Replacing one would delete a working
    writer and then silently fail to rebuild it, so replace is refused for
    that type even when explicitly requested.
    """

    def setUp(self):
        self.actual = present_actual(
            {"Name": "w", "RemoteEndpointUri": "tcp://192.0.2.21:1515/"})

    def test_replace_is_refused_for_an_uncreatable_type(self):
        actions = logs.plan(desired(type="tcp_client", ip="192.0.2.99"), self.actual,
                            on_immutable_change="replace")
        self.assertEqual([a.kind for a in actions], ["blocked_immutable"])
        self.assertIn("cannot be recreated", actions[0].summary)

    def test_no_delete_is_emitted(self):
        class Recorder(object):
            def __init__(self):
                self.writes = []

            def init(self, *a):
                self.writes.append(("init",) + a)

            def set(self, *a):
                self.writes.append(("set",) + a)

            def delete(self, *a):
                self.writes.append(("delete",) + a)

        client = Recorder()
        d = desired(type="tcp_client", ip="192.0.2.99")
        logs.apply_plan(client, d, logs.plan(d, self.actual,
                                             on_immutable_change="replace"))
        self.assertEqual(client.writes, [])

    def test_explicit_absent_is_still_allowed_but_flagged_one_way(self):
        # Deleting deliberately is fine - it is repairing drift by deletion
        # that is refused.
        actions = logs.plan(desired(type="tcp_client", ip=None, state="absent"),
                            self.actual)
        self.assertEqual([a.kind for a in actions], ["writer_delete"])
        self.assertIn("cannot be recreated", actions[0].summary)
        self.assertTrue(actions[0].destructive)


class TestWriterProperties(unittest.TestCase):
    def test_change_is_planned(self):
        actual = present_actual({"Name": "w", "RemoteEndpointUri": "tcp://192.0.2.21:1515/",
                                 "OfflineMaxCacheCount": "10"})
        actions = logs.plan(
            desired(type="tcp_client", ip=None,
                    properties={"OfflineMaxCacheCount": 20}), actual)
        self.assertEqual([a.kind for a in actions], ["writer_properties_set"])
        self.assertEqual(actions[0].detail["changes"], {"OfflineMaxCacheCount": "20"})

    def test_no_change_when_already_correct(self):
        actual = present_actual({"Name": "w", "RemoteEndpointUri": "tcp://192.0.2.21:1515/",
                                 "OfflineMaxCacheCount": "20"})
        self.assertEqual(
            logs.plan(desired(type="tcp_client", ip=None,
                              properties={"OfflineMaxCacheCount": 20}), actual), [])

    def test_verify_catches_a_property_that_did_not_take(self):
        class FakeClient(object):
            def get(self, path, prop=None):
                if path == "Logs#0.TcpClientWriter#w":
                    return {"Name": "w", "OfflineMaxCacheCount": "10"}
                return {}

            def children(self, path):
                return {}

        with self.assertRaises(sapv2.SapV2VerifyError) as ctx:
            logs.verify(FakeClient(),
                        desired(type="tcp_client", ip=None,
                                properties={"OfflineMaxCacheCount": 20}))
        self.assertIn("OfflineMaxCacheCount", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class TestDiffRendering(unittest.TestCase):
    """--diff must describe what will actually happen, not an idealised state."""

    def test_absent_writer_renders_as_absent(self):
        d = logs.render_state(desired(), absent_actual())
        self.assertIn("(absent)", d["before"])
        self.assertIn("RemoteEndpointUri: udp://192.0.2.10:514/", d["after"])

    def test_existing_subscriptions_survive_in_after_when_not_purging(self):
        # Without purge the module leaves unmanaged subscriptions alone, so
        # showing them removed would promise a deletion that never happens.
        actual = present_actual({"Name": "w", "RemoteEndpointUri": "udp://192.0.2.10:514/"})
        actual["subscriptions"] = {"6001": {"Subscription": "sub MemorySlots#0 SlotValue",
                                            "Severity": "Informational",
                                            "CustomName": "memoryslot-value"}}
        d = logs.render_state(desired(), actual, purge_subscriptions=False)
        self.assertIn("6001", d["after"])

    def test_purge_shows_them_going_away(self):
        actual = present_actual({"Name": "w", "RemoteEndpointUri": "udp://192.0.2.10:514/"})
        actual["subscriptions"] = {"6001": {"Subscription": "sub MemorySlots#0 SlotValue",
                                            "Severity": "Informational",
                                            "CustomName": "memoryslot-value"}}
        d = logs.render_state(desired(), actual, purge_subscriptions=True)
        self.assertIn("6001", d["before"])
        self.assertNotIn("6001", d["after"])

    def test_state_absent_renders_removal(self):
        actual = present_actual({"Name": "w", "RemoteEndpointUri": "udp://192.0.2.10:514/"})
        d = logs.render_state(desired(state="absent"), actual)
        self.assertIn("(absent)", d["after"])
