# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Logs subtree planner, applier and read-back verification."""
import unittest

from loader import logs, sapv2


WRITER = "alloy_site1"
VIP = "192.0.2.10"
URI = "udp://192.0.2.10:514/"

SUB_1001 = {"typeid": 1001,
            "subscription": "sub Devices#0 Connected $MAX_DEPTH=-1",
            "severity": "Warning", "customname": "device-connected"}
SUB_7002 = {"typeid": 7002,
            "subscription": "sub Devices#0 GAIN $MAX_DEPTH=-1",
            "severity": "Informational", "customname": "device-gain"}


def desired(**overrides):
    base = {"name": WRITER, "ip": VIP, "state": "present",
            "subscriptions": [SUB_1001, SUB_7002],
            "message_log_settings": {"Lwrp": "Both"}}
    base.update(overrides)
    return base


def actual_absent():
    return {"exists": False, "properties": {}, "subscriptions": {},
            "message_log_settings": {}}


def actual_converged():
    return {
        "exists": True,
        "properties": {"Name": WRITER, "RemoteEndpointUri": URI},
        "subscriptions": {
            "1001": {"Subscription": SUB_1001["subscription"],
                     "Severity": "Warning", "CustomName": "device-connected"},
            "7002": {"Subscription": SUB_7002["subscription"],
                     "Severity": "Informational", "CustomName": "device-gain"},
        },
        "message_log_settings": {"Lwrp": "Both"},
    }


class FakeClient(object):
    """Records writes and serves canned reads, so the planner needs no device."""

    def __init__(self, reads=None):
        self.reads = reads or {}
        self.writes = []
        self.guard_calls = []

    # write verbs
    def init(self, type_path, params):
        self.writes.append(("init", type_path, list(params)))

    def set(self, path, properties):
        items = list(properties.items()) if hasattr(properties, "items") else list(properties)
        self.writes.append(("set", path, items))

    def delete(self, path):
        self.writes.append(("del", path, None))

    # read verbs
    def get(self, path, prop=None):
        return self.reads.get(path, {})

    def children(self, path):
        return dict((k, v) for k, v in self.reads.items()
                    if k.startswith(path + ".") and "." not in k[len(path) + 1:])


class TestPlanCreate(unittest.TestCase):
    def test_from_nothing(self):
        actions = logs.plan(desired(), actual_absent())
        kinds = [a.kind for a in actions]
        self.assertEqual(kinds, ["writer_create", "subscription_create",
                                 "subscription_create", "settings_set"])

    def test_create_is_not_destructive(self):
        self.assertFalse(any(a.destructive
                             for a in logs.plan(desired(), actual_absent())))

    def test_settings_are_planned_after_a_create_even_if_they_look_current(self):
        # A fresh writer's MessageLogSettings default to off, so an "actual"
        # that appears to match must not suppress the write.
        actual = actual_absent()
        actual["message_log_settings"] = {"Lwrp": "Both"}
        kinds = [a.kind for a in logs.plan(desired(), actual)]
        self.assertIn("settings_set", kinds)


class TestPlanConverged(unittest.TestCase):
    def test_no_actions_when_already_correct(self):
        self.assertEqual(logs.plan(desired(), actual_converged()), [])

    def test_whitespace_differences_are_not_drift(self):
        actual = actual_converged()
        actual["subscriptions"]["7002"]["Subscription"] = \
            "sub  Devices#0   GAIN $MAX_DEPTH=-1"
        self.assertEqual(logs.plan(desired(), actual), [])

    def test_case_differences_ARE_drift(self):
        # The device stores expressions verbatim, so case-folding would mask a
        # real difference and then fail verification instead.
        actual = actual_converged()
        actual["subscriptions"]["7002"]["Subscription"] = \
            "SUB Devices#0 GAIN $MAX_DEPTH=-1"
        kinds = [a.kind for a in logs.plan(desired(), actual)]
        self.assertEqual(kinds, ["subscription_update"])


