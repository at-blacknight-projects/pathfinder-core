# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the Advanced options allow-list.

The theme running through these: the vendor's own Advanced options list does
not match the device. It names a read-only property, properties that do not
exist on measured firmware, and one applied with NOP rather than SET. Every
one of those would be accepted on the wire and do nothing, so each is caught
before anything is sent.
"""
import unittest

from loader import advanced


class TestRotationLivesInPfcLogs(unittest.TestCase):
    """Rotation is under Logs#0, so pfc_logs owns it.

    The vendor's Advanced options page covers rotation, but each reconciler
    owns a subtree - so listing it here as well would put two modules on the
    same properties, which is the conflict this module was meant to avoid
    rather than cause.
    """

    def test_no_logs_subtree_options_remain_here(self):
        self.assertEqual(
            [o.key for o in advanced.OPTIONS if o.path.startswith("Logs#0")], [])

    def test_rotation_keys_are_gone(self):
        for key in ("rotate_max_file_size", "rotate_max_count",
                    "skip_clean_logs", "check_rotation_after_max_writes",
                    "minutes_between_search"):
            self.assertNotIn(key, advanced.OPTIONS_BY_KEY, key)


class TestVendorListMismatches(unittest.TestCase):
    def test_read_only_option_is_rejected(self):
        problems = advanced.validate({"floating_ips_use_unicast": True})
        self.assertEqual(len(problems), 1)
        self.assertIn("READ-ONLY", problems[0])

    def test_absent_options_are_not_writable(self):
        for key in ("lwcp_ss", "qor_monitor"):
            self.assertFalse(advanced.OPTIONS_BY_KEY[key].writable, key)
            self.assertTrue(advanced.validate({key: True}), key)

    def test_nop_applied_option_is_unsupported(self):
        self.assertTrue(advanced.validate({"livewire_local_axia_ip": "0.0.0.0"}))

    def test_none_of_those_reach_the_allow_list(self):
        for key in ("floating_ips_use_unicast", "lwcp_ss", "qor_monitor",
                    "livewire_local_axia_ip"):
            option = advanced.OPTIONS_BY_KEY[key]
            self.assertNotIn((option.path, option.prop),
                             advanced.ALLOWED_PROPERTIES, key)


class TestValidation(unittest.TestCase):
    def test_unknown_option(self):
        self.assertTrue(advanced.validate({"nope": 1}))

    def test_non_numeric_for_a_numeric_option(self):
        self.assertTrue(advanced.validate({"fp_stat_poll_rate": "abc"}))

    def test_a_good_request_passes(self):
        self.assertEqual(advanced.validate(
            {"fp_stat_poll_rate": 15000, "skip_sanity_poll": False,
             "use_staged_writes": True}), [])


class TestMissingProperties(unittest.TestCase):
    """Caught against the device's own read, not the module's table."""

    def test_absent_property_is_reported(self):
        missing = advanced.missing_properties(
            {"fp_stat_poll_rate": 1}, {"Devices#0": {}})
        self.assertEqual(len(missing), 1)
        self.assertIn("does not expose", missing[0])

    def test_present_property_is_not_reported(self):
        self.assertEqual(advanced.missing_properties(
            {"fp_stat_poll_rate": 1},
            {"Devices#0": {"FpStatPollRate": "15000"}}), [])


class TestPlan(unittest.TestCase):
    ACTUAL = {
        "Devices#0": {"FpStatPollRate": "15000", "LwrpVerPollingOnly": "False"},
        "Routers#0": {"SkipSanityPoll": "False"},
    }

    def test_only_differences_are_planned(self):
        changes = advanced.plan(
            {"fp_stat_poll_rate": 9000, "skip_sanity_poll": False}, self.ACTUAL)
        self.assertEqual([c[0] for c in changes], ["fp_stat_poll_rate"])
        self.assertEqual(changes[0][2:], ("15000", "9000"))

    def test_booleans_compare_the_way_the_device_reports_them(self):
        self.assertEqual(advanced.plan({"skip_sanity_poll": False}, self.ACTUAL), [])
        self.assertEqual(
            [c[0] for c in advanced.plan({"skip_sanity_poll": True}, self.ACTUAL)],
            ["skip_sanity_poll"])

    def test_numbers_compare_as_strings_not_types(self):
        self.assertEqual(advanced.plan({"fp_stat_poll_rate": 15000}, self.ACTUAL), [])


class TestIntendedWrites(unittest.TestCase):
    """Rendered for the startup-script checker, which works on raw commands."""

    def test_rendered_as_path_property_value_triples(self):
        self.assertEqual(
            advanced.intended_writes({"fp_stat_poll_rate": 9000}),
            [("Devices#0", "FpStatPollRate", "9000")])

    def test_unknown_keys_are_skipped(self):
        self.assertEqual(advanced.intended_writes({"nope": 1}), [])


if __name__ == "__main__":
    unittest.main()
