# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The runtime/config boundary: which parts of the object tree may be reconciled.

Nothing in PathfinderCore's object model marks which subtrees are declarative.
``Logs#0.UdpSysLogWriter#x.RemoteEndpointUri`` and ``MemorySlots#0.MemorySlot#y.SlotValue``
are the same kind of thing to the API: a writable property on an object. But the
first is configuration a human chose, and the second is live state that
a separate control bridge rewrites continuously. A reconciler that enforced
desired state on the second would fight the bridge forever, and on a router's
``CurrentSourcePath`` it would actively re-route air.

So the boundary has to be stated somewhere, explicitly, and it may as well be
enforced rather than documented. This module is that statement.

Two design points worth keeping
-------------------------------
**The boundary runs through subtrees, not around them.** A MemorySlot's
existence, ``SlotName``, ``Persistent`` and ``StartupState`` are configuration;
its ``SlotValue`` is not. A Device's definition is configuration; its
``Connected``/``State``/``GAIN`` are not. Classifying whole subtrees as runtime
would wrongly lock out real config, so a subtree can be ``MIXED`` and name its
runtime properties.

**Default deny.** An unlisted path is refused, not allowed. Adding a subtree is
a deliberate act that requires writing down why it is safe, which is exactly the
review moment that a generic "PFC resource" module would skip.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type


#: Configuration. Safe to reconcile: a human chose this state and nothing else
#: writes it.
DECLARATIVE = "declarative"

#: Live state. Written by the device or by another system. Never reconcile.
RUNTIME = "runtime"

#: Both, in the same subtree. Reconcile the declarative properties only; the
#: named runtime properties are refused.
MIXED = "mixed"

#: Executable logic, not configuration. Logic flows and scenes are programs;
#: "desired state" is not a meaningful frame for them and a diff-and-apply
#: reconciler would be a code deployer wearing the wrong hat.
PROGRAM = "program"

#: Configuration, but the firmware exposes no writable path to it. Distinct
#: from RUNTIME: this is not live state, it simply cannot be set over SapV2, so
#: a reconciler would be writing into a void that never reports failure.
READ_ONLY = "read_only"

#: Not yet assessed. Refused until someone does the work.
UNCLASSIFIED = "unclassified"


class Subtree(object):
    """One entry in the boundary registry."""

    def __init__(self, path, kind, reason, runtime_properties=None,
                 implemented_by=None, purge_safe=False, verify_scope="object"):
        self.path = path
        self.kind = kind
        self.reason = reason
        #: Properties refused even though the subtree is otherwise declarative.
        self.runtime_properties = frozenset(runtime_properties or [])
        #: Module that reconciles this subtree, or None if not built yet.
        self.implemented_by = implemented_by
        #: Whether deleting objects this module did not create is ever safe
        #: here. False wherever another system creates objects in the subtree.
        self.purge_safe = purge_safe
        #: "object" - verify the whole object after a write; safe only where
        #: nothing else writes it. "properties" - verify only what we wrote,
        #: because a concurrent writer would otherwise cause false failures.
        self.verify_scope = verify_scope

    @property
    def writable(self):
        return self.kind in (DECLARATIVE, MIXED)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<Subtree %s %s>" % (self.path, self.kind)


