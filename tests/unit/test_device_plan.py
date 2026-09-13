# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for reconciling a whole device: several writers in one session.

The safety property under test is the phase boundary. ``pfc_logs`` takes the
device's whole writer list so that standing up a replacement and retiring the
one it replaces is a single intent, and that is only safe if the delete happens
strictly after the create has been read back. On this protocol a failed create
is reported as silence, so an ordering bug here would delete a working writer to
make room for one that never arrived.
"""
import re
import unittest

from loader import logs


NEW = "alloy_site1"
OLD = "legacy_tcp"
VIP = "192.0.2.10"
URI = "udp://192.0.2.10:514/"

SUB = {"typeid": 1001, "subscription": "sub Devices#0 Connected $MAX_DEPTH=-1",
       "severity": "Warning", "customname": "device-connected"}

NEW_PATH = "Logs#0.UdpSysLogWriter#" + NEW
OLD_PATH = "Logs#0.TcpClientWriter#" + OLD


def writer(name, **overrides):
    entry = {"name": name, "type": "udp_syslog", "ip": VIP, "state": "present",
             "subscriptions": [], "message_log_settings": {},
             "subscriptions_purge": False, "properties": {}, "port": None}
    entry.update(overrides)
    return entry


class FakeDevice(object):
    """A device that answers reads from a dict and records every write.

    ``applied`` controls whether writes are reflected in later reads, which is
    how a write that the device silently ignores - the failure mode this whole
    design exists for - gets simulated.
    """

    def __init__(self, reads=None, applied=True):
        self.reads = dict(reads or {})
        self.writes = []
        self.applied = applied

    def init(self, type_path, params):
        self.writes.append(("init", type_path, list(params)))
        if not self.applied:
            return
        params = dict(params)
        if type_path.endswith(".LogSubscription"):
            path = "%s#%s" % (type_path, params["typeid"])
            self.reads[path] = {
                "Subscription": params["subscription"],
                "Severity": params.get("severity", "Informational"),
                "CustomName": params.get("customname", ""),
            }
        else:
            path = "%s#%s" % (type_path, params["name"])
            self.reads[path] = {"Name": params["name"],
                                "RemoteEndpointUri": "udp://%s:514/" % params["ip"]}
            self.reads[path + ".MessageLogSettings#0"] = {}

    def set(self, path, properties):
        items = list(properties.items()) if hasattr(properties, "items") else list(properties)
        self.writes.append(("set", path, items))
        if self.applied:
            self.reads.setdefault(path, {}).update(dict(items))

    def delete(self, path):
        self.writes.append(("del", path, None))
        if self.applied:
            for key in [k for k in self.reads if k == path or k.startswith(path + ".")]:
                del self.reads[key]

    def get(self, path, prop=None):
        return self.reads.get(path, {})

    def children(self, path):
        out = {}
        for key, value in self.reads.items():
            if not key.startswith(path + "."):
                continue
            rest = key[len(path) + 1:]
            # A bracket-quoted name contains dots that are NOT separators -
            # every log_file name does. Counting them naively hides exactly the
            # case worth testing, so mask the brackets first.
            if "." not in re.sub(r"\[[^\]]*\]", "", rest):
                out[key] = value
        return out

    def tree(self, path, depth=-1):
        """The object and every descendant, as the real client returns them.

        The real one needs two commands because $MAX_DEPTH excludes the object
        named in the request; that detail is the client's problem, and by the
        time a caller sees the result the object is in it.
        """
        self.reads_issued = getattr(self, "reads_issued", 0) + 1
        return dict((k, v) for k, v in self.reads.items()
                    if k == path or k.startswith(path + "."))

    def verbs(self):
        return [(verb, path) for verb, path, _payload in self.writes]


def converged_new():
    return {NEW_PATH: {"Name": NEW, "RemoteEndpointUri": URI},
            NEW_PATH + ".MessageLogSettings#0": {}}


def existing_old():
    return {OLD_PATH: {"Name": OLD, "RemoteEndpointUri": "tcp://192.0.2.99:1515/"},
            OLD_PATH + ".MessageLogSettings#0": {}}


class TestInheritance(unittest.TestCase):
    """Task-level values fill entries that stay silent, and only those."""

    DEFAULTS = {"state": "present", "subscriptions": [SUB],
                "subscriptions_purge": True,
                "message_log_settings": {"Lwrp": "Both"}}

    def resolve(self, *entries):
        return logs.resolve_writers(list(entries), self.DEFAULTS)

    def test_omitted_keys_inherit(self):
        resolved = self.resolve({"name": NEW, "ip": VIP})[0]
        self.assertEqual(resolved["subscriptions"], [SUB])
        self.assertEqual(resolved["message_log_settings"], {"Lwrp": "Both"})
        self.assertTrue(resolved["subscriptions_purge"])
        self.assertEqual(resolved["state"], "present")

    def test_an_explicit_empty_list_is_not_an_omission(self):
        # The whole reason these suboptions carry no default: `subscriptions: []`
        # on one writer has to mean "none", not "inherit the device catalogue".
        resolved = self.resolve({"name": NEW, "ip": VIP, "subscriptions": []})[0]
        self.assertEqual(resolved["subscriptions"], [])

    def test_an_explicit_empty_dict_is_not_an_omission(self):
        resolved = self.resolve({"name": NEW, "ip": VIP,
                                 "message_log_settings": {}})[0]
        self.assertEqual(resolved["message_log_settings"], {})

    def test_explicit_false_is_not_an_omission(self):
        resolved = self.resolve({"name": NEW, "ip": VIP,
                                 "subscriptions_purge": False})[0]
        self.assertFalse(resolved["subscriptions_purge"])

    def test_per_writer_state_overrides_the_task_default(self):
        resolved = self.resolve({"name": NEW, "ip": VIP},
                                {"name": OLD, "state": "absent"})
        self.assertEqual([w["state"] for w in resolved], ["present", "absent"])

    def test_type_defaults_without_a_task_level_equivalent(self):
        self.assertEqual(self.resolve({"name": NEW})[0]["type"], "udp_syslog")


class TestPlanOrdering(unittest.TestCase):
    def setUp(self):
        self.device = FakeDevice(existing_old())
        self.plan = logs.plan_device(self.device, [
            writer(OLD, type="tcp_client", ip=None, state="absent"),
            writer(NEW, subscriptions=[SUB]),
        ])

    def test_creates_are_ordered_before_deletes_regardless_of_input_order(self):
        # The delete was listed first. The plan must still put it last.
        kinds = [a.kind for a in self.plan.actions]
        self.assertEqual(kinds, ["writer_create", "subscription_create",
                                 "writer_delete"])

    def test_every_action_says_which_writer_it_belongs_to(self):
        for action in self.plan.actions:
            self.assertIn(action.to_dict()["writer"], (NEW, OLD))
            self.assertEqual(action.to_dict()["scope"], "writer")

    def test_rotation_actions_are_device_scoped(self):
        device = FakeDevice(dict(converged_new(), **{
            "Logs#0": {"SkipCleanLogs": "False", "CheckRotationAfterMaxWrites": "250"},
            "Logs#0.LogRotator#0": {"MinutesBetweenSearch": "15"},
            "Logs#0.LogRotator#0.RotateRule#0": {"MaxFileSize": "1", "MaxCount": "3"},
        }))
        plan = logs.plan_device(device, [writer(NEW)], {"max_count": 10})
        self.assertEqual([a.to_dict()["scope"] for a in plan.actions], ["device"])

    def test_rotation_runs_between_the_creates_and_the_deletes(self):
        device = FakeDevice(dict(existing_old(), **{
            "Logs#0": {}, "Logs#0.LogRotator#0": {},
            "Logs#0.LogRotator#0.RotateRule#0": {"MaxCount": "3"},
        }))
        plan = logs.plan_device(
            device,
            [writer(OLD, type="tcp_client", ip=None, state="absent"), writer(NEW)],
            {"max_count": 10})
        self.assertEqual([a.kind for a in plan.actions],
                         ["writer_create", "rotation_set", "writer_delete"])


class TestDeletesAreGatedOnTheCreates(unittest.TestCase):
    """The reason writers is a list rather than a loop over a single-writer task."""

    def _cutover(self, applied):
        device = FakeDevice(existing_old(), applied=applied)
        plan = logs.plan_device(device, [
            writer(NEW, subscriptions=[SUB]),
            writer(OLD, type="tcp_client", ip=None, state="absent"),
        ])
        return device, plan, plan.apply(device)

    def test_a_clean_run_creates_then_deletes(self):
        device, _plan, failures = self._cutover(applied=True)
        self.assertEqual(failures, [])
        self.assertEqual(device.verbs(), [
            ("init", "Logs#0.UdpSysLogWriter"),
            ("init", NEW_PATH + ".LogSubscription"),
            ("del", OLD_PATH),
        ])

    def test_a_create_the_device_ignored_stops_the_delete(self):
        # The device accepted every write and did nothing. Read-back is the
        # only thing that notices, and the old writer must survive it.
        device, _plan, failures = self._cutover(applied=False)
        self.assertTrue(failures)
        self.assertNotIn(("del", OLD_PATH), device.verbs())
        self.assertIn(OLD_PATH, device.reads)

    def test_the_failure_names_what_did_not_take(self):
        _device, _plan, failures = self._cutover(applied=False)
        self.assertIn("does not exist after create", failures[0])

    def test_a_writer_that_failed_is_not_reported_verified(self):
        _device, plan, _failures = self._cutover(applied=False)
        self.assertFalse(plan.creating[0].verified)

    def test_deletes_still_run_when_there_is_nothing_to_gate_on(self):
        device = FakeDevice(existing_old())
        plan = logs.plan_device(
            device, [writer(OLD, type="tcp_client", ip=None, state="absent")])
        self.assertEqual(plan.apply(device), [])
        self.assertEqual(device.verbs(), [("del", OLD_PATH)])


class TestVerification(unittest.TestCase):
    def test_all_failures_are_collected_not_just_the_first(self):
        device = FakeDevice(applied=False)
        plan = logs.plan_device(device, [writer(NEW), writer("alloy_site2")])
        self.assertEqual(len(plan.apply(device)), 2)

    def test_a_converged_writer_is_not_claimed_as_verified(self):
        # Nothing was written to it, so nothing was read back. Reporting it as
        # verified would overstate what the run actually established.
        device = FakeDevice(converged_new())
        plan = logs.plan_device(device, [writer(NEW), writer("alloy_site2")])
        self.assertEqual(plan.apply(device), [])
        by_name = dict((w.name, w) for w in plan.writers)
        self.assertFalse(by_name[NEW].changed)
        self.assertFalse(by_name[NEW].verified)
        self.assertTrue(by_name["alloy_site2"].verified)

    def test_rotation_failure_also_stops_the_deletes(self):
        device = FakeDevice(dict(existing_old(), **{
            "Logs#0": {}, "Logs#0.LogRotator#0": {},
            "Logs#0.LogRotator#0.RotateRule#0": {"MaxCount": "3"},
        }), applied=False)
        plan = logs.plan_device(
            device, [writer(OLD, type="tcp_client", ip=None, state="absent")],
            {"max_count": 10})
        self.assertTrue(plan.apply(device))
        self.assertNotIn(("del", OLD_PATH), device.verbs())


class TestIdentity(unittest.TestCase):
    def test_the_same_type_and_name_twice_is_rejected(self):
        problems = logs.validate_writers([writer(NEW), writer(NEW)])
        self.assertTrue(any("appears twice" in p for p in problems))

    def test_one_name_under_two_types_is_allowed(self):
        # They are different objects at different paths.
        self.assertEqual(logs.validate_writers(
            [writer(NEW), writer(NEW, type="log_file", ip=None)]), [])

    def test_problems_name_the_entry_that_has_them(self):
        problems = logs.validate_writers([writer(NEW), writer("broken", ip=None)])
        self.assertEqual(len(problems), 1)
        self.assertIn("writers[1] (broken)", problems[0])

    def test_per_writer_subscriptions_are_validated(self):
        problems = logs.validate_writers(
            [writer(NEW, subscriptions=[SUB, dict(SUB)])])
        self.assertTrue(any("duplicate typeid" in p for p in problems))

    def test_per_writer_settings_are_validated(self):
        problems = logs.validate_writers(
            [writer(NEW, message_log_settings={"Lwrp": "In"})])
        self.assertTrue(any("silently ignored" in p for p in problems))


class TestDiff(unittest.TestCase):
    def test_one_diff_per_changing_object(self):
        device = FakeDevice(existing_old())
        plan = logs.plan_device(device, [
            writer(NEW),
            writer(OLD, type="tcp_client", ip=None, state="absent"),
        ])
        headers = [d["before_header"] for d in plan.diff()]
        self.assertEqual(headers, [NEW_PATH, OLD_PATH])

    def test_converged_writers_contribute_no_diff(self):
        device = FakeDevice(converged_new())
        plan = logs.plan_device(device, [writer(NEW)])
        self.assertEqual(plan.diff(), [])

    def test_rotation_gets_its_own_diff_entry(self):
        # Without this a rotation-only run reports changed against an empty
        # diff, which reads like a bug in the module.
        device = FakeDevice(dict(converged_new(), **{
            "Logs#0": {}, "Logs#0.LogRotator#0": {},
            "Logs#0.LogRotator#0.RotateRule#0": {"MaxCount": "3"},
        }))
        plan = logs.plan_device(device, [writer(NEW)], {"max_count": 10})
        diffs = plan.diff()
        self.assertEqual(len(diffs), 1)
        self.assertIn("max_count", diffs[0]["before"])
        self.assertIn("3", diffs[0]["before"])
        self.assertIn("10", diffs[0]["after"])

    def test_rotation_settings_not_asked_for_are_unchanged_in_the_after(self):
        device = FakeDevice({
            "Logs#0": {"SkipCleanLogs": "False", "CheckRotationAfterMaxWrites": "250"},
            "Logs#0.LogRotator#0": {"MinutesBetweenSearch": "15"},
            "Logs#0.LogRotator#0.RotateRule#0": {"MaxFileSize": "1", "MaxCount": "3"},
        })
        plan = logs.plan_device(device, [], {"max_count": 10})
        diff = plan.diff()[0]

        def value(block, key):
            for line in block.splitlines():
                if line.startswith(key):
                    return line.split()[-1]
            raise AssertionError("%s missing from %r" % (key, block))

        # Untouched settings stay put on both sides. Dropping them from the
        # "after" would advertise a write the module is not going to make.
        for key in ("minutes_between_search", "max_file_size", "skip_clean_logs",
                    "check_rotation_after_max_writes"):
            self.assertEqual(value(diff["before"], key), value(diff["after"], key))
        self.assertEqual(value(diff["before"], "max_count"), "3")
        self.assertEqual(value(diff["after"], "max_count"), "10")


class TestCheckMode(unittest.TestCase):
    def test_planning_writes_nothing(self):
        device = FakeDevice(existing_old())
        plan = logs.plan_device(device, [
            writer(NEW, subscriptions=[SUB]),
            writer(OLD, type="tcp_client", ip=None, state="absent"),
        ])
        self.assertTrue(plan.changed)
        self.assertEqual(device.writes, [])

    def test_a_blocked_action_is_visible_before_anything_is_applied(self):
        device = FakeDevice({NEW_PATH: {"Name": NEW,
                                        "RemoteEndpointUri": "udp://192.0.2.99:514/"},
                             NEW_PATH + ".MessageLogSettings#0": {}})
        plan = logs.plan_device(device, [writer(NEW)])
        self.assertEqual([a.kind for a in plan.blocked], ["blocked_immutable"])


class TestBooleanNormalisation(unittest.TestCase):
    """A stringified bool must not read as permanent drift."""

    def test_string_true_matches_the_devices_True(self):
        self.assertEqual(logs.normalise_scalar("true"), "True")
        self.assertEqual(logs.normalise_scalar("TRUE"), "True")
        self.assertEqual(logs.normalise_scalar(True), "True")

    def test_a_stringified_bool_is_not_drift(self):
        device = FakeDevice(dict(converged_new(), **{
            NEW_PATH + ".MessageLogSettings#0": {"AuditSet": "True"}}))
        plan = logs.plan_device(
            device, [writer(NEW, message_log_settings={"AuditSet": "true"})])
        self.assertEqual(plan.actions, [])


if __name__ == "__main__":
    unittest.main()


class TestUnmanagedWriters(unittest.TestCase):
    """Writers the task did not name are ignored unless asked about.

    The scoping to named TYPES is the safety property. A Core PRO ships with
    around a dozen LogFileWriters of its own, and a purge that swept them up
    would be silent, immediate and irreversible.
    """

    def device(self):
        reads = {
            "Logs#0.UdpSysLogWriter#alloy_site1": {"Name": "alloy_site1",
                                                   "RemoteEndpointUri": URI},
            "Logs#0.UdpSysLogWriter#alloy_site1.MessageLogSettings#0": {},
            "Logs#0.UdpSysLogWriter#stale_one": {"Name": "stale_one",
                                                 "RemoteEndpointUri": URI},
            "Logs#0.UdpSysLogWriter#stale_one.MessageLogSettings#0": {},
            "Logs#0.TcpClientWriter#legacy": {"Name": "legacy"},
            "Logs#0.TcpClientWriter#legacy.MessageLogSettings#0": {},
            "Logs#0.LogRotator#0": {"MinutesBetweenSearch": "15"},
        }
        for name in ("Connected_Msg.log", "SAPv2Log.log", "Scenes.log"):
            reads["Logs#0.LogFileWriter#[%s]" % name] = {"Name": name}
            reads["Logs#0.LogFileWriter#[%s].MessageLogSettings#0" % name] = {}
        return FakeDevice(reads)

    def test_list_writers_parses_bracket_quoted_names(self):
        found = logs.list_writers(self.device())
        self.assertIn(("log_file", "SAPv2Log.log"), found)
        self.assertIn(("udp_syslog", "alloy_site1"), found)
        self.assertIn(("tcp_client", "legacy"), found)
        # LogRotator#0 is not a writer.
        self.assertFalse([k for k in found if "rotator" in k[0].lower()])

    def test_ignore_is_the_default_and_looks_at_nothing(self):
        device = self.device()
        plan = logs.plan_device(device, [writer("alloy_site1")])
        self.assertEqual(plan.unmanaged, [])
        self.assertEqual(plan.actions, [])

    def test_report_lists_every_type_not_just_the_managed_one(self):
        # Reporting is non-destructive, so the honest answer to "what else is
        # on this device" includes the log files and the legacy TCP writer -
        # the latter being exactly what an estate migration wants surfaced.
        device = self.device()
        plan = logs.plan_device(device, [writer("alloy_site1")],
                                unmanaged="report")
        self.assertEqual(
            sorted(u["name"] for u in plan.unmanaged),
            ["Connected_Msg.log", "SAPv2Log.log", "Scenes.log", "legacy",
             "stale_one"])
        # Reporting is not a change.
        self.assertEqual(plan.actions, [])
        self.assertFalse(plan.changed)

    def test_report_marks_what_a_purge_would_actually_touch(self):
        # Listing more than a purge would delete is only safe if the difference
        # is visible, or someone reads five entries and expects five deletions.
        device = self.device()
        plan = logs.plan_device(device, [writer("alloy_site1")],
                                unmanaged="report")
        by_name = dict((u["name"], u) for u in plan.unmanaged)
        self.assertTrue(by_name["stale_one"]["in_purge_scope"])
        self.assertEqual(by_name["stale_one"]["action"], "reported")
        for name in ("SAPv2Log.log", "legacy"):
            self.assertFalse(by_name[name]["in_purge_scope"])
            self.assertEqual(by_name[name]["action"], "out_of_scope")

    def test_purge_deletes_only_the_managed_type(self):
        # The whole point: a task naming a udp_syslog writer must not delete
        # the device's own log files or the legacy TCP writer.
        device = self.device()
        plan = logs.plan_device(device, [writer("alloy_site1")],
                                unmanaged="purge")
        deleted = [a.to_dict()["writer"] for a in plan.actions
                   if a.kind == "writer_delete"]
        self.assertEqual(deleted, ["stale_one"])
        self.assertEqual(
            sorted(u["name"] for u in plan.unmanaged if u["action"] == "delete"),
            ["stale_one"])

    def test_one_read_regardless_of_how_many_writers_exist(self):
        device = self.device()
        logs.plan_device(device, [writer("alloy_site1")], unmanaged="report")
        # The listing is a single deep read, not one per writer.
        self.assertEqual(getattr(device, "reads_issued", 0), 2)

    def test_a_tcp_client_is_purgeable_now_that_it_can_be_recreated(self):
        # It used to be skipped, on the belief that a TcpClientWriter could not
        # be created. The device disproved that, so the rail no longer applies
        # to this type - it is still tested, against a synthetic one, in
        # test_writer_types.
        device = self.device()
        plan = logs.plan_device(
            device,
            [writer("alloy_site1"), writer("keep_me", type="tcp_client",
                                           ip="192.0.2.70")],
            unmanaged="purge")
        by_name = dict((u["name"], u) for u in plan.unmanaged)
        self.assertEqual(by_name["stale_one"]["action"], "delete")
        self.assertEqual(by_name["legacy"]["action"], "delete")

    def test_the_endpoint_is_recorded_before_anything_is_removed(self):
        # A purge is reversible only for someone who knows where the writer
        # pointed, and afterwards there is nothing left to read it from.
        device = self.device()
        plan = logs.plan_device(device, [writer("alloy_site1")],
                                unmanaged="report")
        by_name = dict((u["name"], u) for u in plan.unmanaged)
        self.assertEqual(by_name["stale_one"]["endpoint"], URI)
        # A log_file writer has no endpoint property, so it carries no endpoint
        # rather than an empty one.
        self.assertNotIn("endpoint", by_name["SAPv2Log.log"])

    def test_purge_deletes_run_after_the_creates_verify(self):
        device = self.device()
        plan = logs.plan_device(
            device,
            [writer("alloy_site1"), writer("brand_new", ip="192.0.2.50")],
            unmanaged="purge")
        kinds = [a.kind for a in plan.actions]
        self.assertLess(kinds.index("writer_create"), kinds.index("writer_delete"))
        self.assertEqual(plan.apply(device), [])
        verbs = device.verbs()
        self.assertLess(verbs.index(("init", "Logs#0.UdpSysLogWriter")),
                        verbs.index(("del", "Logs#0.UdpSysLogWriter#stale_one")))

    def test_a_failed_create_stops_the_purge(self):
        device = FakeDevice(self.device().reads, applied=False)
        plan = logs.plan_device(
            device,
            [writer("alloy_site1"), writer("brand_new", ip="192.0.2.50")],
            unmanaged="purge")
        self.assertTrue(plan.apply(device))
        self.assertNotIn(("del", "Logs#0.UdpSysLogWriter#stale_one"), device.verbs())

    def test_an_empty_writer_list_purges_nothing(self):
        # No named types means nothing is in scope, so an accidentally empty
        # list cannot empty a device.
        device = self.device()
        plan = logs.plan_device(device, [], unmanaged="purge")
        self.assertEqual(plan.unmanaged, [])
        self.assertEqual(plan.actions, [])

    def test_a_purge_delete_is_marked_destructive(self):
        device = self.device()
        plan = logs.plan_device(device, [writer("alloy_site1")],
                                unmanaged="purge")
        self.assertTrue(all(a.destructive for a in plan.actions
                            if a.kind == "writer_delete"))
