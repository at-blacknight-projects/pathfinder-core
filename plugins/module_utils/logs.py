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

#: Syslog on PathfinderCore is UDP and port 514 is fixed. The GUI asks only for
#: an IP; the API exposes a full URI, and the device builds it from ``ip=``.
SYSLOG_PORT = 514

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


def writer_path(name):
    return join_path(LOGS_ROOT, UDP_WRITER_TYPE + quote_path_segment(name))


def subscription_path(writer_name, typeid):
    return join_path(writer_path(writer_name),
                     SUBSCRIPTION_TYPE + quote_path_segment(typeid))


def settings_path(writer_name):
    return join_path(writer_path(writer_name), MESSAGE_LOG_SETTINGS)


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


def read_actual(client, writer_name):
    """Read the current state of one writer into a plain dict.

    Returns ``{"exists": False}`` when the writer is absent, which is a
    different thing from a writer that exists with no subscriptions.
    """
    path = writer_path(writer_name)
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
        "message_log_settings": client.get(settings_path(writer_name)),
    }


def plan(desired, actual, on_immutable_change="fail", purge_subscriptions=False):
    """Compute the ordered list of actions to bring `actual` to `desired`.

    Pure: no client, no I/O. `desired` is the module's validated parameters,
    `actual` is :func:`read_actual` output.
    """
    actions = []
    name = desired["name"]
    state = desired.get("state", "present")

    if state == "absent":
        if actual["exists"]:
            actions.append(Action(
                "writer_delete",
                "delete writer %s (and its subscriptions)" % name,
                destructive=True,
                detail={"writer": name, "path": writer_path(name)}))
        return actions

    wanted_uri = expected_uri(desired["ip"])
    recreate = False

    if not actual["exists"]:
        actions.append(Action(
            "writer_create",
            "create writer %s -> %s" % (name, wanted_uri),
            detail={"writer": name, "ip": desired["ip"], "uri": wanted_uri}))
        recreate = True
    else:
        current_uri = actual["properties"].get("RemoteEndpointUri", "")
        if normalise_expression(current_uri) != normalise_expression(wanted_uri):
            # RemoteEndpointUri is read-only after creation, so there is no
            # update path - only delete and recreate, which drops logs for as
            # long as the gap lasts.
            if on_immutable_change == "replace":
                actions.append(Action(
                    "writer_replace",
                    "replace writer %s: endpoint %s -> %s (delete + recreate; "
                    "RemoteEndpointUri is read-only)" % (name, current_uri, wanted_uri),
                    destructive=True,
                    detail={"writer": name, "from": current_uri, "to": wanted_uri}))
                recreate = True
            else:
                actions.append(Action(
                    "blocked_immutable",
                    "writer %s endpoint is %s but %s is wanted. "
                    "RemoteEndpointUri is read-only, so this needs a delete and "
                    "recreate, which interrupts log delivery. Set "
                    "on_immutable_change=replace to allow it, or preferably "
                    "create the new writer under a different name, confirm "
                    "delivery, then remove the old one."
                    % (name, current_uri, wanted_uri),
                    destructive=True,
                    detail={"writer": name, "from": current_uri, "to": wanted_uri}))
                return actions

    actions.extend(_plan_subscriptions(desired, actual, recreate, purge_subscriptions))
    actions.extend(_plan_settings(desired, actual, recreate))
    return actions


def _plan_subscriptions(desired, actual, recreate, purge):
    actions = []
    name = desired["name"]
    wanted = dict((str(s["typeid"]), s) for s in desired.get("subscriptions") or [])
    current = {} if recreate else actual.get("subscriptions", {})

    for typeid in sorted(wanted, key=lambda t: int(t)):
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
        for typeid in sorted(set(current) - set(wanted), key=lambda t: int(t)):
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
    subs_by_id = dict((str(s["typeid"]), s) for s in desired.get("subscriptions") or [])

    for action in actions:
        if action.kind == "blocked_immutable":
            continue
        if action.kind == "writer_delete":
            client.delete(writer_path(name))
        elif action.kind == "writer_replace":
            client.delete(writer_path(name))
            _create_writer(client, desired)
        elif action.kind == "writer_create":
            _create_writer(client, desired)
        elif action.kind == "subscription_create":
            _create_subscription(client, name, subs_by_id[action.detail["typeid"]])
        elif action.kind == "subscription_replace":
            client.delete(subscription_path(name, action.detail["typeid"]))
            _create_subscription(client, name, subs_by_id[action.detail["typeid"]])
        elif action.kind == "subscription_update":
            client.set(subscription_path(name, action.detail["typeid"]),
                       [("Subscription", action.detail["to"])])
        elif action.kind == "subscription_delete":
            client.delete(subscription_path(name, action.detail["typeid"]))
        elif action.kind == "settings_set":
            client.set(settings_path(name), sorted(action.detail["changes"].items()))


def _create_writer(client, desired):
    # `name` and `ip` are the device's init-parameter names, not the object's
    # property names, and `ip` takes a bare address - the device builds the
    # udp://<ip>:514/ URI itself. Using Name= / RemoteEndpointUri= here is a
    # silent no-op.
    client.init(join_path(LOGS_ROOT, UDP_WRITER_TYPE),
                [("name", desired["name"]), ("ip", desired["ip"])])


def _create_subscription(client, writer_name, sub):
    # severity and customname are settable ONLY here: both are read-only once
    # the object exists.
    params = [("subscription", sub["subscription"]), ("typeid", sub["typeid"])]
    if sub.get("severity"):
        params.append(("severity", sub["severity"]))
    if sub.get("customname"):
        params.append(("customname", sub["customname"]))
    client.init(join_path(writer_path(writer_name), SUBSCRIPTION_TYPE), params)


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
    actual = read_actual(client, name)

    if state == "absent":
        if actual["exists"]:
            raise SapV2VerifyError(
                "writer %s still exists after delete. SapV2 does not report "
                "write failures, so this is what a rejected delete looks like."
                % name)
        return actual

    problems = []
    if not actual["exists"]:
        raise SapV2VerifyError(
            "writer %s does not exist after create. The most common cause is "
            "init parameter shape: parameters are comma-separated and use the "
            "device's own names (name=, ip=), not the object's property names."
            % name)

    wanted_uri = expected_uri(desired["ip"])
    have_uri = actual["properties"].get("RemoteEndpointUri", "")
    if normalise_expression(have_uri) != normalise_expression(wanted_uri):
        problems.append("RemoteEndpointUri is %r, wanted %r" % (have_uri, wanted_uri))

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
