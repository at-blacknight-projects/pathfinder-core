# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay real device replies through the parsers.

Every other test module drives the code with fakes I wrote, which means they
test the code against my understanding of the protocol. That understanding has
been wrong repeatedly and expensively. These tests replay bytes the device
actually sent, so they fail when the code stops agreeing with the hardware
rather than when it stops agreeing with me.

See fixtures/README.md for what was captured and why.
"""
import io
import re
import os
import unittest

from loader import logs, sapv2

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
DEVICES = ("clean", "populated")


def read(path):
    """Read a fixture verbatim. ``newline=""`` keeps the device's CRLFs."""
    with io.open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def reply(device, name):
    return read(os.path.join(FIXTURES, device, name + ".txt"))


def firmware(device):
    return read(os.path.join(FIXTURES, device, "FIRMWARE")).strip()


def every_fixture():
    """Yield ``(device, filename, text)`` for every captured file."""
    for device in DEVICES:
        directory = os.path.join(FIXTURES, device)
        for name in sorted(os.listdir(directory)):
            yield device, name, read(os.path.join(directory, name))


# These fixtures live in a PUBLIC repository, and the originals carried real
# cluster addresses and a site-coded hostname.
#
# The guard is an ALLOW-list of shapes, deliberately. A deny-list would have to
# spell out the hostnames, system names and account names being excluded - which
# puts every one of them in the public repo, in a file whose purpose is to
# announce that they are sensitive. That is a worse leak than the one it
# prevents, and the first draft of this test made exactly that mistake.
#
#: Any dotted quad must be RFC 5737 documentation space (or the bind-all
#: address, which the device really does report for a TCP listener).
QUAD = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")

#: Anything ending in a real TLD is a hostname. Restricted to actual suffixes so
#: it does not fire on a filename like SAPv2Log.log or a version like 1.10.9_vm.
DNS_LIKE = re.compile(
    r"\b[a-z0-9][a-z0-9._-]*\."
    r"(?:com|net|org|io|au|nz|uk|us|co|internal|local|lan|corp)\b", re.I)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: Credential material. Naming the FORMATS is safe - "$apr1$" is a public
#: marker, not a secret - and it catches what the address and hostname rules
#: cannot. This exists because a capture of
#: ``get Users#0.SapUser#<name> Constructor`` came back carrying the account's
#: Apache MD5 password hash, which is weak and offline-crackable, and was one
#: commit away from a public repository. Reading that subtree is inherently
#: secret; do not capture it.
SECRET_SHAPES = re.compile(
    r"\$(?:apr1|1|2[aby]|5|6|y)\$"          # crypt hashes, incl. Apache MD5
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"        # PEM keys
    r"|\b[A-Za-z0-9+/]{40,}={0,2}\b",        # long base64-ish blobs
    re.I)