#: The registry. Ordered longest-path-first at lookup time so a more specific
#: entry (``System#0.Access#0``) wins over a broader one (``System#0``).
SUBTREES = [
    Subtree(
        "Logs#0", DECLARATIVE,
        "Log writers, subscriptions and MessageLogSettings. Nothing else writes "
        "here, and the worst case for getting it wrong is losing logs rather "
        "than losing air - which is why this subtree was implemented first.",
        implemented_by="pfc_logs",
        purge_safe=True,
        verify_scope="object",
    ),
    Subtree(
        "Users#0", DECLARATIVE,
        "SapUser accounts and their UserSecurity children. Measured schema: "
        "SapUser has Username RO and Password RW; UserSecurity has IsAdmin, "
        "SecurityPaths, MenuItems, CanChangeLocks and LocksDoNotApply all RW. "
        "So this - not System#0.Access#0 - is where the writable access-control "
        "surface actually lives. Username being read-only makes a rename a "
        "delete-and-recreate. NOTE: reading a SapUser returns its password "
        "hash in the clear, so treat all reads here as secret.",
        implemented_by=None,
        purge_safe=True,
        verify_scope="object",
    ),
    Subtree(
        "System#0.Access#0", READ_ONLY,
        "Security configuration, exposed as a single SecurityJson property. "
        "Measured on a Core PRO, SecurityJson is ReadWrite=RO - it cannot be "
        "written over SapV2 at all, so the read-modify-write approach this "
        "subtree was originally scoped for is not available. It remains useful "
        "to READ for drift detection and audit. The writable equivalent is "
        "Users#0.SapUser#<name>.UserSecurity.",
        implemented_by=None,
        purge_safe=False,
        verify_scope="properties",
    ),
    Subtree(
        "Devices#0", MIXED,
        "Device definitions are configuration; device liveness and audio state "
        "are not. The runtime properties below are the ones the log "
        "subscription catalogue itself treats as events.",
        runtime_properties=[
            "Connected", "Online", "State", "GAIN", "PinState",
            "LastSent", "LastReceived", "ResponseData", "ResponseCode",
            "ResponseSuccess",
        ],
        implemented_by=None,
        purge_safe=False,
        verify_scope="properties",
    ),
    Subtree(
        "MemorySlots#0", MIXED,
        "Slot identity and persistence are configuration; SlotValue is live "
        "state written continuously by whatever control bridge owns them. Slots are also "
        "created and deleted by the bridge at runtime, so purge is never safe "
        "here - a purging reconciler would delete slots it did not create.",
        runtime_properties=["SlotValue", "LastChanged", "LastKnownValue"],
        implemented_by=None,
        purge_safe=False,
        verify_scope="properties",
    ),
    Subtree(
        "Routers#0", MIXED,
        "Router, source and destination definitions are configuration. "
        "CurrentSourcePath is the live route - writing it changes what is on "
        "air, and enforcing it would re-route on every reconcile. Treat any "
        "reconciler here as a change to broadcast output, not to config.",
        runtime_properties=[
            "CurrentSourcePath", "DestinationMessageBeingSent", "CurrentValue",
        ],
        implemented_by=None,
        purge_safe=False,
        verify_scope="properties",
    ),
    Subtree(
        "LogicFlows#0", PROGRAM,
        "Logic flows are programs. Desired-state reconciliation is the wrong "
        "model, and a diff that looks trivial can change signal routing "
        "behaviour. Deploy these deliberately or not at all.",
    ),
    Subtree(
        "Scenes#0", PROGRAM,
        "Scenes are stored sequences of actions - programs, like logic flows. "
        "Activating one changes air.",
    ),
    Subtree(
        "DeviceEmulators#0", UNCLASSIFIED,
        "Emulator definitions are probably configuration and their watchers "
        "probably runtime, but this has not been measured. Refused until it is.",
    ),
    Subtree(
        "PropertyGroups#0", UNCLASSIFIED,
        "Not assessed.",
    ),
    Subtree(
        "Clustering#0", UNCLASSIFIED,
        "Node clustering. BufferInternalMessages and ChangeLocalHostName are "
        "writable, but the subtree also exposes CreateCluster, JoinCluster, "
        "LeaveCluster and ManualSync as write-only actions - the most "
        "destructive operations on the device. Refused as a subtree; the one "
        "service setting is reachable through the advanced-options allow-list.",
    ),
    Subtree(
        "UserPanels#0", UNCLASSIFIED,
        "Operator panel presentation. Several writable properties, but the "
        "panels themselves have not been assessed and WritePanelPage is a "
        "write-only action. The theme and filter settings are reachable "
        "through the advanced-options allow-list.",
    ),
    Subtree(
        "LegacyPanels#0", UNCLASSIFIED,
        "Surveyed: 8 properties, only Ready writable. Nothing here worth "
        "reconciling, and Ready is deliberately not exposed anywhere in this "
        "collection.",
    ),
    Subtree(
        "Meters#0", UNCLASSIFIED,
        "Audio metering. Surveyed: CurrentMeteringPollRate, DefaultLowLevel "
        "and DefaultClipLevel are writable, so it is configuration rather than "
        "pure telemetry as first assumed. Left unclassified because changing "
        "metering thresholds affects what operators see, and nobody has asked "
        "for it.",
    ),
    Subtree(
        "Requests#0", UNCLASSIFIED,
        "Surveyed: 5 properties, none writable. Nothing to reconcile.",
    ),
    Subtree(
        "UpdateModerators#0", UNCLASSIFIED,
        "Surveyed: 2 properties, only Ready writable. Nothing to reconcile.",
    ),
    Subtree(
        "TimeEvents#0", UNCLASSIFIED,
        "Scheduled events. Probably declarative, but they fire actions, so "
        "treat as program-adjacent until measured.",
    ),
    Subtree(
        "System#0", RUNTIME,
        "System telemetry (Cpu, memory, uptime). Read-only in practice. Note "
        "the more specific System#0.Access#0 entry above is declarative.",
    ),
]


#: Write-only properties measured across 65 object types on a Core PRO.
#:
#: Every one of them is an imperative ACTION rather than a piece of state:
#: ForceServiceRestart, SendLwrpCommand, Reconnect, ClearLogFile, Trigger,
#: Pulse, ActivateScene, CloneTo, SendCriticalMessage, WriteTimer. The device
#: models RPC calls as write-only properties.
#:
#: Two consequences. They cannot be read back, so on a protocol that does not
#: report failure a write to one can never be verified. And more importantly
#: they are not desired state at all - "reconciling" ActivateScene would put a
#: scene to air on every run. A reconciler must refuse them; invoking one is a
#: deliberate act for a different kind of module.
ACTION_PROPERTIES = frozenset([
    "Append", "ActivateScene", "ChangeAllByValue", "ClearElapsed",
    "ClearLogFile", "CloneTo", "CopyTo", "CopyValue", "DeleteLogFile",
    "ForceServiceRestart", "Pulse", "PulseValue", "Reconnect",
    "RemoveDeviceIp", "RotateSource", "SendCriticalMessage",
    "SendLwcpCommand", "SendLwrpCommand", "SubmitSapMessage", "Trigger",
    "WriteSlot", "WriteTimer",
    # Found when surveying the objects the Advanced options page touches.
    # The clustering four are the most destructive actions on the device:
    # LeaveCluster on the wrong node is a genuine outage.
    "CreateCluster", "JoinCluster", "LeaveCluster", "ManualSync",
    "FixPanelSecurity", "WritePanelPage",
])