class TestSubscriptionImmutability(unittest.TestCase):
    def test_expression_change_updates_in_place(self):
        actual = actual_converged()
        actual["subscriptions"]["7002"]["Subscription"] = "sub Devices#0 OTHER"
        actions = logs.plan(desired(), actual)
        self.assertEqual([a.kind for a in actions], ["subscription_update"])

    def test_severity_change_forces_a_replace(self):
        # Severity is read-only after creation.
        actual = actual_converged()
        actual["subscriptions"]["1001"]["Severity"] = "Informational"
        actions = logs.plan(desired(), actual)
        self.assertEqual([a.kind for a in actions], ["subscription_replace"])

    def test_customname_change_forces_a_replace(self):
        actual = actual_converged()
        actual["subscriptions"]["1001"]["CustomName"] = "something-else"
        actions = logs.plan(desired(), actual)
        self.assertEqual([a.kind for a in actions], ["subscription_replace"])

    def test_replacing_a_subscription_is_not_flagged_destructive(self):
        # A subscription carries no state, so recreating one loses nothing.
        # This asymmetry with writers is deliberate.
        actual = actual_converged()
        actual["subscriptions"]["1001"]["Severity"] = "Informational"
        self.assertFalse(logs.plan(desired(), actual)[0].destructive)


class TestWriterImmutability(unittest.TestCase):
    def test_endpoint_change_is_blocked_by_default(self):
        actual = actual_converged()
        actual["properties"]["RemoteEndpointUri"] = "udp://192.0.2.99:514/"
        actions = logs.plan(desired(), actual)
        self.assertEqual([a.kind for a in actions], ["blocked_immutable"])

    def test_blocked_plan_stops_there(self):
        # Nothing else should be planned against a writer we are refusing to
        # touch.
        actual = actual_converged()
        actual["properties"]["RemoteEndpointUri"] = "udp://192.0.2.99:514/"
        actual["subscriptions"] = {}
        self.assertEqual(len(logs.plan(desired(), actual)), 1)

    def test_replace_when_explicitly_allowed(self):
        actual = actual_converged()
        actual["properties"]["RemoteEndpointUri"] = "udp://192.0.2.99:514/"
        actions = logs.plan(desired(), actual, on_immutable_change="replace")
        self.assertEqual(actions[0].kind, "writer_replace")
        self.assertTrue(actions[0].destructive)

    def test_replace_recreates_every_subscription(self):
        # The old object and its children are gone, so all of them must be
        # planned again even though they looked present.
        actual = actual_converged()
        actual["properties"]["RemoteEndpointUri"] = "udp://192.0.2.99:514/"
        kinds = [a.kind for a in
                 logs.plan(desired(), actual, on_immutable_change="replace")]
        self.assertEqual(kinds, ["writer_replace", "subscription_create",
                                 "subscription_create", "settings_set"])


class TestPurge(unittest.TestCase):
    def test_extra_subscriptions_are_left_alone_by_default(self):
        actual = actual_converged()
        actual["subscriptions"]["6001"] = {"Subscription": "sub MemorySlots#0 SlotValue",
                                           "Severity": "Informational",
                                           "CustomName": "memoryslot-value"}
        self.assertEqual(logs.plan(desired(), actual), [])

    def test_purge_removes_them_and_marks_it_destructive(self):
        actual = actual_converged()
        actual["subscriptions"]["6001"] = {"Subscription": "sub MemorySlots#0 SlotValue",
                                           "Severity": "Informational",
                                           "CustomName": "memoryslot-value"}
        actions = logs.plan(desired(), actual, purge_subscriptions=True)
        self.assertEqual([a.kind for a in actions], ["subscription_delete"])
        self.assertTrue(actions[0].destructive)


class TestStateAbsent(unittest.TestCase):
    def test_delete_when_present(self):
        actions = logs.plan(desired(state="absent"), actual_converged())
        self.assertEqual([a.kind for a in actions], ["writer_delete"])
        self.assertTrue(actions[0].destructive)

    def test_noop_when_already_gone(self):
        self.assertEqual(logs.plan(desired(state="absent"), actual_absent()), [])