class TestFixturesArePresent(unittest.TestCase):
    def test_both_devices_captured(self):
        for device in DEVICES:
            self.assertTrue(os.path.isdir(os.path.join(FIXTURES, device)), device)

    def test_they_are_different_firmwares(self):
        # The value of a second capture is that it is not the first one.
        self.assertNotEqual(firmware("clean"), firmware("populated"))

    def test_replies_kept_their_line_endings(self):
        # CRLF framing is part of what is being tested; a capture normalised to
        # LF would quietly stop exercising it.
        self.assertIn("\r\n", reply("clean", "root_listing"))

    def test_every_address_is_documentation_space(self):
        """RFC 5737 or nothing. A digit-quad that is not 192.0.2.x is a leak."""
        for device, name, text in every_fixture():
            for match in QUAD.finditer(text):
                address = match.group(0)
                if all(int(part) < 256 for part in match.groups()):
                    self.assertTrue(
                        address.startswith("192.0.2.") or address == "0.0.0.0",
                        "%s/%s carries %s" % (device, name, address))

    def test_every_hostname_is_the_documentation_domain(self):
        """Anything with a real TLD must be example.*"""
        for device, name, text in every_fixture():
            for match in DNS_LIKE.finditer(text):
                host = match.group(0).lower()
                self.assertTrue(
                    host.startswith("example.") or ".example." in host,
                    "%s/%s carries hostname %s" % (device, name, host))

    def test_no_email_address_survived(self):
        for device, name, text in every_fixture():
            self.assertIsNone(EMAIL.search(text),
                              "%s/%s carries an email address" % (device, name))

    def test_no_credential_material_survived(self):
        """The one a shape-guard for addresses and hostnames would miss.

        A capture of ``Users#0.SapUser#<name> Constructor`` came back carrying
        the account's Apache MD5 password hash. Nothing about that looks like an
        address or a hostname, and it was one commit from being public.
        """
        for device, name, text in every_fixture():
            match = SECRET_SHAPES.search(text)
            self.assertIsNone(
                match, "%s/%s looks like credential material (%s...)"
                % (device, name, (match.group(0)[:12] if match else "")))

    def test_the_users_subtree_was_never_captured(self):
        """Reading a SapUser returns its password hash in the clear.

        There is no safe way to keep such a capture, so the rule is that the
        subtree is not captured at all rather than captured and scrubbed.
        """
        for device, name, text in every_fixture():
            self.assertNotIn("SapUser", text, "%s/%s" % (device, name))


class TestSchemaParsing(unittest.TestCase):
    """The escaped-quote bug, against the reply that caused it."""

    def test_no_property_name_is_invented(self):
        for device in DEVICES:
            schema = sapv2.parse_schema(reply(device, "rfs_devices"))
            for name in schema:
                self.assertNotIn(",", name, device)
                self.assertNotIn('"', name, device)
                self.assertNotIn("\\", name, device)

    def test_every_property_has_an_access_type(self):
        # Losing one is not cosmetic: access type decides whether a property is
        # configuration or refused as runtime state.
        for device in DEVICES:
            schema = sapv2.parse_schema(reply(device, "rfs_devices"))
            missing = [n for n, meta in schema.items() if not meta.get("ReadWrite")]
            self.assertEqual(missing, [], device)

    def test_the_properties_after_a_quoted_description_survive(self):
        for device in DEVICES:
            schema = sapv2.parse_schema(reply(device, "rfs_devices"))
            # Ready carries the UiDescription that used to break the split;
            # these follow it in the reply.
            self.assertEqual(schema["SubmitSapMessage"]["ReadWrite"], "WO")
            self.assertIn("SystemStartupState", schema)
            self.assertIn("ForceServiceRestart", schema)

    def test_known_access_types_are_read_correctly(self):
        schema = sapv2.parse_schema(reply("clean", "rfs_devices"))
        self.assertEqual(schema["DeviceCount"]["ReadWrite"], "RO")
        self.assertEqual(schema["FpStatPollRate"]["ReadWrite"], "RW")
        self.assertEqual(schema["SubmitSapMessage"]["ReadWrite"], "WO")

    def test_the_two_firmwares_agree_on_the_schema(self):
        # Values differ between devices; the schema does not. If a future
        # firmware breaks this, that is worth knowing loudly.
        self.assertEqual(sapv2.parse_schema(reply("clean", "rfs_devices")),
                         sapv2.parse_schema(reply("populated", "rfs_devices")))


class TestDoneTerminator(unittest.TestCase):
    """The terminator must be stripped, not absorbed into a value."""

    def test_it_is_detected_on_a_real_reply(self):
        for device in DEVICES:
            self.assertTrue(sapv2.has_done(reply(device, "done_logs_object")),
                            device)

    def test_the_empty_result_form_parses_to_nothing(self):
        for device in DEVICES:
            raw = reply(device, "done_unknown_path")
            self.assertTrue(sapv2.has_done(raw), device)
            self.assertEqual(sapv2.parse_indi(raw), {}, device)

    def test_it_does_not_end_up_inside_the_last_value(self):
        raw = reply("populated", "done_message_log_settings")
        parsed = sapv2.parse_indi(raw)
        self.assertTrue(parsed)
        for path, properties in parsed.items():
            for name, value in properties.items():
                self.assertNotIn("$DONE", str(value),
                                 "%s.%s absorbed the terminator" % (path, name))

    def test_a_terminated_read_parses_the_same_as_an_unterminated_one(self):
        for device in DEVICES:
            self.assertEqual(sapv2.parse_indi(reply(device, "logs_object")),
                             sapv2.parse_indi(reply(device, "done_logs_object")),
                             device)

    def test_schema_parsing_is_unaffected_by_the_terminator(self):
        for device in DEVICES:
            self.assertEqual(sapv2.parse_schema(reply(device, "rfs_devices")),
                             sapv2.parse_schema(reply(device, "done_rfs_devices")),
                             device)


