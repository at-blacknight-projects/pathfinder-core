# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The device's "Advanced options" - a cross-cutting set of service settings.

PathfinderCore's own Advanced options page is not one subtree. It writes
individual properties on the ROOT objects of many: ``Devices#0``,
``Routers#0``, ``Clustering#0``, ``LogicFlows#0``, ``Logs#0``,
``MemorySlots#0``, ``UserPanels#0`` and ``System#0.FloatingIps#0``. Log
rotation lives here too, which is why rotation is not a module of its own -
splitting it out would leave two modules writing the same four properties.

Why an explicit (path, property) allow-list
-------------------------------------------
The boundary registry classifies whole subtrees, and several of these subtrees
are deliberately not writable: ``LogicFlows#0`` is ``PROGRAM`` because a logic
flow is a program, and ``Devices#0``/``Routers#0``/``MemorySlots#0`` are
``MIXED`` because their children carry live state.

But the ROOT object of such a subtree is different from its children. A logic
flow is a program; ``LogicFlows#0.BufferInternalMessages`` is a service tuning
knob. Enumerating each property individually keeps default-deny intact - every
entry here is a deliberate, reviewed exception rather than an open subtree -
and it is far more precise than promoting whole subtrees to writable.

Two measured cautions
---------------------
**The vendor's own list contains a line that silently does nothing.** It
includes ``SET System#0.FloatingIps#0 UseUnicast=True``, but ``rfs`` reports
``UseUnicast`` as read-only; the only writable property there is
``LoadStoredData``. Replaying the vendor script verbatim no-ops that line with
no error. So entries are marked with their measured access and the module
refuses to write one that is not RW - rather than sending it and reporting a
confident, meaningless success.

**Durability was measured, not assumed.** The page is a startup script that
SapV2 cannot read or write, so values set at runtime might have been stamped
back at boot. They are not: across a real reboot of a Core PRO (with
``StartupFileProcessed`` True), rotation values differing from the vendor
defaults survived unchanged. That proves the startup script does not reassert
the defaults on that host; it does not prove a novel value would survive, so
the module reports ``durable: observed`` rather than claiming a guarantee.

**One vendor line uses ``NOP``, not ``SET``**
(``NOP Devices#0.EndpointDiscoverers#0.LivewireEndpointDiscovery
localAxiaIP=...``). What NOP does differently has not been characterised, so
that setting is listed as unsupported rather than guessed at.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type


#: Measured access, so the module can refuse rather than silently no-op.
RW = "RW"
RO = "RO"
UNMEASURED = "unmeasured"


class Option(object):
    """One advanced option: a single property on a single object."""

    def __init__(self, key, path, prop, access, kind, vendor_default=None,
                 supported=True, note=""):
        self.key = key
        self.path = path
        self.prop = prop
        #: Access as reported by `rfs` on real hardware.
        self.access = access
        #: "num" | "bool" | "text" - drives validation, not the wire format.
        self.kind = kind
        #: Value in the vendor's documented Advanced options defaults, where
        #: one exists. Recorded for reference; NOT applied automatically.
        self.vendor_default = vendor_default
        #: False where the device exposes the setting but this module cannot
        #: honestly manage it.
        self.supported = supported
        self.note = note

    @property
    def writable(self):
        return self.access == RW and self.supported


