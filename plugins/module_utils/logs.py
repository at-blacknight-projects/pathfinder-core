# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reconciler logic for the ``Logs#0`` subtree.

Layer 2. Reading, planning and diffing are separated from applying so the
planner is a pure function of (desired, actual) and can be unit-tested without
a device. That matters more here than usual, because on this protocol a write
produces no evidence that it worked.

Identity and immutability
-------------------------
A **writer** is identified by ``(name, uri)``. ``Name`` and ``RemoteEndpointUri``
are both read-only once the object exists, so a change to either is a
delete-and-recreate, not an update. Deleting a live writer stops log delivery,
so this module refuses by default and asks to be told explicitly - see
``on_immutable_change``. The estate procedure is additive anyway: stand up the
new writer alongside, confirm delivery, then remove the old one.

A **subscription** is identified by its ``SubscriptionTypeId``. Its
``Subscription`` expression is writable, so it updates in place. ``Severity``
and ``CustomName`` are read-only, so a change to either is a delete-and-recreate
- but a subscription carries no state and recreating one loses nothing, so that
replacement happens automatically. The asymmetry with writers is deliberate.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

try:
    from .sapv2 import (
        SapV2VerifyError,
        join_path,
        normalise_path,
        quote_path_segment,
    )
except ImportError:  # pragma: no cover - direct file import in unit tests
    from sapv2 import (  # type: ignore
        SapV2VerifyError,
        join_path,
        normalise_path,
        quote_path_segment,
    )


LOGS_ROOT = "Logs#0"
UDP_WRITER_TYPE = "UdpSysLogWriter"
MESSAGE_LOG_SETTINGS = "MessageLogSettings#0"
SUBSCRIPTION_TYPE = "LogSubscription"
LOG_ROTATOR = "LogRotator#0"

#: Syslog on PathfinderCore is UDP and port 514 is fixed. The GUI asks only for
#: an IP; the API exposes a full URI, and the device builds it from ``ip=``.
SYSLOG_PORT = 514

#: A TcpClientWriter has no fixed port, but one is mandatory at creation - the
#: init no-ops without it. This is only a default for callers that omit it.
TCP_CLIENT_PORT = 1515


class WriterType(object):
    """What the module may do with one class of log writer.

    Each attribute is measured, not assumed. ``Constructor`` - the hidden
    property that reveals a type's init parameters elsewhere in the tree -
    returns nothing for the entire ``Logs#0`` family on every firmware tested,
    so all of this had to be established empirically.
    """

    def __init__(self, key, sap_type, creatable, init_params, writable_properties=(),
                 endpoint_property=None, endpoint_template=None, notes="",
                 required_params=(), ignored_params=()):
        self.key = key
        self.sap_type = sap_type
        #: Whether ``init`` is known to work for this type. False means the
        #: module can manage and delete an existing object but never make one.
        self.creatable = creatable
        #: Callable(desired) -> ordered (name, value) init parameters.
        self.init_params = init_params
        #: Properties writable on the writer object itself, after creation.
        self.writable_properties = tuple(writable_properties)
        #: Property holding the destination, if the type has one. Read-only
        #: wherever it exists, so a change means delete-and-recreate.
        self.endpoint_property = endpoint_property
        #: Callable(desired) -> the endpoint value the device will build.
        self.endpoint_template = endpoint_template
        #: Parameters `init` needs for this type. Missing one produces a
        #: broken object rather than an error, so validate up front.
        self.required_params = tuple(required_params)
        #: Parameters the device accepts and ignores for this type.
        #: Rejected rather than silently dropped, so a playbook that sets
        #: one is told instead of believing it took effect.
        self.ignored_params = tuple(ignored_params)
        self.notes = notes

    @property
    def replaceable(self):
        """Whether a delete-and-recreate is even possible for this type.

        A type that cannot be created must never be deleted to 'fix' drift:
        the delete would succeed and the recreate would silently no-op,
        destroying a working writer with no way back.
        """
        return self.creatable


WRITER_TYPES = {
    # Proven: `init Logs#0.UdpSysLogWriter name=<n>,ip=<bare ip>`. The device
    # builds udp://<ip>:514/ itself. No property on the writer is writable
    # after creation - configuration lives in its subscriptions and settings.
    "udp_syslog": WriterType(
        key="udp_syslog",
        sap_type=UDP_WRITER_TYPE,
        creatable=True,
        init_params=lambda d: [("name", d["name"]), ("ip", d["ip"])],
        writable_properties=(),
        endpoint_property="RemoteEndpointUri",
        endpoint_template=lambda d: ("udp://%s:%d/" % (d["ip"], SYSLOG_PORT)) if d.get("ip") else None,
        required_params=("ip",),
        ignored_params=("port",),
        notes="Syslog is UDP-only with port 514 fixed.",
    ),

    # Creatable after all, and the correction matters: this was recorded as
    # impossible on the strength of nine parameter vocabularies that all
    # silently no-op'd - ip+port, uri, remoteendpointuri, the object's own
    # property names, host+port, address, endpoint, url, and ip alone.
    #
    # Every one of those was missing `autoReconnect`, which is load-bearing.
    # Measured with one name per attempt so nothing was confounded by a
    # delete-then-recreate:
    #
    #   name,ip,port                        -> no-op
    #   name,ip,port (ip quoted)            -> no-op
    #   name,ip,autoReconnect   (no port)   -> no-op
    #   name,port,autoReconnect (no ip)     -> no-op
    #   name,autoReconnect                  -> no-op
    #   name,ip,port,autoReconnect          -> CREATED, quoted or bare ip
    #
    # So all four are required together; any one missing is a silent no-op.
    # `autoReconnect` is init-only - it is accepted and does not appear as a
    # property afterwards. The device builds RemoteEndpointUri from ip+port,
    # exactly as UdpSysLogWriter does from ip.
    #
    # ⚠️ `name=` ALONE also creates something, with RemoteEndpointUri pointing
    # at a stale address and port 0. Same zombie shape as a tcp_listener made
    # without a port, so required_params refuses it.
    #
    # ⚠️ Creating one makes the device attempt the outbound connection
    # immediately, and reads QUEUE BEHIND THAT - measured at ~30s against an
    # unreachable endpoint, far past the default read_timeout. A read-back
    # taken straight afterwards returns empty and looks exactly like a failed
    # create. See verify().
    "tcp_client": WriterType(
        key="tcp_client",
        sap_type="TcpClientWriter",
        creatable=True,
        init_params=lambda d: [("name", d["name"]), ("ip", d["ip"]),
                               ("port", d.get("port") or TCP_CLIENT_PORT),
                               ("autoReconnect", True)],
        writable_properties=("GetOnConnect", "OfflineMaxCacheCount"),
        endpoint_property="RemoteEndpointUri",
        endpoint_template=lambda d: ("tcp://%s:%s/" % (d["ip"], d.get("port") or TCP_CLIENT_PORT)) if d.get("ip") else None,
        required_params=("ip",),
        notes=("Plain text with no header, tag or in-band identity, so no "
               "attribution survives SNAT - prefer udp_syslog for new work. "
               "Creating one blocks the device's reads while it dials out."),
    ),

    # The other TCP option, and the opposite of tcp_client: the device
    # LISTENS and a collector connects in. Creatable, unlike tcp_client.
    #
    # `port=` is MANDATORY. `init ... name=<n>` alone still creates the object,
    # but a zombie: it has no properties at all, exposes no schema, and cannot
    # be configured afterwards - only deleted. So the module refuses to create
    # one without a port rather than leaving that behind.
    #
    # `ip=` is accepted and ignored; EndpointId is always 0.0.0.0:<port>.
    "tcp_listener": WriterType(
        key="tcp_listener",
        sap_type="TcpListenerWriter",
        creatable=True,
        init_params=lambda d: [("name", d["name"]), ("port", d["port"])],
        writable_properties=("GetOnConnect", "Listening", "OfflineMaxCacheCount"),
        endpoint_property="EndpointId",
        endpoint_template=lambda d: ("0.0.0.0:%s" % d["port"]) if d.get("port") else None,
        required_params=("port",),
        ignored_params=("ip",),
        notes=("Binds all interfaces. Plain text like tcp_client, so no "
               "in-band identity - prefer udp_syslog for new work."),
    ),

    # Proven here for the first time: `init Logs#0.LogFileWriter name=<file>`.
    # `Name=` works too, so init parameter names are case-insensitive. The
    # object itself has NO writable properties at all - even
    # UserLogFileDirectory is read-only, defaulting to /var/log/user - so
    # configuring one means its subscriptions and MessageLogSettings.
    "log_file": WriterType(
        key="log_file",
        sap_type="LogFileWriter",
        creatable=True,
        init_params=lambda d: [("name", d["name"])],
        writable_properties=(),
        endpoint_property=None,
        endpoint_template=None,
        ignored_params=("ip", "port"),
        notes=("Writes locally on the device. A filename containing '.' is "
               "addressed bracket-quoted, which the device does itself."),
    ),
}

