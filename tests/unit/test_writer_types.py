# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the four writer types.

Each type differs in ways that were measured on hardware rather than assumed,
and every one of those differences is a way to break a device quietly. These
lock them in.
"""
import contextlib
import unittest

from loader import logs, sapv2, subtrees


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

    def test_tcp_client_is_creatable_with_autoreconnect(self):
        # This type was recorded as impossible on the strength of nine
        # parameter vocabularies that all silently no-op'd. Every one of them
        # was missing autoReconnect, which turns out to be load-bearing:
        # name+ip+port no-ops, name+ip+port+autoReconnect creates.
        wtype = logs.writer_type("tcp_client")
        self.assertTrue(wtype.creatable)
        params = dict(wtype.init_params(
            {"name": "w", "ip": "192.0.2.99", "port": 1515}))
        self.assertEqual(params["autoReconnect"], True)
        self.assertEqual(params["ip"], "192.0.2.99")
        self.assertEqual(params["port"], 1515)

    def test_autoreconnect_renders_bare(self):
        # Quoting an init parameter the device wants bare is a silent no-op,
        # which is the whole reason the render table exists.
        rendered = sapv2.render_init_params(
            logs.writer_type("tcp_client").init_params(
                {"name": "w", "ip": "192.0.2.99", "port": 1515}))
        self.assertIn("autoReconnect=True", rendered)
        self.assertNotIn('autoReconnect="True"', rendered)

    def test_a_tcp_client_without_an_ip_is_refused(self):
        # name= alone DOES create something on hardware - an object pointing at
        # a stale address on port 0. Same zombie shape as a tcp_listener made
        # without a port, so it is refused rather than left behind.
        self.assertTrue(logs.validate_writer(desired(type="tcp_client", ip=None)))

    def test_applying_a_blocked_plan_writes_nothing(self):
        client = Recorder()
        with uncreatable_type():
            actions = logs.plan(desired(type="frozen", ip=None), absent_actual())
            self.assertEqual([a.kind for a in actions], ["blocked_uncreatable"])
            logs.apply_plan(client, desired(type="frozen", ip=None), actions)
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


class Recorder(object):
    """A client that records writes and performs none."""

    def __init__(self):
        self.writes = []

    def init(self, *args):
        self.writes.append(("init",) + args)

    def set(self, *args):
        self.writes.append(("set",) + args)

    def delete(self, *args):
        self.writes.append(("delete",) + args)


@contextlib.contextmanager
def uncreatable_type():
    """Register a writer type the device will not create, for the duration.

    No real type is uncreatable any more, so the rails that protect
    unrebuildable objects need one to exist in order to be tested at all.
    """
    logs.WRITER_TYPES["frozen"] = logs.WriterType(
        key="frozen",
        sap_type="FrozenWriter",
        creatable=False,
        init_params=None,
        endpoint_property="RemoteEndpointUri",
        endpoint_template=lambda d: ("tcp://%s:1515/" % d["ip"]) if d.get("ip") else None,
        notes="Synthetic, for tests.",
    )
    try:
        yield
    finally:
        del logs.WRITER_TYPES["frozen"]


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
    """The rail, tested against a synthetic type.

    It used to be tested against tcp_client, which was believed uncreatable.
    The device disproved that, so no REAL type triggers this today - but the
    rail is the reason a purge or a replace cannot destroy something
    unrebuildable, and it should not go untested just because the one known
    example turned out to be rebuildable after all. A firmware that refuses a
    type, or a type added later, lands straight back on it.
    """

    def setUp(self):
        self.actual = present_actual(
            {"Name": "w", "RemoteEndpointUri": "tcp://192.0.2.21:1515/"})

    def test_replace_is_refused_for_an_uncreatable_type(self):
        with uncreatable_type():
            actions = logs.plan(desired(type="frozen", ip="192.0.2.99"),
                                self.actual, on_immutable_change="replace")
        self.assertEqual([a.kind for a in actions], ["blocked_immutable"])
        self.assertIn("cannot be recreated", actions[0].summary)

    def test_no_delete_is_emitted(self):
        client = Recorder()
        with uncreatable_type():
            d = desired(type="frozen", ip="192.0.2.99")
            logs.apply_plan(client, d, logs.plan(
                d, self.actual, on_immutable_change="replace"))
        self.assertEqual(client.writes, [])

    def test_explicit_absent_is_still_allowed_but_flagged_one_way(self):
        # Deleting deliberately is fine - it is repairing drift by deletion
        # that is refused.
        with uncreatable_type():
            actions = logs.plan(desired(type="frozen", ip=None, state="absent"),
                                self.actual)
        self.assertEqual([a.kind for a in actions], ["writer_delete"])
        self.assertIn("cannot be recreated", actions[0].summary)
        self.assertTrue(actions[0].destructive)

    def test_a_purge_skips_it_rather_than_deleting_it(self):
        # A purge is the one place a writer is removed without anyone naming
        # it, so an unrebuildable one is skipped and reported.
        with uncreatable_type():
            self.assertFalse(logs.writer_type("frozen").replaceable)


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


class TestRotation(unittest.TestCase):
    """Rotation is under Logs#0, so this module owns it.

    Device-scoped rather than writer-scoped, which is why it is planned
    separately and only when asked for.
    """

    ACTUAL = {
        "Logs#0.LogRotator#0.RotateRule#0": {"MaxFileSize": "100", "MaxCount": "10"},
        "Logs#0.LogRotator#0": {"MinutesBetweenSearch": "15"},
        "Logs#0": {"SkipCleanLogs": "False", "CheckRotationAfterMaxWrites": "250"},
    }

    def test_all_five_knobs_are_known(self):
        self.assertEqual(sorted(logs.ROTATION_PROPERTIES),
                         ["check_rotation_after_max_writes", "max_count",
                          "max_file_size", "minutes_between_search",
                          "skip_clean_logs"])

    def test_only_differences_are_planned(self):
        actions = logs.plan_rotation({"max_file_size": 1, "max_count": 10},
                                     self.ACTUAL)
        self.assertEqual([a.kind for a in actions], ["rotation_set"])
        self.assertEqual(actions[0].detail["property"], "MaxFileSize")
        self.assertEqual(actions[0].detail["to"], "1")

    def test_converged_plans_nothing(self):
        self.assertEqual(logs.plan_rotation(
            {"max_file_size": 100, "skip_clean_logs": False}, self.ACTUAL), [])

    def test_omitted_rotation_touches_nothing(self):
        self.assertEqual(logs.plan_rotation({}, self.ACTUAL), [])
        self.assertEqual(logs.plan_rotation(None, self.ACTUAL), [])

    def test_property_absent_on_the_device_is_blocked_not_written(self):
        actions = logs.plan_rotation({"max_file_size": 1},
                                     {"Logs#0.LogRotator#0.RotateRule#0": {}})
        self.assertEqual([a.kind for a in actions], ["blocked_absent_property"])

    def test_unknown_setting_is_rejected(self):
        self.assertTrue(logs.validate_rotation({"max_age_days": 7}))

    def test_non_numeric_is_rejected(self):
        self.assertTrue(logs.validate_rotation({"max_count": "lots"}))

    def test_intended_writes_feed_the_startup_script_check(self):
        self.assertEqual(logs.rotation_writes({"max_count": 3}),
                         [("Logs#0.LogRotator#0.RotateRule#0", "MaxCount", "3")])


class TestMessageLogSettingsCoverage(unittest.TestCase):
    """Every RW property on a real MessageLogSettings object is manageable.

    Established by `rfs` against a Core PRO and diffed against the module's
    list, which is how SkipWebClientSapMessages was found - it had been missing
    since the first version and nothing would have surfaced it, because an
    unmanaged property is simply never written.
    """

    #: Measured 2026-09-12. FriendlyName/SapObjectType/SubVersion are RO.
    DEVICE_RW = (
        "AccessViolations", "AuditGet", "AuditSet", "LoginFailures",
        "LoginSuccesses", "Lwcp", "Lwrp", "SapV2External", "SapV2Internal",
        "SkipWebClientSapMessages",
    )

    def test_no_writable_property_is_unmanaged(self):
        self.assertEqual(sorted(set(self.DEVICE_RW) - set(logs.MESSAGE_LOG_PROPERTIES)),
                         [])

    def test_nothing_managed_that_the_device_does_not_expose(self):
        self.assertEqual(sorted(set(logs.MESSAGE_LOG_PROPERTIES) - set(self.DEVICE_RW)),
                         [])

    def test_the_new_boolean_validates_like_the_others(self):
        self.assertEqual(
            logs.validate_message_log_settings({"SkipWebClientSapMessages": True}), [])
        self.assertTrue(logs.validate_message_log_settings({"SkipWebClient": True}))


class TestLogsRuntimeBoundary(unittest.TestCase):
    """Logs#0 is declarative, but not entirely."""

    def test_ready_is_refused(self):
        # RW on the device, but it states whether logging is up rather than
        # configuring it - and the plausible reading of a write is "stop".
        guard = subtrees.SubtreeGuard()
        with self.assertRaises(sapv2.SapV2GuardError):
            guard.assert_writable("Logs#0", verb="set", properties=["Ready"])

    def test_rotator_file_stats_are_refused(self):
        guard = subtrees.SubtreeGuard()
        for prop in ("LogSize", "LastChanged", "RootFileName"):
            with self.assertRaises(sapv2.SapV2GuardError):
                guard.assert_writable("Logs#0.LogRotator#0.LogFile#[user/a.log]",
                                      verb="set", properties=[prop])

    def test_rotation_policy_is_still_writable(self):
        # RotateRule#0 is the wildcard rule (RootFileName="*") and IS config.
        subtrees.SubtreeGuard().assert_writable(
            "Logs#0.LogRotator#0.RotateRule#0", verb="set",
            properties=["MaxFileSize", "MaxCount"])
