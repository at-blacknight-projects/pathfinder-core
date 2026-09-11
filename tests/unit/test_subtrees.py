# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the runtime/config boundary.

These are the safety rails, so they are tested for what they REFUSE at least as
hard as for what they allow.
"""
import unittest

from loader import sapv2, subtrees


class TestClassification(unittest.TestCase):
    def setUp(self):
        self.guard = subtrees.SubtreeGuard()

    def test_descendant_paths_inherit_the_root_entry(self):
        entry = self.guard.classify(
            "Logs#0.UdpSysLogWriter#site1.LogSubscription#7002")
        self.assertEqual(entry.path, "Logs#0")
        self.assertEqual(entry.kind, subtrees.DECLARATIVE)

    def test_longest_prefix_wins(self):
        # System#0 is runtime telemetry; System#0.Access#0 is security config
        # that the firmware exposes read-only. Different classifications, so
        # the more specific entry has to win.
        self.assertEqual(self.guard.classify("System#0.Cpu#0").kind,
                         subtrees.RUNTIME)
        self.assertEqual(self.guard.classify("System#0.Access#0").kind,
                         subtrees.READ_ONLY)

    def test_prefix_match_is_on_path_segments_not_substrings(self):
        # "Logs#0Extra" must not match the "Logs#0" entry.
        self.assertIsNone(self.guard.classify("Logs#0Extra.Thing#1"))

    def test_unknown_path_is_unclassified(self):
        self.assertIsNone(self.guard.classify("Wibble#0"))


class TestGuardRefusals(unittest.TestCase):
    def setUp(self):
        self.guard = subtrees.SubtreeGuard()

    def test_declarative_subtree_is_allowed(self):
        self.guard.assert_writable("Logs#0.UdpSysLogWriter#x", verb="set",
                                   properties=["Subscription"])

    def test_unlisted_path_is_refused_by_default(self):
        with self.assertRaises(sapv2.SapV2GuardError) as ctx:
            self.guard.assert_writable("Wibble#0.Thing#1", verb="set")
        self.assertIn("no entry in the runtime/config boundary", str(ctx.exception))

    def test_program_subtree_is_refused(self):
        with self.assertRaises(sapv2.SapV2GuardError) as ctx:
            self.guard.assert_writable("LogicFlows#0.Flow#1", verb="set")
        self.assertIn("program", str(ctx.exception))

    def test_runtime_subtree_is_refused(self):
        with self.assertRaises(sapv2.SapV2GuardError):
            self.guard.assert_writable("System#0.Cpu#0", verb="set")

    def test_runtime_property_in_a_mixed_subtree_is_refused(self):
        # The whole point of MIXED: the slot itself is configuration, its live
        # value is not.
        self.guard.assert_writable("MemorySlots#0.MemorySlot#x", verb="set",
                                   properties=["SlotName", "Persistent"])
        with self.assertRaises(sapv2.SapV2GuardError) as ctx:
            self.guard.assert_writable("MemorySlots#0.MemorySlot#x", verb="set",
                                       properties=["SlotValue"])
        self.assertIn("live runtime state", str(ctx.exception))

    def test_router_current_source_path_is_refused(self):
        # Writing this re-routes air.
        with self.assertRaises(sapv2.SapV2GuardError):
            self.guard.assert_writable("Routers#0.Router#1", verb="set",
                                       properties=["CurrentSourcePath"])

    def test_delete_refused_where_another_system_creates_objects(self):
        # The bridge creates and deletes memory slots at runtime, so purging
        # them would delete objects this collection never created.
        with self.assertRaises(sapv2.SapV2GuardError) as ctx:
            self.guard.assert_writable("MemorySlots#0.MemorySlot#x", verb="del")
        self.assertIn("created by another system", str(ctx.exception))

    def test_delete_allowed_in_logs(self):
        self.guard.assert_writable("Logs#0.UdpSysLogWriter#x", verb="del")

    def test_allow_all_escape_hatch(self):
        subtrees.SubtreeGuard(allow_all=True).assert_writable(
            "LogicFlows#0.Flow#1", verb="set")


class TestGuardIsEnforcedByTheClient(unittest.TestCase):
    """The rail lives in the client, so a reconciler cannot forget to check."""

    def test_client_set_refuses_a_runtime_subtree(self):
        client = sapv2.SapV2Client(host="unused", check_mode=True)
        with self.assertRaises(sapv2.SapV2GuardError):
            client.set("System#0.Cpu#0", {"Anything": "1"})

    def test_client_refuses_via_raw_execute_too(self):
        client = sapv2.SapV2Client(host="unused", check_mode=True)
        with self.assertRaises(sapv2.SapV2GuardError):
            client.execute("set Scenes#0.Scene#1 ActivateScene=True")

    def test_check_mode_records_but_does_not_send(self):
        client = sapv2.SapV2Client(host="unused", check_mode=True)
        client.set("Logs#0.UdpSysLogWriter#x.MessageLogSettings#0", {"Lwrp": "Both"})
        self.assertEqual(len(client.transcript), 1)
        self.assertTrue(client.transcript[0]["skipped"])


class TestBoundaryReport(unittest.TestCase):
    def test_every_entry_states_a_reason(self):
        for row in subtrees.boundary_report():
            self.assertTrue(row["reason"].strip(),
                            "%s has no reason" % row["path"])

    def test_only_logs_is_implemented_so_far(self):
        implemented = [r["path"] for r in subtrees.boundary_report()
                       if r["module"] != "-"]
        self.assertEqual(implemented, ["Logs#0"])


if __name__ == "__main__":
    unittest.main()