DEFAULT_WRITER_TYPE = "udp_syslog"


def writer_type(key):
    try:
        return WRITER_TYPES[key or DEFAULT_WRITER_TYPE]
    except KeyError:
        raise KeyError("unknown writer type %r; known: %s"
                       % (key, ", ".join(sorted(WRITER_TYPES))))


#: The only valid values for the four direction-valued MessageLogSettings
#: properties. ``In`` and ``Out`` look plausible, are accepted on the wire, and
#: are silently ignored - this constant exists to catch that at plan time
#: rather than after a confusing no-op.
DIRECTION_VALUES = ("None", "Incoming", "Outgoing", "Both")

DIRECTION_PROPERTIES = ("Lwrp", "Lwcp", "SapV2Internal", "SapV2External")

BOOLEAN_PROPERTIES = (
    "AuditGet", "AuditSet", "LoginFailures", "LoginSuccesses", "AccessViolations",
    # Suppresses SAP traffic originating from the device's own web client.
    # Found by diffing the module's property list against an `rfs` of a real
    # MessageLogSettings object rather than by being asked for - it is a volume
    # control on a writer that is otherwise all-or-nothing per protocol.
    "SkipWebClientSapMessages",
)

MESSAGE_LOG_PROPERTIES = DIRECTION_PROPERTIES + BOOLEAN_PROPERTIES

SEVERITY_VALUES = ("Informational", "Warning", "Error", "Critical", "Debug")

_WHITESPACE_RE = re.compile(r"\s+")


def expected_uri(ip, port=SYSLOG_PORT):
    """The RemoteEndpointUri the device will build from a bare ``ip=``."""
    return "udp://%s:%d/" % (ip, port)


def writer_path(name, wtype=DEFAULT_WRITER_TYPE):
    return join_path(LOGS_ROOT,
                     writer_type(wtype).sap_type + quote_path_segment(name))


def subscription_path(writer_name, typeid, wtype=DEFAULT_WRITER_TYPE):
    return join_path(writer_path(writer_name, wtype),
                     SUBSCRIPTION_TYPE + quote_path_segment(typeid))


def settings_path(writer_name, wtype=DEFAULT_WRITER_TYPE):
    return join_path(writer_path(writer_name, wtype), MESSAGE_LOG_SETTINGS)


def rotator_path():
    return join_path(LOGS_ROOT, LOG_ROTATOR)


def normalise_expression(value):
    """Collapse whitespace for comparison, preserving case.

    Case is significant: the device stores subscription expressions verbatim
    (the estate catalogue contains both ``sub`` and ``SUB`` forms, each read
    back off real hardware), so case-folding here would mask a genuine diff and
    then fail verification instead.
    """
    return _WHITESPACE_RE.sub(" ", (value or "").strip())


def normalise_scalar(value):
    """Render a desired value the way the device reports it back."""
    if isinstance(value, bool):
        return "True" if value else "False"
    text = str(value).strip()
    # A boolean that arrived as a string - from an untyped dict suboption, or a
    # Jinja expression that stringified on the way in - would otherwise never
    # compare equal to the device's "True"/"False". The module would then plan
    # the same write on every run, send it, read back a value it still
    # considered different, and fail verification. Reporting a permanent
    # non-convergence for a device that is in fact correct is the worst of the
    # available failure modes, so fold the case here. No property on this
    # device holds a free-text value where "true" means anything else.
    if text.lower() in ("true", "false"):
        return text.capitalize()
    return text


def validate_message_log_settings(settings):
    """Return a list of human-readable problems, empty if the settings are sane."""
    problems = []
    for key, value in (settings or {}).items():
        if key not in MESSAGE_LOG_PROPERTIES:
            problems.append(
                "unknown MessageLogSettings property %r (known: %s)"
                % (key, ", ".join(MESSAGE_LOG_PROPERTIES)))
        elif key in DIRECTION_PROPERTIES:
            if normalise_scalar(value) not in DIRECTION_VALUES:
                problems.append(
                    "MessageLogSettings.%s=%r is invalid. Valid values are %s. "
                    "Note 'In' and 'Out' are silently ignored by the device "
                    "rather than rejected."
                    % (key, value, ", ".join(DIRECTION_VALUES)))
    return problems


