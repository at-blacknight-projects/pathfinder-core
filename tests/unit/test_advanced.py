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
        self.assertTrue(advanced.validate({"rotate_max_count": "abc"}))

    def test_a_good_request_passes(self):
        self.assertEqual(advanced.validate(
            {"rotate_max_count": 3, "rotate_max_file_size": 1,
             "skip_clean_logs": False, "minutes_between_search": 15}), [])


class TestMissingProperties(unittest.TestCase):
    """Caught against the device's own read, not the module's table."""

    def test_absent_property_is_reported(self):
        actual = {"Logs#0.LogRotator#0.RotateRule#0": {}}
        missing = advanced.missing_properties({"rotate_max_count": 3}, actual)
        self.assertEqual(len(missing), 1)
        self.assertIn("does not expose", missing[0])

    def test_present_property_is_not_reported(self):
        actual = {"Logs#0.LogRotator#0.RotateRule#0": {"MaxCount": "10"}}
        self.assertEqual(
            advanced.missing_properties({"rotate_max_count": 3}, actual), [])


class TestPlan(unittest.TestCase):
    ACTUAL = {
        "Logs#0.LogRotator#0.RotateRule#0": {"MaxCount": "10", "MaxFileSize": "100"},
        "Logs#0": {"SkipCleanLogs": "False", "CheckRotationAfterMaxWrites": "250"},
    }

    def test_only_differences_are_planned(self):
        changes = advanced.plan(
            {"rotate_max_count": 3, "rotate_max_file_size": 100}, self.ACTUAL)
        self.assertEqual([c[0] for c in changes], ["rotate_max_count"])
        self.assertEqual(changes[0][2:], ("10", "3"))

    def test_booleans_compare_the_way_the_device_reports_them(self):
        self.assertEqual(advanced.plan({"skip_clean_logs": False}, self.ACTUAL), [])
        self.assertEqual(
            [c[0] for c in advanced.plan({"skip_clean_logs": True}, self.ACTUAL)],
            ["skip_clean_logs"])

    def test_numbers_compare_as_strings_not_types(self):
        self.assertEqual(
            advanced.plan({"check_rotation_after_max_writes": 250}, self.ACTUAL), [])


class TestRotationIsHereNotInItsOwnModule(unittest.TestCase):
    """All five rotation knobs live here, so nothing else can fight them."""

    def test_every_rotation_knob_is_present_and_writable(self):
        for key in ("rotate_max_file_size", "rotate_max_count",
                    "check_rotation_after_max_writes", "skip_clean_logs",
                    "minutes_between_search"):
            self.assertTrue(advanced.OPTIONS_BY_KEY[key].writable, key)

    def test_ready_is_deliberately_absent(self):
        # Toggling it does not restart anything and the one direction that
        # acts disables the service.
        self.assertNotIn("ready", advanced.OPTIONS_BY_KEY)
        for option in advanced.OPTIONS:
            self.assertNotEqual(option.prop, "Ready")


if __name__ == "__main__":
    unittest.main()


class TestStartupScriptConflicts(unittest.TestCase):
    """The startup script replays at boot and SapV2 cannot change it.

    Measured the hard way: rotation values surviving a reboot looked like
    persistence, but that host's script simply set the same numbers. A
    different value would have been reverted.
    """

    SCRIPT = {"rotate_max_file_size": 100, "rotate_max_count": 10,
              "skip_clean_logs": False}

    def test_disagreement_is_reported(self):
        out = advanced.conflicts_with_startup_script(
            {"rotate_max_file_size": 1}, self.SCRIPT)
        self.assertEqual(len(out), 1)
        self.assertIn("reverts at the next reboot", out[0])
        self.assertIn("MaxFileSize", out[0])

    def test_agreement_is_not_reported(self):
        self.assertEqual(advanced.conflicts_with_startup_script(
            {"rotate_max_file_size": 100}, self.SCRIPT), [])

    def test_option_absent_from_the_script_is_not_reported(self):
        self.assertEqual(advanced.conflicts_with_startup_script(
            {"minutes_between_search": 12}, self.SCRIPT), [])

    def test_no_script_supplied_means_unchecked_not_safe(self):
        # Empty result here means "not checked" - the caller must not read it
        # as proof that nothing reverts.
        self.assertEqual(advanced.conflicts_with_startup_script(
            {"rotate_max_file_size": 1}, {}), [])

    def test_boolean_and_numeric_forms_compare_correctly(self):
        self.assertEqual(advanced.conflicts_with_startup_script(
            {"skip_clean_logs": False}, self.SCRIPT), [])
        self.assertEqual(len(advanced.conflicts_with_startup_script(
            {"skip_clean_logs": True}, self.SCRIPT)), 1)
