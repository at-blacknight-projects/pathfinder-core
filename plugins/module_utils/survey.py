# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only schema survey of a PathfinderCore object tree.

``rfs <path>`` with no property name dumps an object's whole schema:
``ReadWrite`` (RW/RO/**WO**), ``SyntaxType``, and ``UiDescription`` per
property. It is the only runtime introspection the device offers, and the
vendor documentation claims it does not exist.

Why this is a tool rather than a one-off
----------------------------------------
Every subtree reconciler needs to know which properties are frozen after
creation, and guessing is expensive on a protocol that rejects writes silently.
A survey turns that into measured fact, and the resulting snapshot doubles as a
test fixture (real device data, no device in CI) and as a firmware-upgrade diff.

Two things the survey is careful about
--------------------------------------
**Schema is per type, not per instance.** ``Devices#0`` can hold thousands of
objects that all share one schema, and every command costs at least the idle
timeout. So the walk groups children by type - the segment before ``#`` - and
runs ``rfs`` against one representative instance of each.

**``WO`` properties break the read-back contract.** A write-only property can
never be read back, so a write to one can never be verified on a protocol that
does not acknowledge writes. The survey reports them explicitly so a reconciler
can refuse to manage them rather than reporting an unverifiable success.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type


#: Fallback roots, used only if root discovery fails.
#:
#: These were originally the hardcoded list, and they were wrong: `get .`
#: enumerates the root and returns 17 objects, six of which were missing here
#: (Clustering#0, LegacyPanels#0, Meters#0, Requests#0, UpdateModerators#0,
#: UserPanels#0). A survey that silently omits a third of the tree is worse
#: than no survey, so discovery is preferred and this is only a safety net.
FALLBACK_ROOTS = [
    "Logs#0",
    "Users#0",
    "System#0",
    "Devices#0",
    "MemorySlots#0",
    "Routers#0",
    "LogicFlows#0",
    "Scenes#0",
    "DeviceEmulators#0",
    "PropertyGroups#0",
    "TimeEvents#0",
]

#: Kept as an alias so existing callers do not break.
DEFAULT_ROOTS = FALLBACK_ROOTS


def discover_roots(client):
    """Enumerate the top-level objects with ``get .``.

    Preferred over a hardcoded list because the object set varies by firmware
    and licensed feature set, and anything missing from a hardcoded list is
    invisible rather than reported.
    """
    roots = sorted(client.children(""))
    return roots or list(FALLBACK_ROOTS)


def type_of(path):
    """The type name of an object path: the last segment before its ``#``.

    ``Logs#0.UdpSysLogWriter#main`` -> ``Logs#0.UdpSysLogWriter``, so two
    writers on the same device collapse to one schema lookup.
    """
    segment = path.rsplit(".", 1)[-1]
    if "#" not in segment:
        return path
    prefix = path.rsplit(".", 1)[0] if "." in path else ""
    base = segment.split("#", 1)[0]
    return "%s.%s" % (prefix, base) if prefix else base


def summarise_access(schema):
    """Group a schema's properties by access type.

    ``unknown`` collects properties whose ``rfs`` entry carried no
    ``ReadWrite`` at all - reported rather than assumed readable or writable.
    """
    buckets = {"RW": [], "RO": [], "WO": [], "unknown": []}
    for prop, meta in sorted(schema.items()):
        access = (meta.get("ReadWrite") or "").upper()
        buckets.get(access, buckets["unknown"]).append(prop)
    return buckets


def survey(client, roots=None, max_instances_per_type=1, probe_constructor=True,
           max_children=None, max_depth=4, max_commands=None):
    """Walk `roots` and return a schema snapshot.

    Read-only: issues only ``get``, ``rfs`` and optionally ``constructor``.
    Returns a dict keyed by type path.

    Bounded on purpose. Every command costs at least the client's idle timeout,
    and the tree is deep enough that an unbounded breadth-first walk against a
    populated device is effectively a denial of afternoon. `max_depth` and
    `max_commands` are the brakes; widen them deliberately.
    """
    roots = list(discover_roots(client) if roots is None else roots)
    snapshot = {}
    seen_types = {}
    queue = [(root, 0) for root in roots]
    visited = set()
    commands = 0

    while queue:
        path, depth = queue.pop(0)
        if path in visited:
            continue
        if max_commands is not None and commands >= max_commands:
            break
        visited.add(path)

        type_path = type_of(path)
        count = seen_types.get(type_path, 0)
        if count >= max_instances_per_type:
            # Already have this type's schema. Still descend, because a
            # sibling may hold child types not present under the first
            # instance, but do not pay for another rfs.
            seen_types[type_path] = count + 1
        else:
            seen_types[type_path] = count + 1
            entry = _describe(client, path, type_path, depth, probe_constructor)
            snapshot[type_path] = entry
            commands += 2 if probe_constructor else 1

        if depth >= max_depth:
            continue
        children = client.children(path)
        commands += 1
        if max_children is not None:
            children = dict(list(children.items())[:max_children])
        for child_path in sorted(children):
            queue.append((child_path, depth + 1))

    for type_path, entry in snapshot.items():
        entry["instances_seen"] = seen_types.get(type_path, 0)
    return snapshot


def _describe(client, path, type_path, depth, probe_constructor):
    schema = client.rfs(path)
    entry = {
        "type": type_path,
        "sampled_instance": path,
        "depth": depth,
        "schema": schema,
        "access": summarise_access(schema),
    }
    entry["write_only"] = entry["access"]["WO"]

    if probe_constructor:
        # Constructor is a HIDDEN PROPERTY, not an operator. It does not appear
        # in an object's property list (confirmed: absent from all 225 distinct
        # properties across 65 surveyed types) and must be requested by name.
        # Issuing `constructor <path>` as a verb instead returns
        # `error <path> $OP=constructor $STATUS="Unsupported Operation."`,
        # because it is not in the operator set.
        #
        # This matters more than it looks: the vendor documentation defines
        # INIT as `{ConstructorProperty}={Value}`, so the init parameter names
        # for a type ARE this property's names. That explains why
        # UdpSysLogWriter takes `name=`/`ip=` while MemorySlot takes
        # `SlotName=`/`Persistent=` - the vocabulary is per-type, and this is
        # how to discover it rather than guess.
        entry["constructor"] = client.constructor(path)
    return entry


def unverifiable_properties(snapshot):
    """Every write-only property found, as ``[(type_path, property)]``.

    These are the properties no reconciler can honestly claim to have set,
    because they cannot be read back on a protocol that does not acknowledge
    writes.
    """
    out = []
    for type_path, entry in sorted(snapshot.items()):
        for prop in entry.get("write_only", []):
            out.append((type_path, prop))
    return out