class TestDeepRead(unittest.TestCase):
    """$MAX_DEPTH against a real subtree."""

    def test_the_populated_device_returns_its_whole_subtree(self):
        parsed = sapv2.parse_indi(reply("populated", "logs_deep"))
        subscriptions = [p for p in parsed if ".LogSubscription#" in p]
        self.assertGreater(len(parsed), 100)
        self.assertEqual(len(subscriptions), 74)

    def test_max_depth_excludes_the_object_itself(self):
        # Measured, and the reason tree() issues a second read. A caller that
        # assumes otherwise concludes the object does not exist.
        for device in DEVICES:
            parsed = sapv2.parse_indi(reply(device, "logs_deep"))
            self.assertNotIn("Logs#0", parsed, device)

    def test_a_listing_carries_paths_but_no_properties(self):
        parsed = sapv2.parse_indi(reply("populated", "logs_children"))
        self.assertGreater(len(parsed), 10)
        self.assertTrue(all(not props for props in parsed.values()))

    def test_bracket_quoted_names_survive_the_round_trip(self):
        parsed = sapv2.parse_indi(reply("populated", "logs_deep"))
        quoted = [p for p in parsed if "#[" in p]
        self.assertTrue(quoted)
        # A log file name contains a dot, which is also the path separator.
        self.assertTrue(any(p.endswith(".log]") for p in quoted))


class TestWriterDiscovery(unittest.TestCase):
    """list_writers against real subtrees, including the empty case."""

    class Replay(object):
        def __init__(self, device):
            self.device = device

        def tree(self, path, depth=-1, include_self=True):
            if path != "Logs#0":
                return {}
            return sapv2.parse_indi(reply(self.device, "logs_deep"))

        def get(self, path, prop=None):
            return {}

    def test_the_populated_device_yields_its_writers(self):
        found = logs.list_writers(self.Replay("populated"))
        by_type = {}
        for key, _name in found:
            by_type[key] = by_type.get(key, 0) + 1
        self.assertEqual(by_type.get("log_file"), 13)
        self.assertEqual(by_type.get("udp_syslog"), 1)
        self.assertEqual(by_type.get("tcp_client"), 1)

    def test_the_rotator_is_not_mistaken_for_a_writer(self):
        found = logs.list_writers(self.Replay("populated"))
        self.assertFalse([k for k in found if "rotator" in k[0].lower()])

    def test_a_device_with_no_writers_yields_none(self):
        # The clean unit has only LogRotator#0 under Logs#0. An empty result
        # has to mean "no writers", not "the read failed".
        self.assertEqual(logs.list_writers(self.Replay("clean")), {})


