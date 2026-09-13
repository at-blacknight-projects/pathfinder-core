# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The device's Advanced options startup script.

PathfinderCore replays a list of SapV2 commands at boot. That list is the
device's real desired state, it is per-host and editable in the GUI, and it is
NOT reachable over SapV2 - there is no startup-file object anywhere in the
object tree. So a module can change a live value and verify it, and still have
the script put the old value back at the next restart.

This parses the script as the operator sees it, so the caller can be told
that up front instead of discovering it after a reboot.

Why raw command lines rather than a typed mapping
-------------------------------------------------
The script is not a settings form. A real one contains at least two verbs and
a parameter name that is not a property name::

    SET Logs#0.LogRotator#0.RotateRule#0 MaxFileSize=100
    NOP Devices#0.EndpointDiscoverers#0.LivewireEndpointDiscovery localAxiaIP="0.0.0.0"

Modelling it as a dict keyed on names this collection happens to know would
silently drop everything else - including that NOP line, and anything a
future firmware adds. Parsing the lines keeps those visible.

The grammar is not documented anywhere available, so this parser is
deliberately permissive and reports what it could not interpret rather than
skipping it quietly. A line nobody can parse is exactly the line worth
showing a human.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

try:
    from .sapv2 import _split_top_level, _unquote
except ImportError:  # pragma: no cover - direct file import in unit tests
    from sapv2 import _split_top_level, _unquote  # type: ignore


#: Verbs observed in a real script. Others are parsed and reported rather than
#: rejected, since the grammar is undocumented.
KNOWN_VERBS = ("SET", "NOP", "INIT", "DEL")

_LINE_RE = re.compile(r"^\s*(?P<verb>[A-Za-z]+)\s+(?P<path>\S+)\s*(?P<rest>.*)$")


class Command(object):
    """One assignment from the script: a verb, an object, a name and a value.

    A single line can carry several assignments (comma-separated), so one
    source line may produce more than one of these.
    """

    def __init__(self, verb, path, name, value, raw):
        self.verb = verb
        self.path = path
        #: Property name for SET. For other verbs this may be an init-style
        #: parameter name instead, which is NOT the same namespace - hence
        #: `is_property` below.
        self.name = name
        self.value = value
        self.raw = raw

    @property
    def is_property(self):
        """Whether `name` can be compared against a live property read.

        Only true for SET. An INIT parameter name is a different vocabulary
        from the object's property names, and what NOP does is uncharacterised,
        so neither can be checked against a `get`.
        """
        return self.verb.upper() == "SET"

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<Command %s %s %s=%s>" % (self.verb, self.path, self.name, self.value)


def parse_script(lines):
    """Parse script lines into ``(commands, unparsed)``.

    `unparsed` holds ``(line, reason)`` for anything not understood. Callers
    should surface it: an unreadable line is still being replayed at every
    boot, and silence about it is the failure mode this collection exists to
    avoid.
    """
    commands, unparsed = [], []
    for raw in lines or []:
        line = (raw or "").strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            unparsed.append((raw, "does not look like '<VERB> <path> <name>=<value>'"))
            continue
        verb = match.group("verb").upper()
        path = match.group("path")
        rest = match.group("rest").strip()
        if not rest:
            # A bare verb and path is legitimate for DEL, meaningless for SET.
            if verb in ("DEL",):
                commands.append(Command(verb, path, None, None, raw))
            else:
                unparsed.append((raw, "no <name>=<value> after the object path"))
            continue
        pairs = 0
        for item in _split_top_level(rest):
            if "=" not in item:
                unparsed.append((raw, "cannot read %r as name=value" % item))
                continue
            name, _sep, value = item.partition("=")
            commands.append(Command(verb, path, name.strip(), _unquote(value), raw))
            pairs += 1
        if not pairs:
            unparsed.append((raw, "no name=value pairs found"))
    return commands, unparsed


def index_by_target(commands):
    """``{(path, name): Command}`` for the SET commands only.

    Later lines win, matching replay order: if a script sets the same property
    twice, the last one is what the device ends up with.
    """
    index = {}
    for command in commands:
        if command.is_property:
            index[(command.path, command.name)] = command
    return index


def boot_vs_live(commands, live, factory=None):
    """Where the script's value differs from what the device currently holds.

    `live` is ``{path: {property: value}}``. Returns the differences, plus the
    lines whose target property SapV2 does not report.

    That second list used to be called "dead lines", on the reasoning that a
    property the API does not expose cannot be set. That inference was wrong.
    The startup file is processed by its own loader - ``Devices#0`` has a
    ``StartupFileProcessed`` property precisely because of it - and that loader
    evidently understands directives the SapV2 object model does not surface as
    properties. Measured: ``SET Devices#0 LwcpSs=True`` and
    ``SET Devices#0 QorMonitor=True`` are absent from ``get`` AND from ``rfs``,
    yet both ship in the device's own FACTORY DEFAULT script.

    So these are reported as *unverifiable from here*, not as broken. `factory`
    - the factory default script, which the web admin page carries alongside
    the live one - lets each entry say whether the vendor ships that line,
    which is the strongest signal available on which side of the line it falls.
    """
    factory_lines = set((factory or []))
    drift, unverifiable = [], []
    for (path, name), command in sorted(index_by_target(commands).items()):
        properties = live.get(path)
        if properties is None:
            continue
        if name not in properties:
            shipped = command.raw.strip() in factory_lines
            unverifiable.append({
                "path": path, "property": name, "script_value": command.value,
                "in_factory_defaults": shipped,
                "reason": (
                    "SapV2 does not report this property, so its effect cannot "
                    "be confirmed from here. " + (
                        "It is in the device's FACTORY DEFAULT script, so it is "
                        "a vendor-shipped directive the startup loader "
                        "understands even though the API does not expose it - "
                        "leave it alone."
                        if shipped else
                        "It is not in the factory defaults either, so it may be "
                        "a typo, a directive for a different firmware, or one "
                        "only the startup loader understands. Check it before "
                        "assuming either way.")),
            })
            continue
        current = str(properties[name]).strip()
        if current != str(command.value).strip():
            drift.append({
                "path": path, "property": name,
                "live": current, "script": command.value,
            })
    return drift, unverifiable


def conflicts(intended_writes, commands):
    """Warn where a write will be undone by the script at the next reboot.

    `intended_writes` is ``[(path, property, value)]`` - whatever the calling
    module is about to set. Only SET lines are considered, because only those
    can be compared against a property.
    """
    index = index_by_target(commands)
    warnings = []
    for path, name, value in intended_writes:
        command = index.get((path, name))
        if command is None:
            continue
        if str(command.value).strip() == str(value).strip():
            continue
        warnings.append(
            "%s.%s will be set to %s now, but the device's Advanced options "
            "startup script sets it to %s, so it reverts at the next reboot. "
            "Change the script too if this must persist."
            % (path, name, value, command.value))
    return warnings