#: Keyed by a stable snake_case name so playbooks never embed SapV2 paths.
#: Access values are from `rfs` against a Core PRO; re-run pfc_survey after a
#: firmware upgrade rather than trusting them indefinitely.
OPTIONS = [
    # ── Log rotation and cleanup ────────────────────────────────────────────
    Option("rotate_max_file_size", "Logs#0.LogRotator#0.RotateRule#0",
           "MaxFileSize", RW, "num", vendor_default=1,
           note="Rotate once a log file reaches this size."),
    Option("rotate_max_count", "Logs#0.LogRotator#0.RotateRule#0",
           "MaxCount", RW, "num", vendor_default=3,
           note="How many rotated files to retain."),
    Option("check_rotation_after_max_writes", "Logs#0",
           "CheckRotationAfterMaxWrites", RW, "num", vendor_default=250),
    Option("skip_clean_logs", "Logs#0", "SkipCleanLogs", RW, "bool",
           vendor_default=False),
    Option("minutes_between_search", "Logs#0.LogRotator#0",
           "MinutesBetweenSearch", RW, "num",
           note="Not on the vendor's Advanced options page, but the same "
                "rotation cycle, and nothing else manages it."),

    # ── Devices ─────────────────────────────────────────────────────────────
    Option("lwrp_ver_polling_only", "Devices#0", "LwrpVerPollingOnly", RW,
           "bool", vendor_default=False),
    # On the vendor's list, but ABSENT from Devices#0 on measured firmware -
    # a read returns nothing at all, so they are not simply read-only, they do
    # not exist. Marked unmeasured (and therefore not writable) rather than
    # assumed RW; re-run pfc_survey on newer firmware before promoting them.
    Option("lwcp_ss", "Devices#0", "LwcpSs", UNMEASURED, "bool",
           vendor_default=True, supported=False,
           note="Not present on Devices#0 on measured firmware."),
    Option("qor_monitor", "Devices#0", "QorMonitor", UNMEASURED, "bool",
           vendor_default=True, supported=False,
           note="Not present on Devices#0 on measured firmware."),
    Option("fp_stat_poll_rate", "Devices#0", "FpStatPollRate", RW, "num",
           vendor_default=15000),

    # ── Routers ─────────────────────────────────────────────────────────────
    Option("skip_sanity_poll", "Routers#0", "SkipSanityPoll", RW, "bool",
           vendor_default=False),

    # ── Clustering ──────────────────────────────────────────────────────────
    Option("clustering_buffer_internal_messages", "Clustering#0",
           "BufferInternalMessages", RW, "bool", vendor_default=False),

    # ── Logic flows (service tuning, NOT the flows themselves) ──────────────
    Option("logicflows_buffer_internal_messages", "LogicFlows#0",
           "BufferInternalMessages", RW, "bool", vendor_default=False),
    Option("logicflows_task_internal_messages", "LogicFlows#0",
           "TaskInternalMessages", RW, "bool", vendor_default=False),

    # ── Memory slots (service tuning, NOT slot values) ──────────────────────
    Option("use_staged_writes", "MemorySlots#0", "UseStagedWrites", RW, "bool",
           vendor_default=True),

    # ── User panels ─────────────────────────────────────────────────────────
    Option("default_theme", "UserPanels#0", "DefaultTheme", RW, "text",
           vendor_default="default",
           note="RW, but it VALIDATES its value and silently discards an "
                "unknown one - setting it to an arbitrary string was accepted "
                "on the wire and left the value unchanged. Read-back "
                "verification catches this; nothing else would."),
    Option("alpha_filter", "UserPanels#0", "AlphaFilter", RW, "text",
           vendor_default="0.5"),
    Option("use_alpha_filter", "UserPanels#0", "UseAlphaFilter", RW, "text",
           vendor_default="True",
           note="Reported SyntaxType TXT, not BOL, though it holds True/False."),
    Option("use_alpha_filter_sa", "UserPanels#0", "UseAlphaFilterSA", RW, "text",
           vendor_default="False"),

    # ── Floating IPs ────────────────────────────────────────────────────────
    Option("floating_ips_load_stored_data", "System#0.FloatingIps#0",
           "LoadStoredData", RW, "text"),
    Option("floating_ips_use_unicast", "System#0.FloatingIps#0", "UseUnicast",
           RO, "bool", vendor_default=True, supported=False,
           note="On the vendor's Advanced options list, but rfs reports it "
                "READ-ONLY on measured firmware. Writing it is a silent "
                "no-op, so this module refuses it rather than reporting a "
                "success it cannot verify."),

    # ── Endpoint discovery ──────────────────────────────────────────────────
    Option("livewire_local_axia_ip",
           "Devices#0.EndpointDiscoverers#0.LivewireEndpointDiscovery",
           "localAxiaIP", UNMEASURED, "text", vendor_default="0.0.0.0",
           supported=False,
           note="The vendor list applies this with NOP rather than SET. What "
                "NOP does differently here has not been characterised, so "
                "this is left unsupported rather than guessed at."),
]

