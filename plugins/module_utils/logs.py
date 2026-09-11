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
        quote_path_segment,
    )
except ImportError:  # pragma: no cover - direct file import in unit tests
    from sapv2 import (  # type: ignore
        SapV2VerifyError,
        join_path,
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

    # NOT creatable. Nine parameter vocabularies were tried against real
    # hardware - ip+port, uri, remoteendpointuri, the object's own property
    # names, host+port, address, endpoint, url, and ip alone - and every one
    # was a silent no-op. So an existing TCP writer can be configured and
    # deleted (which is what retiring the legacy path needs), but not created.
    "tcp_client": WriterType(
        key="tcp_client",
        sap_type="TcpClientWriter",
        creatable=False,
        init_params=None,
        writable_properties=("GetOnConnect", "OfflineMaxCacheCount"),
        endpoint_property="RemoteEndpointUri",
        endpoint_template=lambda d: ("tcp://%s:%s/" % (d["ip"], d.get("port") or 1515)) if d.get("ip") else None,
        notes=("Creation is not supported by the device. Plain text with no "
               "header, tag or in-band identity - this is the legacy path."),
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


#: Device-scoped log service settings. These live on Logs#0 and LogRotator#0,
#: not on any writer, which is why they belong to pfc_log_service rather than
#: pfc_logs - two writers on one device must not fight over them.
#:
#: This is the COMPLETE set of rotation-related knobs SapV2 exposes. There is
#: no retention, max-size, max-age or file-count setting; do not add one
#: speculatively.
LOG_SERVICE_PROPERTIES = {
    "check_rotation_after_max_writes": (LOGS_ROOT, "CheckRotationAfterMaxWrites", "num"),
    "skip_clean_logs": (LOGS_ROOT, "SkipCleanLogs", "bool"),
    "ready": (LOGS_ROOT, "Ready", "bool"),
    "minutes_between_search": (LOG_ROTATOR, "MinutesBetweenSearch", "num"),
}

#: The only valid values for the four direction-valued MessageLogSettings
#: properties. ``In`` and ``Out`` look plausible, are accepted on the wire, and
#: are silently ignored - this constant exists to catch that at plan time
#: rather than after a confusing no-op.
DIRECTION_VALUES = ("None", "Incoming", "Outgoing", "Both")

DIRECTION_PROPERTIES = ("Lwrp", "Lwcp", "SapV2Internal", "SapV2External")

BOOLEAN_PROPERTIES = (
    "AuditGet", "AuditSet", "LoginFailures", "LoginSuccesses", "AccessViolations",
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
    return str(value).strip()


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

    Returns ``{"exists": False}`` when the writer is absent, which is a
    different thing from a writer that exists with no subscriptions.
    """
    path = writer_path(writer_name, wtype)
    properties = client.get(path)
    if not properties:
        return {"exists": False, "properties": {}, "subscriptions": {},
                "message_log_settings": {}}

    subscriptions = {}
    for child_path, child_props in client.children(path).items():
        segment = child_path.rsplit(".", 1)[-1]
        if not segment.startswith(SUBSCRIPTION_TYPE + "#"):
            continue
        typeid = segment.split("#", 1)[1].strip("[]")
        # The listing may be shallow, so read the object for its full property
        # set rather than trusting whatever the children reply happened to
        # include.
        full = client.get(child_path) or child_props
        subscriptions[str(typeid)] = full

    return {
        "exists": True,
        "properties": properties,
        "subscriptions": subscriptions,
        "message_log_settings": client.get(settings_path(writer_name, wtype)),
    }


def plan(desired, actual, on_immutable_change="fail", purge_subscriptions=False):
    """Compute the ordered list of actions to bring `actual` to `desired`.

    Pure: no client, no I/O. `desired` is the module's validated parameters,
    `actual` is :func:`read_actual` output.
    """
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