def validate_writer(desired):
    """Return human-readable problems with the desired writer, empty if sane."""
    problems = []
    try:
        wtype = writer_type(desired.get("type"))
    except KeyError as exc:
        return [str(exc)]

    if desired.get("state", "present") == "present":
        for param in wtype.required_params:
            if not desired.get(param):
                problems.append(
                    "writer type %r requires %r. Creating a %s without it does "
                    "not fail - the device makes an object with no properties "
                    "at all, which can then only be deleted."
                    % (wtype.key, param, wtype.sap_type))
        for param in wtype.ignored_params:
            if desired.get(param):
                problems.append(
                    "writer type %r ignores %r; the device accepts it and does "
                    "nothing with it, so setting it here would be misleading."
                    % (wtype.key, param))

    for key in (desired.get("properties") or {}):
        if key not in wtype.writable_properties:
            problems.append(
                "%r is not writable on a %s (writable: %s). Setting a "
                "read-only property is silently ignored by the device."
                % (key, wtype.sap_type,
                   ", ".join(wtype.writable_properties) or "none"))
    return problems


def validate_subscriptions(subscriptions):
    """Return a list of human-readable problems with the desired catalogue."""
    problems = []
    seen = {}
    for sub in subscriptions or []:
        typeid = sub.get("typeid")
        if typeid is None:
            problems.append("subscription with no typeid: %r" % (sub,))
            continue
        if typeid in seen:
            problems.append(
                "duplicate typeid %s - SubscriptionTypeId is the identity key, "
                "so two entries with the same id cannot both exist" % typeid)
        seen[typeid] = sub
        if not sub.get("subscription"):
            problems.append("subscription %s has no expression" % typeid)
        severity = sub.get("severity")
        if severity and severity not in SEVERITY_VALUES:
            problems.append(
                "subscription %s severity %r is not one of %s"
                % (typeid, severity, ", ".join(SEVERITY_VALUES)))
    return problems


def validate_writers(writers):
    """Validate a whole writer list, including conflicts between its entries.

    Per-entry problems are prefixed with the index and name so a list of a
    dozen writers reports which one is wrong. Everything here is checked
    before a session is opened: on this protocol an invalid write is accepted
    and ignored rather than refused, so the only cheap place to catch a bad
    request is before it is sent.
    """
    problems = []
    seen = {}
    for index, entry in enumerate(writers or []):
        label = "writers[%d]" % index
        name = entry.get("name")
        if not name:
            problems.append("%s has no name" % label)
            continue
        try:
            wtype = writer_type(entry.get("type"))
        except KeyError as exc:
            problems.append("%s (%s): %s" % (label, name, exc))
            continue

        # Identity is (type, name), not name alone: the four types live at
        # different paths, so one name under two types is two distinct objects.
        # The same pair twice is not - the second entry would plan against the
        # first entry's writes and the outcome would depend on list order.
        key = (wtype.key, name)
        if key in seen:
            problems.append(
                "%s duplicates writers[%d]: %s %r appears twice. A writer is "
                "identified by (type, name), so the second entry would be "
                "planned against the first entry's writes."
                % (label, seen[key], wtype.key, name))
        seen[key] = index

        for problem in (validate_writer(entry)
                        + validate_subscriptions(entry.get("subscriptions"))
                        + validate_message_log_settings(
                            entry.get("message_log_settings"))):
            problems.append("%s (%s): %s" % (label, name, problem))
    return problems


# -- actions -------------------------------------------------------------

class Action(object):
    """One planned change. `destructive` drives confirmation and reporting."""

    def __init__(self, kind, summary, destructive=False, detail=None):
        self.kind = kind
        self.summary = summary
        self.destructive = destructive
        self.detail = detail or {}

    def to_dict(self):
        out = {"action": self.kind, "summary": self.summary,
               "destructive": self.destructive}
        out.update(self.detail)
        return out

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<Action %s: %s>" % (self.kind, self.summary)


def read_actual(client, writer_name, wtype=DEFAULT_WRITER_TYPE):
    """Read the current state of one writer into a plain dict.

    ONE command. ``$MAX_DEPTH=-1`` returns the writer, every subscription under
    it and its MessageLogSettings in a single reply, so this does not grow with
    the subscription count. Read child-by-child it was ``3 + N`` commands, each
    costing a framing wait - 30 of them for the estate's 27-subscription
    catalogue, on both the planning read and the verification read.

    Falls back to the per-object reads if the deep read comes back with only
    the writer itself, which is what a firmware without ``$MAX_DEPTH`` would
    produce.

    Returns ``{"exists": False}`` when the writer is absent, which is a
    different thing from a writer that exists with no subscriptions.
    """
    path = writer_path(writer_name, wtype)
    absent = {"exists": False, "properties": {}, "subscriptions": {},
              "message_log_settings": {}}

    tree = client.tree(path) if hasattr(client, "tree") else {}
    properties = _match(tree, path)
    if not properties:
        # Either the writer is gone, or this client cannot do a deep read.
        properties = client.get(path)
        if not properties:
            return absent
        tree = {}

    settings = _match(tree, settings_path(writer_name, wtype))
    subscriptions = {}
    for obj_path, props in tree.items():
        segment = obj_path.rsplit(".", 1)[-1]
        if segment.startswith(SUBSCRIPTION_TYPE + "#"):
            subscriptions[segment.split("#", 1)[1].strip("[]")] = props

    if not tree:
        # Fallback path: a listing carries paths only, so each child still
        # needs its own read.
        for child_path in client.children(path):
            segment = child_path.rsplit(".", 1)[-1]
            if segment.startswith(SUBSCRIPTION_TYPE + "#"):
                typeid = segment.split("#", 1)[1].strip("[]")
                subscriptions[str(typeid)] = client.get(child_path)
        settings = client.get(settings_path(writer_name, wtype))

    return {"exists": True, "properties": properties,
            "subscriptions": subscriptions, "message_log_settings": settings}


def _match(tree, path):
    """One object out of a deep read, matched on path and nothing else."""
    if path in tree:
        return tree[path]
    wanted = normalise_path(path)
    for obj_path, props in tree.items():
        if normalise_path(obj_path) == wanted:
            return props
    return {}


def plan(desired, actual, on_immutable_change="fail", purge_subscriptions=False):
    """Compute the ordered list of actions to bring `actual` to `desired`.

    Pure: no client, no I/O. `desired` is one validated writer entry, `actual`
    is :func:`read_actual` output for it.
    """
    wtype = writer_type(desired.get("type"))
    actions = _plan_writer(desired, actual, on_immutable_change, purge_subscriptions)
    # Stamp every action with the writer it belongs to. The module flattens
    # the per-writer plans into one ordered list for the device, and `summary`
    # alone is not enough for a caller to tell two writers apart or to filter
    # the plan down to one of them.
    for action in actions:
        action.detail.setdefault("writer", desired["name"])
        action.detail.setdefault("type", wtype.key)
        action.detail.setdefault("scope", "writer")
    return actions