OPTIONS_BY_KEY = dict((o.key, o) for o in OPTIONS)

#: Every (path, property) pair this module may write. The subtree guard
#: consults it so a root-object service setting stays writable even where the
#: subtree itself is PROGRAM or MIXED.
ALLOWED_PROPERTIES = frozenset(
    (o.path, o.prop) for o in OPTIONS if o.writable)


def validate(desired):
    """Return human-readable problems with the requested options."""
    problems = []
    for key, value in (desired or {}).items():
        option = OPTIONS_BY_KEY.get(key)
        if option is None:
            problems.append(
                "unknown advanced option %r (known: %s)"
                % (key, ", ".join(sorted(OPTIONS_BY_KEY))))
            continue
        if option.access == RO:
            problems.append(
                "%s maps to %s.%s which is READ-ONLY on measured firmware; "
                "writing it is silently ignored. %s"
                % (key, option.path, option.prop, option.note))
        elif not option.supported:
            problems.append("%s is not supported by this module. %s"
                            % (key, option.note))
        elif option.kind == "num":
            try:
                int(str(value))
            except (TypeError, ValueError):
                problems.append("%s expects a number, got %r" % (key, value))
    return problems


def normalise(value):
    """Render a desired value the way the device reports it back."""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value).strip()


def missing_properties(desired, actual):
    """Options whose property is absent from the device entirely.

    The vendor's Advanced options list is not firmware-specific: it names
    settings that simply do not exist on some builds (LwcpSs and QorMonitor
    are absent from Devices#0 on measured firmware). Writing one is accepted
    and does nothing. Catching it against the device's own read, rather than
    against this module's table, keeps that correct on firmware nobody has
    surveyed yet.
    """
    missing = []
    for key in sorted(desired or {}):
        option = OPTIONS_BY_KEY.get(key)
        if option is None:
            continue
        if option.prop not in actual.get(option.path, {}):
            missing.append(
                "%s maps to %s.%s, which this device does not expose at all. "
                "Writing it would be accepted and ignored."
                % (key, option.path, option.prop))
    return missing


def plan(desired, actual):
    """Ordered (key, option, current, wanted) tuples for what needs changing.

    Pure: `actual` is ``{path: {prop: value}}`` as read from the device.
    """
    changes = []
    for key in sorted(desired or {}):
        option = OPTIONS_BY_KEY[key]
        wanted = normalise(desired[key])
        current = normalise(actual.get(option.path, {}).get(option.prop, ""))
        if current != wanted:
            changes.append((key, option, current, wanted))
    return changes


def paths_for(desired):
    """The distinct object paths a given request needs to read."""
    return sorted({OPTIONS_BY_KEY[k].path for k in (desired or {})
                   if k in OPTIONS_BY_KEY})


def report(actual):
    """Every known option's current value, for reporting and drift checks."""
    out = {}
    for option in OPTIONS:
        out[option.key] = {
            "path": option.path,
            "property": option.prop,
            "access": option.access,
            "supported": option.supported,
            "current": actual.get(option.path, {}).get(option.prop),
            "vendor_default": option.vendor_default,
        }
    return out


def render_state(desired, actual):
    """Render current and desired option values for Ansible's ``--diff``.

    Only the options the caller asked about: a diff of all 21 every run would
    bury the one line that changed.
    """
    lines_before, lines_after = [], []
    for key in sorted(desired or {}):
        option = OPTIONS_BY_KEY.get(key)
        if option is None:
            continue
        current = actual.get(option.path, {}).get(option.prop, "")
        lines_before.append("%-38s %s" % (key, current))
        lines_after.append("%-38s %s" % (key, normalise(desired[key])))
    nl = chr(10)
    return {
        "before": nl.join(lines_before) + nl if lines_before else "",
        "after": nl.join(lines_after) + nl if lines_after else "",
        "before_header": "advanced options",
        "after_header": "advanced options",
    }