class TestApply(unittest.TestCase):
    def test_create_emits_the_proven_init_shapes(self):
        client = FakeClient()
        actions = logs.plan(desired(), actual_absent())
        logs.apply_plan(client, desired(), actions)

        verbs = [w[0] for w in client.writes]
        self.assertEqual(verbs, ["init", "init", "init", "set"])

        # Writer: created against the TYPE path with name= and a bare ip=.
        self.assertEqual(client.writes[0][1], "Logs#0.UdpSysLogWriter")
        self.assertEqual(client.writes[0][2], [("name", WRITER), ("ip", VIP)])

        # Subscription: created under the writer INSTANCE, with severity and
        # customname supplied at init because they are read-only afterwards.
        self.assertEqual(client.writes[1][1],
                         "Logs#0.UdpSysLogWriter#alloy_site1.LogSubscription")
        self.assertEqual(dict(client.writes[1][2])["typeid"], 1001)
        self.assertEqual(dict(client.writes[1][2])["severity"], "Warning")
        self.assertEqual(dict(client.writes[1][2])["customname"], "device-connected")

    def test_replace_deletes_before_recreating(self):
        client = FakeClient()
        actual = actual_converged()
        actual["subscriptions"]["1001"]["Severity"] = "Informational"
        actions = logs.plan(desired(), actual)
        logs.apply_plan(client, desired(), actions)
        self.assertEqual(client.writes[0][0], "del")
        self.assertEqual(client.writes[0][1],
                         "Logs#0.UdpSysLogWriter#alloy_site1.LogSubscription#1001")
        self.assertEqual(client.writes[1][0], "init")

    def test_blocked_action_writes_nothing(self):
        client = FakeClient()
        actual = actual_converged()
        actual["properties"]["RemoteEndpointUri"] = "udp://192.0.2.99:514/"
        actions = logs.plan(desired(), actual)
        logs.apply_plan(client, desired(), actions)
        self.assertEqual(client.writes, [])


class TestVerify(unittest.TestCase):
    """Read-back is the only failure detection this protocol permits."""

    def _client_with(self, state):
        reads = {}
        path = "Logs#0.UdpSysLogWriter#alloy_site1"
        if state["exists"]:
            reads[path] = state["properties"]
            reads[path + ".MessageLogSettings#0"] = state["message_log_settings"]
            for typeid, props in state["subscriptions"].items():
                reads["%s.LogSubscription#%s" % (path, typeid)] = props
        return FakeClient(reads)

    def test_passes_when_the_device_agrees(self):
        client = self._client_with(actual_converged())
        logs.verify(client, desired())

    def test_missing_subscription_is_caught(self):
        state = actual_converged()
        del state["subscriptions"]["7002"]
        client = self._client_with(state)
        with self.assertRaises(sapv2.SapV2VerifyError) as ctx:
            logs.verify(client, desired())
        self.assertIn("subscription 7002 missing", str(ctx.exception))

    def test_silently_ignored_enum_is_caught(self):
        # The exact trap this whole design exists for: "In" is accepted on the
        # wire, ignored by the device, and invisible without a read-back.
        state = actual_converged()
        state["message_log_settings"] = {"Lwrp": "None"}
        client = self._client_with(state)
        with self.assertRaises(sapv2.SapV2VerifyError) as ctx:
            logs.verify(client, desired())
        self.assertIn("MessageLogSettings.Lwrp", str(ctx.exception))

    def test_absent_state_verifies_the_writer_is_gone(self):
        client = self._client_with(actual_converged())
        with self.assertRaises(sapv2.SapV2VerifyError) as ctx:
            logs.verify(client, desired(state="absent"))
        self.assertIn("still exists after delete", str(ctx.exception))


class TestValidation(unittest.TestCase):
    def test_in_and_out_are_rejected_before_the_wire(self):
        problems = logs.validate_message_log_settings({"Lwrp": "In"})
        self.assertEqual(len(problems), 1)
        self.assertIn("silently ignored", problems[0])

    def test_valid_direction_values_pass(self):
        for value in logs.DIRECTION_VALUES:
            self.assertEqual(logs.validate_message_log_settings({"Lwrp": value}), [])

    def test_unknown_property_is_rejected(self):
        self.assertTrue(logs.validate_message_log_settings({"Nonsense": "Both"}))

    def test_duplicate_typeid_is_rejected(self):
        problems = logs.validate_subscriptions([SUB_1001, dict(SUB_1001)])
        self.assertTrue(any("duplicate typeid" in p for p in problems))

    def test_missing_expression_is_rejected(self):
        self.assertTrue(logs.validate_subscriptions([{"typeid": 1, "subscription": ""}]))


class TestPathHelpers(unittest.TestCase):
    def test_expected_uri_is_built_from_a_bare_ip(self):
        self.assertEqual(logs.expected_uri("192.0.2.33"), "udp://192.0.2.33:514/")

    def test_dotted_writer_name_is_bracket_quoted(self):
        self.assertEqual(logs.writer_path("alloy.site1"),
                         "Logs#0.UdpSysLogWriter#[alloy.site1]")


if __name__ == "__main__":
    unittest.main()