def _plan_writer(desired, actual, on_immutable_change, purge_subscriptions):
    actions = []
    name = desired["name"]
    state = desired.get("state", "present")
    wtype = writer_type(desired.get("type"))

    if state == "absent":
        if actual["exists"]:
            warning = ""
            if not wtype.creatable:
                warning = (" NOTE: a %s cannot be recreated over SapV2, so this "
                           "is one-way." % wtype.sap_type)
            actions.append(Action(
                "writer_delete",
                "delete %s %s (and its subscriptions).%s"
                % (wtype.sap_type, name, warning),
                destructive=True,
                detail={"writer": name, "type": wtype.key,
                        "path": writer_path(name, wtype.key)}))
        return actions

    recreate = False

    if not actual["exists"]:
        if not wtype.creatable:
            # Refuse rather than emit an init that is known to no-op. A silent
            # no-op followed by a read-back failure would be correct but
            # baffling; say why up front.
            actions.append(Action(
                "blocked_uncreatable",
                "%s %s does not exist and cannot be created over SapV2. Every "
                "known init parameter form is silently ignored by the device, "
                "so this writer has to be created in the GUI. %s"
                % (wtype.sap_type, name, wtype.notes),
                detail={"writer": name, "type": wtype.key}))
            return actions
        endpoint = wtype.endpoint_template(desired) if wtype.endpoint_template else None
        actions.append(Action(
            "writer_create",
            "create %s %s%s" % (wtype.sap_type, name,
                                (" -> " + endpoint) if endpoint else ""),
            detail={"writer": name, "type": wtype.key}))
        recreate = True
    elif wtype.endpoint_property and wtype.endpoint_template(desired):
        wanted_uri = wtype.endpoint_template(desired)
        current_uri = actual["properties"].get(wtype.endpoint_property, "")
        if normalise_expression(current_uri) != normalise_expression(wanted_uri):
            # The endpoint property is read-only on every writer type, so
            # there is no update path - only delete and recreate, which drops
            # log delivery for the length of the gap.
            if on_immutable_change == "replace" and wtype.replaceable:
                actions.append(Action(
                    "writer_replace",
                    "replace %s %s: endpoint %s -> %s (delete + recreate; %s is "
                    "read-only)" % (wtype.sap_type, name, current_uri, wanted_uri,
                                    wtype.endpoint_property),
                    destructive=True,
                    detail={"writer": name, "type": wtype.key,
                            "from": current_uri, "to": wanted_uri}))
                recreate = True
            elif on_immutable_change == "replace" and not wtype.replaceable:
                # The important rail: never delete what cannot be recreated.
                # The delete would succeed, the recreate would silently no-op,
                # and a working writer would be gone with no way back.
                actions.append(Action(
                    "blocked_immutable",
                    "%s %s endpoint is %s but %s is wanted, and %s cannot be "
                    "recreated over SapV2. Replacing it would delete a working "
                    "writer that nothing can rebuild, so this is refused even "
                    "with on_immutable_change=replace. Re-point it in the GUI, "
                    "or stand up a udp_syslog writer alongside and retire this "
                    "one." % (wtype.sap_type, name, current_uri, wanted_uri,
                              wtype.sap_type),
                    destructive=True,
                    detail={"writer": name, "type": wtype.key,
                            "from": current_uri, "to": wanted_uri}))
                return actions
            else:
                actions.append(Action(
                    "blocked_immutable",
                    "%s %s endpoint is %s but %s is wanted. %s is read-only, so "
                    "this needs a delete and recreate, which interrupts log "
                    "delivery. Set on_immutable_change=replace to allow it, or "
                    "preferably create the new writer under a different name, "
                    "confirm delivery, then remove the old one."
                    % (wtype.sap_type, name, current_uri, wanted_uri,
                       wtype.endpoint_property),
                    destructive=True,
                    detail={"writer": name, "type": wtype.key,
                            "from": current_uri, "to": wanted_uri}))
                return actions

    actions.extend(_plan_writer_properties(desired, actual, wtype))
    actions.extend(_plan_subscriptions(desired, actual, recreate, purge_subscriptions))
    actions.extend(_plan_settings(desired, actual, recreate))
    return actions


def _plan_writer_properties(desired, actual, wtype):
    """Properties writable on the writer object itself.

    Only TcpClientWriter has any (GetOnConnect, OfflineMaxCacheCount);
    UdpSysLogWriter and LogFileWriter expose none, so this is usually empty.
    """
    wanted = desired.get("properties") or {}
    if not wanted:
        return []
    changes = {}
    for key in wtype.writable_properties:
        if key not in wanted:
            continue
        want = normalise_scalar(wanted[key])
        if normalise_scalar(actual["properties"].get(key, "")) != want:
            changes[key] = want
    if not changes:
        return []
    return [Action(
        "writer_properties_set",
        "set %s on %s: %s" % (wtype.sap_type, desired["name"],
                              ", ".join("%s=%s" % kv for kv in sorted(changes.items()))),
        detail={"writer": desired["name"], "type": wtype.key, "changes": changes})]


def _plan_subscriptions(desired, actual, recreate, purge):
    actions = []
    name = desired["name"]
    wanted = dict((str(s["typeid"]), s) for s in desired.get("subscriptions") or [])
    current = {} if recreate else actual.get("subscriptions", {})

    for typeid in sorted(wanted, key=int):
        sub = wanted[typeid]
        existing = current.get(typeid)
        if existing is None:
            actions.append(Action(
                "subscription_create",
                "create subscription %s (%s) on %s"
                % (typeid, sub.get("customname") or "unnamed", name),
                detail={"writer": name, "typeid": typeid}))
            continue

        # Severity and CustomName are read-only after creation. Unlike a
        # writer, a subscription holds no state and recreating one costs
        # nothing, so drift on a frozen field is repaired automatically.
        frozen = []
        for key, prop in (("severity", "Severity"), ("customname", "CustomName")):
            want = sub.get(key)
            if want is None:
                continue
            if normalise_scalar(want) != normalise_scalar(existing.get(prop, "")):
                frozen.append("%s %r -> %r" % (prop, existing.get(prop, ""), want))
        if frozen:
            actions.append(Action(
                "subscription_replace",
                "replace subscription %s on %s: %s (read-only fields, so "
                "delete + recreate)" % (typeid, name, "; ".join(frozen)),
                detail={"writer": name, "typeid": typeid, "changes": frozen}))
            continue

        want_expr = normalise_expression(sub.get("subscription"))
        have_expr = normalise_expression(existing.get("Subscription"))
        if want_expr != have_expr:
            actions.append(Action(
                "subscription_update",
                "update subscription %s expression on %s" % (typeid, name),
                detail={"writer": name, "typeid": typeid,
                        "from": have_expr, "to": want_expr}))

    if purge:
        for typeid in sorted(set(current) - set(wanted), key=int):
            actions.append(Action(
                "subscription_delete",
                "delete unmanaged subscription %s from %s" % (typeid, name),
                destructive=True,
                detail={"writer": name, "typeid": typeid}))
    return actions


