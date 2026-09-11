# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parser and wire-format tests for the layer-1 SapV2 client."""
import unittest

from loader import sapv2


class TestSplitTopLevel(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(sapv2._split_top_level('A=1, B=2'), ['A=1', 'B=2'])

    def test_comma_inside_quotes_is_not_a_separator(self):
        # Subscription expressions and UiDescription text both contain commas.
        self.assertEqual(
            sapv2._split_top_level('A="x, y", B=2'), ['A="x, y"', 'B=2'])

    def test_comma_inside_brackets_is_not_a_separator(self):
        # rfs metadata arrives as a bracketed group of comma-separated pairs.
        self.assertEqual(
            sapv2._split_top_level('A=[ReadWrite=RW,SyntaxType=TXT], B=2'),
            ['A=[ReadWrite=RW,SyntaxType=TXT]', 'B=2'])


class TestParseIndi(unittest.TestCase):
    def test_single_object(self):
        text = 'indi Logs#0.UdpSysLogWriter#sca1 Name="sca1", Connected="True"'
        self.assertEqual(
            sapv2.parse_indi(text),
            {"Logs#0.UdpSysLogWriter#sca1": {"Name": "sca1", "Connected": "True"}})

    def test_none_reply_is_empty_not_an_error(self):
        # An unknown path and an object with no children are the same shape.
        self.assertEqual(sapv2.parse_indi("indi NONE"), {})

    def test_children_listing_merges_lines(self):
        text = ('indi Logs#0.LogSubscription#1001 Severity="Warning"\r\n'
                'indi Logs#0.LogSubscription#7002 Severity="Informational"')
        self.assertEqual(sorted(sapv2.parse_indi(text)),
                         ["Logs#0.LogSubscription#1001",
                          "Logs#0.LogSubscription#7002"])

    def test_value_containing_a_comma_survives(self):
        text = 'indi X Subscription="sub Devices#0 GAIN $MAX_DEPTH=-1, more"'
        self.assertEqual(sapv2.parse_indi(text)["X"]["Subscription"],
                         "sub Devices#0 GAIN $MAX_DEPTH=-1, more")

    def test_empty_input(self):
        self.assertEqual(sapv2.parse_indi(""), {})


class TestParseSchema(unittest.TestCase):
    SFR = ('sfr Logs#0.UdpSysLogWriter#x '
           'Name=[ReadWrite=RO,SyntaxType=TXT], '
           'Subscription=[ReadWrite=RW,SyntaxType=TXT], '
           'Secret=[ReadWrite=WO,SyntaxType=TXT]')

    def test_access_and_syntax(self):
        schema = sapv2.parse_schema(self.SFR)
        self.assertEqual(schema["Name"]["ReadWrite"], "RO")
        self.assertEqual(schema["Subscription"]["SyntaxType"], "TXT")

    def test_is_writable_requires_a_positive_rw(self):
        schema = sapv2.parse_schema(self.SFR)
        self.assertTrue(sapv2.is_writable(schema, "Subscription"))
        self.assertFalse(sapv2.is_writable(schema, "Name"))
        # Write-only is not writable for our purposes: it can never be read
        # back, so a write to it can never be verified.
        self.assertFalse(sapv2.is_writable(schema, "Secret"))
        self.assertFalse(sapv2.is_writable(schema, "NoSuchProperty"))


class TestPathQuoting(unittest.TestCase):
    def test_plain_name(self):
        self.assertEqual(sapv2.quote_path_segment("alloy_sca1"), "#alloy_sca1")

    def test_dotted_name_is_bracketed(self):
        # "." is the path separator, so a dotted name must be bracket-quoted.
        # Real devices emit this form, e.g. LogFileWriter#[Connected_Msg.log].
        self.assertEqual(sapv2.quote_path_segment("Connected_Msg.log"),
                         "#[Connected_Msg.log]")

    def test_integer_typeid(self):
        self.assertEqual(sapv2.quote_path_segment(7002), "#7002")


class TestInitRendering(unittest.TestCase):
    """These lock the exact wire forms proven against real hardware.

    init parameters are comma-separated and use the device's own lowercase
    names, not the object's property names. Both mistakes are silent no-ops, so
    a regression here would look like "creation is not supported" rather than
    like a bug.
    """

    def test_writer_create_matches_proven_form(self):
        self.assertEqual(
            sapv2.render_init_params([("name", "spike4"), ("ip", "172.23.150.207")]),
            "name=spike4,ip=172.23.150.207")

    def test_subscription_create_matches_proven_form(self):
        rendered = sapv2.render_init_params([
            ("subscription", "sub Devices#0 GAIN $MAX_DEPTH=-1"),
            ("typeid", 7002),
            ("severity", "Informational"),
            ("customname", "device-gain"),
        ])
        self.assertEqual(
            rendered,
            'subscription="sub Devices#0 GAIN $MAX_DEPTH=-1",typeid=7002,'
            'severity=Informational,customname="device-gain"')

    def test_none_values_are_omitted(self):
        self.assertEqual(
            sapv2.render_init_params([("name", "x"), ("customname", None)]),
            "name=x")


class TestRenderValue(unittest.TestCase):
    def test_enum_and_bool_are_bare(self):
        self.assertEqual(sapv2.render_value("Both"), "Both")
        self.assertEqual(sapv2.render_value(True), "True")
        self.assertEqual(sapv2.render_value(False), "False")

    def test_expression_with_spaces_is_quoted(self):
        self.assertEqual(sapv2.render_value("sub Devices#0 GAIN"),
                         '"sub Devices#0 GAIN"')


if __name__ == "__main__":
    unittest.main()