class TestErrorAndConstructor(unittest.TestCase):
    def test_an_error_reply_is_recognised(self):
        for device in DEVICES:
            parsed = sapv2.parse_error(reply(device, "error_reply"))
            self.assertIsNotNone(parsed, device)
            path, op, status = parsed
            self.assertEqual(path, "Logs#0")
            self.assertEqual(op, "constructor")
            self.assertIn("Unsupported", status)

    def test_an_error_reply_is_not_parsed_as_properties(self):
        for device in DEVICES:
            self.assertEqual(sapv2.parse_indi(reply(device, "error_reply")), {},
                             device)

    def test_constructor_is_unwrapped(self):
        # Captured from a memory slot, NOT from a SapUser: reading an account
        # returns its password hash, so that subtree is never captured.
        parsed = sapv2.parse_indi(reply("clean", "constructor"))
        value = list(parsed.values())[0].get("Constructor", "")
        command = sapv2.strip_encapsulation(value)
        self.assertTrue(command.startswith("init MemorySlots#0.MemorySlot"))
        self.assertNotIn("%BeginEncap%", command)
        self.assertNotIn("%EndEncap%", command)

    def test_constructor_shows_init_params_are_not_property_names(self):
        # slotName=, not SlotName= - the whole reason Constructor is the way to
        # discover a type's init form rather than guessing from its properties.
        parsed = sapv2.parse_indi(reply("clean", "constructor"))
        command = sapv2.strip_encapsulation(
            list(parsed.values())[0].get("Constructor", ""))
        self.assertIn("slotName=", command)
        self.assertNotIn("SlotName=", command)

    def test_the_root_listing_names_the_subtrees_the_registry_knows(self):
        parsed = sapv2.parse_indi(reply("clean", "root_listing"))
        for root in ("Logs#0", "Devices#0", "Users#0", "Routers#0"):
            self.assertIn(root, parsed)


if __name__ == "__main__":
    unittest.main()


class TestEchoedModifiers(unittest.TestCase):
    """The device echoes the REQUEST'S modifiers onto every reply line.

    First understood as a $DONE quirk and fixed for $DONE alone. A deep read
    echoes $MAX_DEPTH the same way, on both firmwares captured here - so the
    corpus contained the evidence from the day it was recorded and the tests
    simply did not look. Which property carries the echo depends on the order
    the device emits properties in, so this was invisible while it landed on
    SubVersion and latent for the day it lands on Subscription.
    """

    DEEP = ("logs_deep", "done_logs_deep")

    def test_no_value_keeps_an_echoed_modifier(self):
        for device in DEVICES:
            for name in self.DEEP:
                for path, props in sapv2.parse_indi(reply(device, name)).items():
                    for prop, value in props.items():
                        if prop == "Subscription":
                            continue  # genuinely ends "... $MAX_DEPTH=-1"
                        self.assertNotIn("$MAX_DEPTH", str(value),
                                         "%s %s.%s" % (device, path, prop))
                        self.assertNotIn("$DONE", str(value),
                                         "%s %s.%s" % (device, path, prop))

    def test_no_value_keeps_a_stray_quote(self):
        # The tell for a folded modifier: the real value's closing quote ends up
        # inside the parsed string, as '"MessageLogSettings#0" $MAX_DEPTH=-1'.
        for device in DEVICES:
            for name in self.DEEP:
                for path, props in sapv2.parse_indi(reply(device, name)).items():
                    for prop, value in props.items():
                        self.assertNotIn('"', str(value),
                                         "%s %s.%s = %r" % (device, path, prop, value))

    def test_a_subscription_expression_is_not_truncated(self):
        # The other half: the modifier INSIDE a quoted value must survive,
        # because that is the expression the device actually stores.
        parsed = sapv2.parse_indi(reply("populated", "done_logs_deep"))
        expressions = [p["Subscription"] for p in parsed.values()
                       if "Subscription" in p]
        self.assertTrue(expressions)
        self.assertTrue(any(e.endswith("$MAX_DEPTH=-1") for e in expressions))

    def test_stripping_is_idempotent_and_harmless(self):
        self.assertEqual(sapv2.strip_modifiers("indi X#0 A=1"), "indi X#0 A=1")
        self.assertEqual(sapv2.strip_modifiers("indi X#0 A=1 $DONE"), "indi X#0 A=1")
        self.assertEqual(
            sapv2.strip_modifiers('indi X#0 A="v" $MAX_DEPTH=-1 $DONE'),
            'indi X#0 A="v"')
        # A quoted value ending in a modifier is left intact.
        self.assertEqual(
            sapv2.strip_modifiers('indi X#0 A="sub Y $MAX_DEPTH=-1"'),
            'indi X#0 A="sub Y $MAX_DEPTH=-1"')