def _plan_settings(desired, actual, recreate):
    wanted = desired.get("message_log_settings") or {}
    if not wanted:
        return []
    # A freshly created writer's MessageLogSettings default to everything
    # off/None, so after a create every requested value is a change.
    current = {} if recreate else actual.get("message_log_settings", {})
    changes = {}
    for key in MESSAGE_LOG_PROPERTIES:
        if key not in wanted:
            continue
        want = normalise_scalar(wanted[key])
        if normalise_scalar(current.get(key, "")) != want:
            changes[key] = want
    if not changes:
        return []
    return [Action(
        "settings_set",
        "set MessageLogSettings on %s: %s"
        % (desired["name"], ", ".join("%s=%s" % kv for kv in sorted(changes.items()))),
        detail={"writer": desired["name"], "changes": changes})]


# -- apply ---------------------------------------------------------------

def apply_plan(client, desired, actions):
    """Execute `actions` in order. Returns nothing; call :func:`verify` after.

    Deliberately returns no success indication. SapV2 acknowledges nothing, so
    any value returned from here would be a lie dressed as a result.
    """
    name = desired["name"]
    wtype = writer_type(desired.get("type"))
    subs_by_id = dict((str(s["typeid"]), s) for s in desired.get("subscriptions") or [])

    for action in actions:
        # Blocked actions are diagnostics, not work. They exist so the plan can
        # explain itself; executing one is exactly what must not happen.
        if action.kind.startswith("blocked_"):
            continue
        if action.kind == "writer_delete":
            client.delete(writer_path(name, wtype.key))
        elif action.kind == "writer_replace":
            client.delete(writer_path(name, wtype.key))
            _create_writer(client, desired, wtype)
        elif action.kind == "writer_create":
            _create_writer(client, desired, wtype)
        elif action.kind == "writer_properties_set":
            client.set(writer_path(name, wtype.key),
                       sorted(action.detail["changes"].items()))
        elif action.kind == "subscription_create":
            _create_subscription(client, name, subs_by_id[action.detail["typeid"]],
                                 wtype)
        elif action.kind == "subscription_replace":
            client.delete(subscription_path(name, action.detail["typeid"], wtype.key))
            _create_subscription(client, name, subs_by_id[action.detail["typeid"]],
                                 wtype)
        elif action.kind == "subscription_update":
            client.set(subscription_path(name, action.detail["typeid"], wtype.key),
                       [("Subscription", action.detail["to"])])
        elif action.kind == "subscription_delete":
            client.delete(subscription_path(name, action.detail["typeid"], wtype.key))
        elif action.kind == "settings_set":
            client.set(settings_path(name, wtype.key),
                       sorted(action.detail["changes"].items()))


def _create_writer(client, desired, wtype):
    # Init parameters are the device's own, not the object's property names,
    # and they differ per type: a UDP writer takes name= plus a BARE ip= (the
    # device builds the udp://<ip>:514/ URI itself), while a LogFileWriter
    # takes only name=, the filename. Using property names such as
    # RemoteEndpointUri= is a silent no-op.
    if not wtype.creatable:
        raise SapV2VerifyError(
            "refusing to create a %s: the device silently ignores every known "
            "init form for this type. %s" % (wtype.sap_type, wtype.notes))
    client.init(join_path(LOGS_ROOT, wtype.sap_type), wtype.init_params(desired))


def _create_subscription(client, writer_name, sub, wtype):
    # severity and customname are settable ONLY here: both are read-only once
    # the object exists.
    params = [("subscription", sub["subscription"]), ("typeid", sub["typeid"])]
    if sub.get("severity"):
        params.append(("severity", sub["severity"]))
    if sub.get("customname"):
        params.append(("customname", sub["customname"]))
    client.init(join_path(writer_path(writer_name, wtype.key), SUBSCRIPTION_TYPE),
                params)


# -- verify --------------------------------------------------------------

def verify(client, desired, purge_subscriptions=False):
    """Re-read the device and assert the desired state actually took.

    This is not optional belt-and-braces. SapV2 returns nothing for both a
    successful write and a rejected one, so this read-back is the only thing
    standing between "reported changed" and "actually changed". Raises
    :class:`SapV2VerifyError` listing every discrepancy.
    """
    name = desired["name"]
    state = desired.get("state", "present")
    wtype = writer_type(desired.get("type"))
    actual = read_actual(client, name, wtype.key)

    if state == "absent":
        if actual["exists"]:
            raise SapV2VerifyError(
                "%s %s still exists after delete. SapV2 does not report write "
                "failures, so this is what a rejected delete looks like."
                % (wtype.sap_type, name))
        return actual

    problems = []
    if not actual["exists"]:
        raise SapV2VerifyError(
            "%s %s does not exist after create. The most common cause is init "
            "parameter shape: parameters are comma-separated and use the "
            "device's own per-type names (a UDP writer takes name= and ip=, a "
            "LogFileWriter takes name=), not the object's property names."
            % (wtype.sap_type, name))

    if wtype.endpoint_property and wtype.endpoint_template(desired):
        wanted_uri = wtype.endpoint_template(desired)
        have_uri = actual["properties"].get(wtype.endpoint_property, "")
        if normalise_expression(have_uri) != normalise_expression(wanted_uri):
            problems.append("%s is %r, wanted %r"
                            % (wtype.endpoint_property, have_uri, wanted_uri))

    # Only TcpClientWriter has writable own-properties; for the other types
    # this loop is empty.
    for key, want in (desired.get("properties") or {}).items():
        if key not in wtype.writable_properties:
            continue
        have = actual["properties"].get(key, "")
        if normalise_scalar(have) != normalise_scalar(want):
            problems.append("%s.%s is %r, wanted %r"
                            % (wtype.sap_type, key, have, want))

    for sub in desired.get("subscriptions") or []:
        typeid = str(sub["typeid"])
        existing = actual["subscriptions"].get(typeid)
        if existing is None:
            problems.append("subscription %s missing" % typeid)
            continue
        checks = [("Subscription", sub.get("subscription"))]
        if sub.get("severity"):
            checks.append(("Severity", sub["severity"]))
        if sub.get("customname"):
            checks.append(("CustomName", sub["customname"]))
        for prop, want in checks:
            if want is None:
                continue
            have = existing.get(prop, "")
            if normalise_expression(str(want)) != normalise_expression(str(have)):
                problems.append("subscription %s %s is %r, wanted %r"
                                % (typeid, prop, have, want))

    if purge_subscriptions:
        wanted_ids = set(str(s["typeid"]) for s in desired.get("subscriptions") or [])
        for typeid in sorted(set(actual["subscriptions"]) - wanted_ids):
            problems.append("subscription %s still present after purge" % typeid)

    for key, want in (desired.get("message_log_settings") or {}).items():
        have = actual["message_log_settings"].get(key, "")
        if normalise_scalar(have) != normalise_scalar(want):
            problems.append(
                "MessageLogSettings.%s is %r, wanted %r%s"
                % (key, have, want,
                   " (note: 'In'/'Out' are silently ignored; valid values are "
                   "None/Incoming/Outgoing/Both)" if key in DIRECTION_PROPERTIES else ""))

    if problems:
        raise SapV2VerifyError(
            "read-back verification failed on %s after writing. SapV2 never "
            "reports a rejected write, so these are writes that were sent and "
            "silently did not take: %s" % (name, "; ".join(problems)))
    return actual


