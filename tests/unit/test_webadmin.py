# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the web admin client.

The save field carries the ENTIRE script, so every failure here is data loss
rather than a wrong reading. The page is also unhelpfully shaped: both the
live script and a factory reference are assigned to the same JavaScript
variable, and the live script's first line contains escaped quotes.
"""
import unittest

from loader import webadmin

# Shaped exactly like the real page: one variable, reassigned, with the id of
# the textarea being the only thing that distinguishes the two.
PAGE = (
    '<textarea id="options" name="options"></textarea>'
    '<textarea id="defaultoptions" readonly></textarea>'
    '<script language="javascript">$(document).ready(function () {'
    ' var fileData = "NOP Devices#0.X LivewireEndpointDiscovery localAxiaIP=\\"0.0.0.0\\"\r\n'
    'SET Logs#0.LogRotator#0.RotateRule#0 MaxFileSize=100\r\n";'
    ' document.getElementById("options").value=fileData;'
    ' fileData = "NOP Devices#0.X LivewireEndpointDiscovery localAxiaIP=\\"0.0.0.0\\"\r\n'
    'SET Logs#0.LogRotator#0.RotateRule#0 MaxFileSize=1\r\n'
    'SET UserPanels#0 AlphaFilter=0.5\r\n";'
    ' document.getElementById("defaultoptions").value=fileData;'
    '});</script>')


class TestExtraction(unittest.TestCase):
    def test_both_scripts_are_found_and_told_apart(self):
        scripts = webadmin.extract_scripts(PAGE)
        self.assertEqual(sorted(scripts), ["defaultoptions", "options"])

    def test_the_live_script_is_not_the_factory_one(self):
        # Keying on the JS variable name returns whichever came last, which is
        # the factory reference. Saving that back would replace a customised
        # script with stock values.
        scripts = webadmin.extract_scripts(PAGE)
        self.assertIn("MaxFileSize=100", scripts["options"])
        self.assertIn("MaxFileSize=1", scripts["defaultoptions"])
        self.assertNotIn("AlphaFilter", scripts["options"])

    def test_escaped_quotes_survive(self):
        # A regex stopping at the first quote truncates line one, and with a
        # whole-document save that is data loss.
        line = webadmin.normalise(webadmin.extract_scripts(PAGE)["options"])[0]
        self.assertTrue(line.endswith('localAxiaIP="0.0.0.0"'), line)

    def test_a_page_with_no_script_yields_nothing(self):
        self.assertEqual(webadmin.extract_scripts("<html>nothing here</html>"), {})

    def test_content_is_not_filtered_on_looking_like_commands(self):
        # Deliberately NOT filtered on containing SET/NOP. A device whose
        # Advanced options are empty has an empty textarea, and rejecting that
        # would make fetch() refuse on a perfectly normal device. Whatever is
        # assigned into the element is the script, even if it is nothing.
        page = 'var x = "just some text"; document.getElementById("options").value=x;'
        self.assertEqual(webadmin.extract_scripts(page), {"options": "just some text"})

    def test_an_empty_script_is_readable_rather_than_an_error(self):
        page = 'var x = ""; document.getElementById("options").value=x;'
        self.assertEqual(webadmin.extract_scripts(page), {"options": ""})
        client = webadmin.WebAdminClient("unused", "u", "p")
        client._request = lambda path, data=None: page
        current, factory = client.fetch()
        self.assertEqual((webadmin.normalise(current), factory), ([], None))


class TestNormaliseAndRender(unittest.TestCase):
    def test_blank_lines_and_eol_style_are_ignored_when_comparing(self):
        self.assertEqual(webadmin.normalise("a\r\n\r\nb\r\n"), ["a", "b"])

    def test_render_uses_the_devices_own_line_ending(self):
        # Preserved so a one-line change does not rewrite every line.
        self.assertEqual(webadmin.render(["a", "b"]), "a\r\nb\r\n")

    def test_round_trip_is_stable(self):
        lines = ["SET Logs#0 SkipCleanLogs=False", "SET Devices#0 QorMonitor=True"]
        self.assertEqual(webadmin.normalise(webadmin.render(lines)), lines)


class TestSaveRefusesToErase(unittest.TestCase):
    """The save replaces the whole document, so an empty one wipes it."""

    def _client(self):
        client = webadmin.WebAdminClient("unused", "u", "p")
        client._request = lambda path, data=None: self.fail("should not send")
        return client

    def test_empty_string(self):
        with self.assertRaises(webadmin.WebAdminError) as ctx:
            self._client().save("")
        self.assertIn("erase every line", str(ctx.exception))

    def test_whitespace_only(self):
        with self.assertRaises(webadmin.WebAdminError):
            self._client().save("   \r\n  \r\n")


class TestFetchRefusesToGuess(unittest.TestCase):
    def test_unrecognisable_page_raises(self):
        client = webadmin.WebAdminClient("unused", "u", "p")
        client._request = lambda path, data=None: "<html>no script</html>"
        with self.assertRaises(webadmin.WebAdminError) as ctx:
            client.fetch()
        self.assertIn("no script could be read", str(ctx.exception))

    def test_missing_options_textarea_raises_rather_than_using_the_factory(self):
        client = webadmin.WebAdminClient("unused", "u", "p")
        client._request = lambda path, data=None: (
            'var d = "SET Logs#0 SkipCleanLogs=False\r\n";'
            'document.getElementById("defaultoptions").value=d;')
        with self.assertRaises(webadmin.WebAdminError) as ctx:
            client.fetch()
        self.assertIn("FACTORY DEFAULTS", str(ctx.exception))
