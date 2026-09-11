# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Advanced options startup-script parser.

The script replays at boot and cannot be read or written over SapV2, so it is
the one piece of desired state nothing else can check. These lock in that it
is parsed as raw commands - a mapping keyed on known option names would
silently drop the parts that matter most.
"""
import unittest

from loader import startup

# A real script, from a Core PRO.
REAL = [
    'NOP Devices#0.EndpointDiscoverers#0.LivewireEndpointDiscovery localAxiaIP="0.0.0.0"',
    "SET Devices#0 LwrpVerPollingOnly=False",
    "SET Routers#0 SkipSanityPoll=False",
    "SET Clustering#0 BufferInternalMessages=False",
    "SET LogicFlows#0 BufferInternalMessages=False",
    "SET LogicFlows#0 TaskInternalMessages=False",
    "SET Logs#0.LogRotator#0.RotateRule#0 MaxFileSize=100",
    "SET Logs#0.LogRotator#0.RotateRule#0 MaxCount=10",
    "SET Logs#0 SkipCleanLogs=False",
    "SET Logs#0 CheckRotationAfterMaxWrites=250",
    "SET MemorySlots#0 UseStagedWrites=True",
    "SET Devices#0 LwcpSs=True",
    "SET Devices#0 QorMonitor=True",
    "SET Devices#0 FpStatPollRate=15000",
    "SET UserPanels#0 DefaultTheme=default",
]


class TestParsing(unittest.TestCase):
    def test_a_real_script_parses_completely(self):
        commands, unparsed = startup.parse_script(REAL)
        self.assertEqual(unparsed, [])
        self.assertEqual(len(commands), len(REAL))

    def test_more_than_one_verb_is_preserved(self):
        commands, _unused = startup.parse_script(REAL)
        self.assertEqual({c.verb for c in commands}, {"SET", "NOP"})

    def test_nop_is_not_treated_as_a_property(self):
        # Its name is an init-style parameter, a different vocabulary from
        # property names, so it must not be compared against a live read.
        commands, _unused = startup.parse_script([REAL[0]])
        self.assertFalse(commands[0].is_property)
        self.assertEqual(commands[0].name, "localAxiaIP")
        self.assertEqual(commands[0].value, "0.0.0.0")

    def test_quoted_values_are_unquoted(self):
        commands, _unused = startup.parse_script(['SET X#0 Name="a b"'])
        self.assertEqual(commands[0].value, "a b")

    def test_comma_separated_assignments_become_separate_commands(self):
        commands, _unused = startup.parse_script(["SET Logs#0.X#0 Lwrp=Both,Lwcp=None"])
        self.assertEqual([(c.name, c.value) for c in commands],
                         [("Lwrp", "Both"), ("Lwcp", "None")])

    def test_blank_and_comment_lines_are_ignored(self):
        commands, unparsed = startup.parse_script(["", "   ", "# a note"])
        self.assertEqual((commands, unparsed), ([], []))

    def test_unreadable_lines_are_reported_not_dropped(self):
        # A line nobody can parse is still replayed every boot, so silence
        # about it is the failure mode being avoided.
        commands_unused, unparsed = startup.parse_script(["complete rubbish", "SET Logs#0"])
        self.assertEqual(len(unparsed), 2)

    def test_later_lines_win_matching_replay_order(self):
        commands, _unused = startup.parse_script(
            ["SET Logs#0 SkipCleanLogs=False", "SET Logs#0 SkipCleanLogs=True"])
        index = startup.index_by_target(commands)
        self.assertEqual(index[("Logs#0", "SkipCleanLogs")].value, "True")


class TestBootVersusLive(unittest.TestCase):
    def test_difference_is_reported(self):
        commands, _unused = startup.parse_script(REAL)
        live = {"Logs#0.LogRotator#0.RotateRule#0": {"MaxFileSize": "1",
                                                     "MaxCount": "10"}}
        drift, _absent = startup.boot_vs_live(commands, live)
        self.assertEqual([(d["property"], d["live"], d["script"]) for d in drift],
                         [("MaxFileSize", "1", "100")])

    def test_property_the_device_does_not_expose_is_flagged(self):
        # LwcpSs and QorMonitor are on this script and absent from the
        # firmware, so those lines do nothing on every boot.
        commands, _unused = startup.parse_script(REAL)
        live = {"Devices#0": {"LwrpVerPollingOnly": "False",
                              "FpStatPollRate": "15000"}}
        _drift, absent = startup.boot_vs_live(commands, live)
        self.assertEqual(sorted(a["property"] for a in absent),
                         ["LwcpSs", "QorMonitor"])

    def test_objects_not_read_are_skipped_rather_than_guessed(self):
        commands, _unused = startup.parse_script(REAL)
        drift, absent = startup.boot_vs_live(commands, {})
        self.assertEqual((drift, absent), ([], []))

    def test_nop_lines_are_not_compared(self):
        commands, _unused = startup.parse_script([REAL[0]])
        live = {"Devices#0.EndpointDiscoverers#0.LivewireEndpointDiscovery": {}}
        _drift, absent = startup.boot_vs_live(commands, live)
        self.assertEqual(absent, [])


class TestConflicts(unittest.TestCase):
    """Warn before a reboot undoes the change, not after."""

    def setUp(self):
        self.commands, _unused = startup.parse_script(REAL)

    def test_a_value_the_script_will_undo_is_reported(self):
        out = startup.conflicts(
            [("Logs#0.LogRotator#0.RotateRule#0", "MaxFileSize", "1")],
            self.commands)
        self.assertEqual(len(out), 1)
        self.assertIn("reverts at the next reboot", out[0])

    def test_a_value_matching_the_script_is_not_reported(self):
        self.assertEqual(startup.conflicts(
            [("Logs#0.LogRotator#0.RotateRule#0", "MaxFileSize", "100")],
            self.commands), [])

    def test_a_property_absent_from_the_script_is_not_reported(self):
        self.assertEqual(startup.conflicts(
            [("Logs#0.LogRotator#0", "MinutesBetweenSearch", "12")],
            self.commands), [])

    def test_no_script_means_unchecked_not_safe(self):
        # An empty result here means "not checked". It must not be read as
        # proof that nothing will revert.
        self.assertEqual(startup.conflicts(
            [("Logs#0", "SkipCleanLogs", "True")], []), [])


if __name__ == "__main__":
    unittest.main()