# -- diff ----------------------------------------------------------------

def render_state(desired, actual, purge_subscriptions=False):
    """Render actual and desired as text, for Ansible's ``--diff``.

    Text rather than nested dicts: the interesting state is one writer, a list
    of subscription ids and a handful of settings, and a unified text diff of
    that reads far better than a structural diff of nested objects.
    """
    wtype = writer_type(desired.get("type"))
    path = writer_path(desired["name"], wtype.key)

    def block(exists, properties, subscriptions, settings):
        if not exists:
            return "# %s\n(absent)\n" % path
        lines = ["# %s" % path]
        if wtype.endpoint_property:
            lines.append("%s: %s" % (wtype.endpoint_property,
                                     properties.get(wtype.endpoint_property, "")))
        for key in wtype.writable_properties:
            if key in properties:
                lines.append("%s: %s" % (key, properties[key]))
        lines.append("subscriptions:")
        for typeid in sorted(subscriptions, key=int):
            sub = subscriptions[typeid]
            lines.append("  %-6s %-14s %-30s %s"
                         % (typeid, sub.get("Severity", ""),
                            sub.get("CustomName", ""),
                            sub.get("Subscription", "")))
        if not subscriptions:
            lines.append("  (none)")
        lines.append("message_log_settings:")
        for key in MESSAGE_LOG_PROPERTIES:
            if key in settings:
                lines.append("  %-18s %s" % (key, settings[key]))
        if not settings:
            lines.append("  (none)")
        return "\n".join(lines) + "\n"

    before = block(actual["exists"], actual["properties"],
                   actual["subscriptions"], actual["message_log_settings"])

    if desired.get("state") == "absent":
        return {"before": before, "after": "# %s\n(absent)\n" % path,
                "before_header": path, "after_header": path}

    want_props = dict(actual["properties"] if actual["exists"] else {})
    if wtype.endpoint_property and wtype.endpoint_template(desired):
        want_props[wtype.endpoint_property] = wtype.endpoint_template(desired)
    for key, value in (desired.get("properties") or {}).items():
        want_props[key] = normalise_scalar(value)

    want_subs = {}
    for sub in desired.get("subscriptions") or []:
        want_subs[str(sub["typeid"])] = {
            "Subscription": sub.get("subscription", ""),
            "Severity": sub.get("severity", ""),
            "CustomName": sub.get("customname", ""),
        }
    # Without purge, subscriptions already on the device are left alone, so
    # they belong in the "after" too. Omitting them would show a deletion the
    # module is not going to perform.
    if not purge_subscriptions:
        for typeid, sub in actual["subscriptions"].items():
            want_subs.setdefault(typeid, sub)

    want_settings = dict(actual["message_log_settings"])
    for key, value in (desired.get("message_log_settings") or {}).items():
        want_settings[key] = normalise_scalar(value)

    after = block(True, want_props, want_subs, want_settings)
    return {"before": before, "after": after,
            "before_header": path, "after_header": path}


# -- rotation (device-scoped, but part of Logs#0) -------------------------

#: Log rotation and cleanup. Every one of these lives under ``Logs#0``, so it
#: belongs to this module: each reconciler owns a subtree, and having the
#: advanced-options module reach in here would put two things on the same
#: properties.
#:
#: Note these are DEVICE-scoped while the rest of this module is WRITER-scoped.
#: Reconciling two writers on one device therefore applies rotation twice -
#: idempotent, so harmless, but two tasks asking for DIFFERENT rotation values
#: would fight and nothing here can detect that. Set it on one task.
#:
#: This is the complete set SapV2 exposes. There is no max-age or total-size
#: control; do not invent one.
ROTATION_PROPERTIES = {
    "max_file_size": ("Logs#0.LogRotator#0.RotateRule#0", "MaxFileSize", "num"),
    "max_count": ("Logs#0.LogRotator#0.RotateRule#0", "MaxCount", "num"),
    "minutes_between_search": ("Logs#0.LogRotator#0", "MinutesBetweenSearch", "num"),
    "skip_clean_logs": ("Logs#0", "SkipCleanLogs", "bool"),
    "check_rotation_after_max_writes": ("Logs#0", "CheckRotationAfterMaxWrites", "num"),
}


def validate_rotation(rotation):
    """Return human-readable problems with the requested rotation settings."""
    problems = []
    for key, value in (rotation or {}).items():
        spec = ROTATION_PROPERTIES.get(key)
        if spec is None:
            problems.append(
                "unknown rotation setting %r (known: %s)"
                % (key, ", ".join(sorted(ROTATION_PROPERTIES))))
            continue
        if spec[2] == "num":
            try:
                int(str(value))
            except (TypeError, ValueError):
                problems.append("rotation.%s expects a number, got %r" % (key, value))
    return problems


def rotation_paths():
    """The distinct objects rotation settings live on."""
    return sorted({path for path, _prop, _kind in ROTATION_PROPERTIES.values()})


def read_rotation(client):
    """Current values of every rotation object, as ``{path: {prop: value}}``."""
    return dict((path, client.get(path)) for path in rotation_paths())