class SubtreeGuard(object):
    """Default-deny policy object consulted by the client's write verbs.

    Lives in the client rather than in each reconciler so that a future module,
    an ad-hoc script using ``execute()``, or a bug in a reconciler all hit the
    same rail. A reconciler that has to remember to check is a reconciler that
    eventually forgets.
    """

    def __init__(self, subtrees=None, allow_all=False):
        self.subtrees = SUBTREES if subtrees is None else subtrees
        #: Escape hatch for genuinely ad-hoc exploration tools. Never set this
        #: in a module - the whole point is that reconcilers cannot opt out.
        self.allow_all = allow_all

    def classify(self, path):
        """Return the most specific registry entry matching `path`, or None."""
        if not path:
            return None
        best = None
        for subtree in self.subtrees:
            if path == subtree.path or path.startswith(subtree.path + "."):
                if best is None or len(subtree.path) > len(best.path):
                    best = subtree
        return best

    def assert_writable(self, path, verb="set", properties=None):
        """Raise unless `path` may be written.

        The exception type is imported lazily so this module has no import-time
        dependency on the client and either can be unit-tested on its own.
        """
        try:
            from .sapv2 import SapV2GuardError
        except ImportError:  # pragma: no cover - direct file import in tests
            from sapv2 import SapV2GuardError  # type: ignore

        if self.allow_all:
            return

        props = list(properties or [])

        # A subtree's ROOT object is not its children. LogicFlows#0 is
        # classified PROGRAM because a logic flow is a program - but
        # LogicFlows#0.BufferInternalMessages is a service tuning knob, and
        # the same is true of Routers#0.SkipSanityPoll and
        # Devices#0.LwrpVerPollingOnly. Classifying by subtree alone would
        # wrongly lock those out.
        #
        # So a small, explicitly enumerated set of (path, property) pairs is
        # permitted regardless of the subtree's classification. This keeps
        # default-deny intact: each pair is an individually reviewed exception
        # rather than an opened subtree, and it applies only to `set` and only
        # when EVERY property in the request is on the list.
        if verb == "set" and props:
            try:
                from .advanced import ALLOWED_PROPERTIES
            except ImportError:  # pragma: no cover - direct file import
                from advanced import ALLOWED_PROPERTIES  # type: ignore
            if all((path, prop) in ALLOWED_PROPERTIES for prop in props):
                return

        subtree = self.classify(path)
        if subtree is None:
            raise SapV2GuardError(
                "refusing to %s %r: no entry in the runtime/config boundary "
                "registry. Unlisted paths are refused by default. If this "
                "subtree is genuinely declarative, add it to SUBTREES in "
                "subtrees.py with a reason." % (verb, path))

        if not subtree.writable:
            raise SapV2GuardError(
                "refusing to %s %r: %s is classified %s. %s"
                % (verb, path, subtree.path, subtree.kind, subtree.reason))

        if verb == "del" and not subtree.purge_safe:
            raise SapV2GuardError(
                "refusing to delete %r: objects under %s may be created by "
                "another system, so deleting what this module did not create "
                "is unsafe. %s" % (path, subtree.path, subtree.reason))

        for prop in properties or []:
            if prop in ACTION_PROPERTIES:
                raise SapV2GuardError(
                    "refusing to %s %s.%s: %s is a write-only ACTION, not "
                    "configuration. The device models RPC calls as write-only "
                    "properties, so setting it would invoke it on every run, "
                    "and it cannot be read back to verify."
                    % (verb, path, prop, prop))
            if prop in subtree.runtime_properties:
                raise SapV2GuardError(
                    "refusing to %s %s.%s: %s is live runtime state, not "
                    "configuration. Enforcing it would fight whatever writes "
                    "it. %s" % (verb, path, prop, prop, subtree.reason))


#: Shared instance used by SapV2Client when no guard is supplied.
default_guard = SubtreeGuard()


def boundary_report():
    """Render the registry as rows, for documentation and for ``--list``."""
    rows = []
    for subtree in sorted(SUBTREES, key=lambda s: s.path):
        rows.append({
            "path": subtree.path,
            "kind": subtree.kind,
            "module": subtree.implemented_by or "-",
            "purge_safe": subtree.purge_safe,
            "runtime_properties": sorted(subtree.runtime_properties),
            "reason": subtree.reason,
        })
    return rows