def plan_rotation(rotation, actual):
    """Actions needed to reach the requested rotation settings.

    Pure. `actual` is :func:`read_rotation` output.
    """
    actions = []
    for key in sorted(rotation or {}):
        path, prop, _kind = ROTATION_PROPERTIES[key]
        want = normalise_scalar(rotation[key])
        properties = actual.get(path, {})
        if prop not in properties:
            actions.append(Action(
                "blocked_absent_property",
                "rotation.%s maps to %s.%s, which this device does not expose. "
                "Writing it would be accepted and ignored." % (key, path, prop),
                detail={"setting": key, "path": path, "property": prop,
                        "scope": "device"}))
            continue
        if normalise_scalar(properties.get(prop, "")) != want:
            actions.append(Action(
                "rotation_set",
                "set %s.%s: %s -> %s"
                % (path, prop, properties.get(prop, ""), want),
                detail={"setting": key, "path": path, "property": prop,
                        "from": normalise_scalar(properties.get(prop, "")),
                        "to": want, "scope": "device"}))
    return actions


def rotation_diff(rotation, actual):
    """Render rotation before/after for Ansible's ``--diff``.

    Rotation lives on three different objects under ``Logs#0`` and is
    device-scoped rather than per-writer, so it gets its own diff entry. Without
    one, a run whose only change is rotation reports ``changed`` against an
    empty diff, which reads like a bug in the module.
    """
    def block(values):
        lines = ["# %s rotation" % LOGS_ROOT]
        for key in sorted(ROTATION_PROPERTIES):
            lines.append("%-32s %s" % (key, values[key]))
        return "\n".join(lines) + "\n"

    before, after = {}, {}
    for key in ROTATION_PROPERTIES:
        path, prop, _kind = ROTATION_PROPERTIES[key]
        have = normalise_scalar(actual.get(path, {}).get(prop, ""))
        before[key] = have
        # Settings not asked for are left alone, so they belong unchanged in
        # the "after" - showing them as removed would advertise a write this
        # module is not going to make.
        after[key] = (normalise_scalar(rotation[key])
                      if key in (rotation or {}) else have)

    header = "%s rotation" % LOGS_ROOT
    return {"before": block(before), "after": block(after),
            "before_header": header, "after_header": header}


def apply_rotation(client, actions):
    """Execute rotation actions. Verify with :func:`verify_rotation` after."""
    for action in actions:
        if action.kind != "rotation_set":
            continue
        client.set(action.detail["path"], [(action.detail["property"],
                                            action.detail["to"])])


def verify_rotation(client, rotation):
    """Re-read and assert the rotation settings actually took."""
    actual = read_rotation(client)
    problems = []
    for key, value in (rotation or {}).items():
        path, prop, _kind = ROTATION_PROPERTIES[key]
        have = actual.get(path, {}).get(prop, "")
        if normalise_scalar(have) != normalise_scalar(value):
            problems.append("%s.%s is %r, wanted %r" % (path, prop, have, value))
    if problems:
        raise SapV2VerifyError(
            "rotation read-back failed. SapV2 never reports a rejected write, "
            "so these were sent and silently did not take: %s"
            % "; ".join(problems))
    return actual


def rotation_writes(rotation):
    """``[(path, property, value)]`` this module intends to write.

    Used to check the startup script for values it will undo at reboot.
    """
    out = []
    for key in sorted(rotation or {}):
        path, prop, _kind = ROTATION_PROPERTIES[key]
        out.append((path, prop, normalise_scalar(rotation[key])))
    return out


# -- the device as a whole ------------------------------------------------
#
# A PathfinderCore's writers are reconciled together rather than one task per
# writer. Three reasons, in increasing order of importance:
#
# 1. One session. SapV2 frames replies on an idle gap and a login costs several
#    seconds, so a loop over a single-writer module pays that per item.
# 2. One plan and one diff for the device, instead of a run of separate task
#    results that have to be read side by side to see what the device will end
#    up looking like.
# 3. Ordering. Standing up a replacement writer and retiring the one it
#    replaces is one intent, and it is only safe in one order. Separate tasks
#    cannot express "delete that one only if this one came up", because by the
#    time the delete task runs the create task has already reported success.

#: Keys a writers[] entry may either state itself or inherit from task level.
INHERITED_KEYS = ("state", "subscriptions", "subscriptions_purge",
                  "message_log_settings")


def resolve_writers(entries, defaults):
    """Fill each writer entry from the task-level defaults.

    An omitted key inherits; a key set to an empty list or dict does not. That
    distinction is why the module's argument spec gives these suboptions no
    default - Ansible fills an omitted suboption with ``None``, so ``None`` is
    the only available marker for "not stated here", and an explicit
    ``subscriptions: []`` on one writer has to mean none rather than "inherit
    the device's catalogue".
    """
    resolved = []
    for entry in entries or []:
        out = {
            "name": entry.get("name"),
            "type": entry.get("type") or DEFAULT_WRITER_TYPE,
            "ip": entry.get("ip"),
            "port": entry.get("port"),
            "properties": entry.get("properties") or {},
        }
        for key in INHERITED_KEYS:
            value = entry.get(key)
            out[key] = (defaults or {}).get(key) if value is None else value
        out["state"] = out["state"] or "present"
        out["subscriptions"] = out["subscriptions"] or []
        out["message_log_settings"] = out["message_log_settings"] or {}
        out["subscriptions_purge"] = bool(out["subscriptions_purge"])
        resolved.append(out)
    return resolved


class WriterPlan(object):
    """One writer's desired state, its state on the device, and the gap."""

    def __init__(self, desired, actual, actions):
        self.desired = desired
        self.actual = actual
        self.actions = actions
        #: Set once this writer has been written to AND read back successfully.
        #: Stays False for a converged writer: nothing was written, so there
        #: was nothing to verify, and saying otherwise would overstate it.
        self.verified = False

    @property
    def name(self):
        return self.desired["name"]

    @property
    def type_key(self):
        return self.desired.get("type") or DEFAULT_WRITER_TYPE

    @property
    def state(self):
        return self.desired.get("state", "present")

    @property
    def changed(self):
        return bool(self.actions)

    @property
    def purge(self):
        return self.desired.get("subscriptions_purge", False)

    @property
    def path(self):
        return writer_path(self.name, self.type_key)

    def diff(self):
        return render_state(self.desired, self.actual,
                            purge_subscriptions=self.purge)


class DevicePlan(object):
    """Everything one run intends to do to one device.

    Built by :func:`plan_device` from reads alone, so it is also the check-mode
    result: the plan, the diff and the drift report are the same object.
    """

    def __init__(self, writers, rotation, rotation_actual, rotation_actions,
                 unmanaged=None):
        self.writers = writers
        self.rotation = rotation
        self.rotation_actual = rotation_actual
        self.rotation_actions = rotation_actions
        #: Writers on the device that were not named, and what was done about
        #: them. Empty in the default `ignore` mode - which is not the same as
        #: "there were none", so the module says which it means.
        self.unmanaged = unmanaged or []

    @property
    def creating(self):
        return [w for w in self.writers if w.state == "present"]

    @property
    def deleting(self):
        return [w for w in self.writers if w.state == "absent"]

    @property
    def actions(self):
        """Every action, in the order :meth:`apply` will run them.

        Not input order. A plan that claims one order and executes another is
        worse than no plan at all.
        """
        return ([a for w in self.creating for a in w.actions]
                + self.rotation_actions
                + [a for w in self.deleting for a in w.actions])

    @property
    def blocked(self):
        """Diagnostic actions. Any of these means the run must not proceed."""
        return [a for a in self.actions if a.kind.startswith("blocked_")]

    @property
    def changed(self):
        return bool(self.actions)

    def diff(self):
        """Before/after blocks for Ansible's ``--diff``, one per changing object.

        Ansible renders a list of diffs natively, and per-object blocks read far
        better than one merged document when several writers change at once.
        """
        diffs = [w.diff() for w in self.creating + self.deleting if w.changed]
        if self.rotation_actions:
            diffs.append(rotation_diff(self.rotation, self.rotation_actual))
        return diffs

    def apply(self, client):
        """Apply the plan in three phases, returning phase-2 failures.

        1. Create and configure every ``present`` writer, then rotation.
        2. Read all of it back.
        3. Delete the ``absent`` writers - but only if step 2 found nothing
           wrong.

        The phase boundary is the point of the whole arrangement. A run that
        stands up a replacement writer and retires the one it replaces cannot
        delete the old writer on a run where the new one failed to appear. That
        is not a theoretical failure: on this protocol one wrong init parameter
        produces exactly that, and reports it as silence.

        A non-empty return therefore means phase 3 did NOT run and the device
        still holds everything the deletes would have removed. A phase-3
        failure raises instead, because nothing follows it to gate.
        """
        for writer in self.creating:
            apply_plan(client, writer.desired, writer.actions)
        apply_rotation(client, self.rotation_actions)

        failures = []
        for writer in self.creating:
            if not writer.changed:
                continue
            try:
                writer.actual = verify(client, writer.desired,
                                       purge_subscriptions=writer.purge)
                writer.verified = True
            except SapV2VerifyError as exc:
                failures.append(str(exc))

        if self.rotation_actions:
            try:
                self.rotation_actual = verify_rotation(client, self.rotation)
            except SapV2VerifyError as exc:
                failures.append(str(exc))

        if failures:
            return failures

        for writer in self.deleting:
            apply_plan(client, writer.desired, writer.actions)
        for writer in self.deleting:
            if writer.changed:
                writer.actual = verify(client, writer.desired)
                writer.verified = True
        return []


#: What to do about writers on the device that `writers:` does not name.
UNMANAGED_MODES = ("ignore", "report", "purge")


def list_writers(client):
    """Every writer object under ``Logs#0``, as ``{(type_key, name): path}``.

    Names are bracket-quoted on the wire whenever they contain a ``.``, which
    every ``log_file`` name does, so the segment is partitioned on ``#`` rather
    than split on dots. Non-writer children (``LogRotator#0``) are skipped.
    """
    by_sap_type = dict((t.sap_type, t) for t in WRITER_TYPES.values())
    found = {}
    for path in client.children(LOGS_ROOT):
        sap_type, _sep, name = path[len(LOGS_ROOT) + 1:].partition("#")
        wtype = by_sap_type.get(sap_type)
        if wtype is not None:
            found[(wtype.key, name.strip("[]"))] = path
    return found


def find_unmanaged(client, desired_writers):
    """Writers on the device that `desired_writers` does not name.

    **Scoped to the types the task manages**, and that scoping is the safety
    property rather than a convenience. A Core PRO ships with around a dozen
    LogFileWriters of its own - Connected_Msg.log, SAPv2Log.log, Scenes.log and
    the rest. A task that manages one udp_syslog writer has no business forming
    an opinion about those, and a purge that swept them up would be silent,
    immediate and irreversible.

    Measured on the sandbox: 15 writers present, a playbook naming one. Blunt
    purge would delete all 15; scoped to the named type it deletes exactly the
    one stale writer that was the point.
    """
    named = set((entry.get("type") or DEFAULT_WRITER_TYPE, entry["name"])
                for entry in desired_writers)
    managed_types = set(key for key, _name in named)

    found = []
    for (key, name), path in sorted(list_writers(client).items()):
        if (key, name) in named or key not in managed_types:
            continue
        entry = {"name": name, "type": key, "path": path}
        # Record where it pointed. Every type that can be purged can also be
        # recreated, but only by someone who knows its endpoint - and after a
        # purge there is nothing left to read it from. One cheap read here is
        # what makes the removal reversible rather than merely undoable in
        # principle.
        wtype = writer_type(key)
        if wtype.endpoint_property:
            entry["endpoint"] = client.get(path).get(wtype.endpoint_property, "")
        found.append(entry)
    return found


def plan_device(client, writers, rotation=None, on_immutable_change="fail",
                unmanaged="ignore"):
    """Read every writer plus rotation and plan the whole device. Reads only."""
    rotation = rotation or {}
    planned = []
    for entry in writers:
        actual = read_actual(client, entry["name"], entry["type"])
        planned.append(WriterPlan(entry, actual, plan(
            entry, actual,
            on_immutable_change=on_immutable_change,
            purge_subscriptions=entry.get("subscriptions_purge", False))))

    # `writers` being empty means no managed types, so nothing is in scope -
    # which also stops an empty list from purging a whole device.
    found = []
    if unmanaged != "ignore" and writers:
        found = find_unmanaged(client, writers)
        for entry in found:
            wtype = writer_type(entry["type"])
            if unmanaged != "purge":
                entry["action"] = "reported"
            elif not wtype.replaceable:
                # The rail that matters more here than anywhere else: a purge
                # is the one place a writer gets deleted without anyone naming
                # it. Skip rather than refuse the run - the estate is full of
                # legacy TCP writers and blocking on them would be obstructive.
                entry["action"] = "skipped"
                entry["reason"] = (
                    "a %s cannot be recreated over SapV2, so purging it would "
                    "be one-way. Remove it deliberately with state=absent if "
                    "that is what you want." % wtype.sap_type)
            else:
                entry["action"] = "delete"
                desired = {"name": entry["name"], "type": entry["type"],
                           "state": "absent", "subscriptions": [],
                           "message_log_settings": {}, "properties": {},
                           "subscriptions_purge": False}
                actual = read_actual(client, entry["name"], entry["type"])
                planned.append(WriterPlan(desired, actual,
                                          plan(desired, actual)))

    rotation_actual = read_rotation(client) if rotation else {}
    return DevicePlan(planned, rotation, rotation_actual,
                      plan_rotation(rotation, rotation_actual), unmanaged=found)
