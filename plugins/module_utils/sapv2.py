# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Thin generic SapV2 client for Telos/Axia PathfinderCore PRO.

This is layer 1: connect, login, get/set/init/del, rfs, and response parsing.
It knows the *protocol* and nothing about any particular subtree. Per-subtree
reconcilers build on top of it.

Deliberately depends on nothing but the standard library so it can be imported
and unit-tested without Ansible present.

Protocol notes that are load-bearing here
-----------------------------------------
* Raw TCP, port 9600 (TLS variant on 9602, not implemented). The device sends
  **no banner** and silently ignores commands issued before login, so a naive
  probe looks like a dead port.
* Framing: connect -> bare CRLF (flushes the device's line parser) ->
  ``Login <user> <pass>`` -> commands. Everything CRLF-terminated.
* Replies are ``indi <path> Prop="value", Prop2="value"``. Appending ``.`` to a
  path lists children. An unknown path replies ``indi NONE``.
* ``rfs <path>`` with no property name dumps the whole schema for that object as
  ``sfr <path> Prop=[ReadWrite=RW,SyntaxType=TXT,...]``. This is the only
  runtime introspection mechanism, and the only honest way to know whether a
  property can be written before trying.
* **Writes are never usefully acknowledged.** A successful ``set`` and a
  rejected one both return nothing at all. Callers MUST verify by reading
  state back; :meth:`SapV2Client.set`, :meth:`~SapV2Client.init` and
  :meth:`~SapV2Client.delete` therefore return ``None`` rather than anything
  that could be mistaken for a result.
* The protocol *does* offer a ``$ACK`` modifier, and this client deliberately
  does not use it. Measured on a Core PRO, appending ``$ACK=True`` to a ``set``
  returns ``ack <path> <Prop>=<value>`` - but it does so for an invalid enum
  value that the device then ignores, for a property that does not exist, and
  for a read-only property. It goes silent only for an unknown *path*. So an
  ack means "I parsed this", never "I applied this", which makes it a more
  dangerous signal than silence: it looks like confirmation and is not.
* There *is* a real error reply for a rejected operation:
  ``error <path> $OP=<verb> $STATUS="Unsupported Operation."``. Worth parsing
  (see :func:`parse_error`), but it does not cover silently-ignored values, so
  it supplements read-back rather than replacing it.
* ``Constructor`` is a **hidden property**, not an operator - it does not
  appear in an object's property list and must be requested by name. Where
  supported it returns the literal ``init`` command that would recreate the
  object, which is the only reliable way to learn a type's init parameter
  names. See :meth:`SapV2Client.constructor`.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
import socket
import time

try:
    from .subtrees import default_guard
except ImportError:  # pragma: no cover - direct file import in unit tests
    from subtrees import default_guard  # type: ignore


DEFAULT_PORT = 9600

#: Seconds of socket silence taken to mean "the reply is complete".
#:
#: This is now the FALLBACK, not the normal path: reads ask for the ``$DONE``
#: terminator and return the moment it lands (see :data:`DONE_MODIFIER`), so
#: this only applies to a firmware that does not echo it. It stays generous
#: because when it is in use it is the only framing there is.
DEFAULT_IDLE_TIMEOUT = 1.5

#: Absolute ceiling on collecting one reply, in case a subscription firehose
#: means the socket never actually goes idle.
DEFAULT_READ_TIMEOUT = 15.0

#: How long to wait for a reply to a WRITE. Writes return nothing - measured,
#: including for rejected values, unknown properties and unknown paths - so
#: this is the wait before taking silence as success. Any reply that does come
#: (an explicit ``error``) has its first byte within ~0.04s on a Core PRO, so
#: this is a wide margin rather than a tight one.
DEFAULT_WRITE_TIMEOUT = 0.35

#: How long to wait for the login reply. The device answers immediately when it
#: answers at all, and silence here is not treated as failure - the inert
#: session probe in connect() is what actually proves the credential.
DEFAULT_LOGIN_TIMEOUT = 3.0

DEFAULT_CONNECT_TIMEOUT = 10.0

CRLF = "\r\n"

READ_VERBS = frozenset(["get", "rfs", "nop"])
WRITE_VERBS = frozenset(["set", "init", "del", "new"])

#: ``init`` parameter names that the device expects BARE (unquoted).
#:
#: This table exists because SapV2 rejects malformed input silently, so the only
#: safe rule is to reproduce forms that have been observed to work on real
#: hardware rather than to generalise. Every name here comes from a command
#: proven against a live Core PRO; anything not listed is quoted, which is the
#: conservative choice for free-text values. If you add a name, prove it first
#: and verify the object by read-back afterwards.
INIT_BARE_PARAMS = frozenset([
    "name", "ip", "port", "typeid", "severity",
    # A TcpClientWriter is created with `autoReconnect=True` bare. It is the
    # parameter that makes that type creatable at all, and the whole table
    # exists because a wrongly-rendered init is a silent no-op.
    "autoreconnect",
])

_NONE_RE = re.compile(r"^\s*(?:indi|sfr)\s+NONE\s*$", re.IGNORECASE | re.MULTILINE)
_REPLY_RE = re.compile(r"^\s*(indi|sfr)\s+(\S+)\s*(.*)$", re.IGNORECASE)

#: SapV2 *does* have an explicit error reply, contrary to the usual assumption
#: that silence is the only failure mode:
#:
#:     error Logs#0 $OP=constructor $STATUS="Unsupported Operation."
#:
#: Measured across 65 object types on a Core PRO. This does NOT make read-back
#: verification optional - a `set` with an invalid enum value still returns
#: absolutely nothing and is silently discarded - but it does mean a whole
#: class of failure (unsupported operation, bad path, refused verb) can be
#: reported precisely instead of as a mystery no-op. Parse it; do not rely on
#: it alone.
_ERROR_RE = re.compile(
    r"^\s*error\s+(?P<path>\S+)\s+(?P<detail>.*)$", re.IGNORECASE | re.MULTILINE)

#: Values matching this are emitted bare by ``set``; anything else is quoted.
#: Enum and boolean values (the RW properties on MessageLogSettings) all match;
#: subscription expressions, which contain spaces and ``$`` tokens, do not.
_BARE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

#: Terminator modifier. Appended to a READ, the device echoes it on the last
#: line of the reply and nowhere else - so a reply can be recognised as
#: complete instead of being inferred from a silence.
#:
#: Measured on a Core PRO: first byte arrives in ~0.04s and the whole reply
#: within ~0.05s, after which the client used to sit out the full idle timeout.
#: With the terminator a read returns as soon as it lands, and idle framing
#: becomes the fallback rather than the normal path. It also removes a guess:
#: the reply misattribution this client was bitten by was fundamentally not
#: knowing where one reply ended.
#:
#: It does NOT help writes. Measured: a successful write, a write of a silently
#: ignored value, a write to an unknown property and a write to an unknown path
#: all return absolutely nothing, with or without the modifier. Read-back
#: verification remains the only way to know a write took.
DONE_MODIFIER = "$DONE"

#: The terminator is appended after the last property SPACE-separated, not
#: comma-separated, so a naive parse folds it into that property's value:
#: ``FriendlyName="MessageLogSettings#0" $DONE``. On a subscription that would
#: make ``Subscription`` compare unequal to the desired expression on every
#: run, forever. Strip it before parsing.
_DONE_SUFFIX_RE = re.compile(r"\s*\$DONE\s*$", re.IGNORECASE)


def strip_done(line):
    """Remove a trailing ``$DONE`` terminator from one reply line."""
    return _DONE_SUFFIX_RE.sub("", line)


def has_done(text):
    """Whether `text` contains a COMPLETE line carrying the terminator.

    The trailing newline matters: without it a chunk boundary landing inside
    the token would end the read early and truncate the reply.
    """
    if not text:
        return False
    index = text.upper().rfind(DONE_MODIFIER)
    return index >= 0 and "\n" in text[index:]


class SapV2Error(Exception):
    """Base class for every failure raised by this client."""


class SapV2ConnectionError(SapV2Error):
    """The TCP session could not be established or was lost."""


class SapV2AuthError(SapV2Error):
    """The device rejected the supplied credentials."""


class SapV2GuardError(SapV2Error):
    """A write was refused because its target subtree is not declarative.

    Raised by the subtree guard, not by the device. See ``subtrees.py``.
    """


class SapV2CommandError(SapV2Error):
    """The device returned an explicit ``error ... $STATUS="..."`` reply.

    Distinct from :class:`SapV2VerifyError`: this is the device saying no,
    whereas a verify error is the device saying nothing and simply not doing
    what was asked.
    """

    def __init__(self, message, path=None, op=None, status=None):
        super(SapV2CommandError, self).__init__(message)
        self.path = path
        self.op = op
        self.status = status


class SapV2VerifyError(SapV2Error):
    """A write was accepted on the wire but did not take effect.

    Because SapV2 never acknowledges writes, this is the *only* way a rejected
    write can ever surface.
    """


def quote_path_segment(name):
    """Render an object name as an addressable path segment.

    ``.`` is SapV2's path separator, so a name containing one has to be
    bracket-quoted or the device parses it as a descent into a child object.
    Estate convention is to avoid the situation entirely by using underscores,
    but existing objects have to be addressable regardless.

    >>> quote_path_segment("alloy_site1")
    '#alloy_site1'
    >>> quote_path_segment("alloy.site1")
    '#[alloy.site1]'
    """
    name = str(name)
    if "." in name or "#" in name or " " in name:
        return "#[%s]" % name
    return "#%s" % name


def join_path(*parts):
    """Join already-rendered path components with the SapV2 separator."""
    return ".".join(str(p) for p in parts if p)


def _split_top_level(text, separator=","):
    """Split on `separator`, ignoring separators inside quotes or brackets.

    ``indi`` payloads look like ``A="x", B="y, z", C=[k=v,k2=v2]`` so neither a
    naive ``split(",")`` nor a single regex is safe.
    """
    out = []
    buf = []
    depth = 0
    in_quotes = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif not in_quotes and ch == "[":
            depth += 1
            buf.append(ch)
        elif not in_quotes and ch == "]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif not in_quotes and depth == 0 and ch == separator:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


#: Encapsulation marker the device wraps certain payloads in. Seen on the
#: ``Constructor`` hidden property and on ``System#0.Access#0.SecurityJson``,
#: so it is a general "here is an opaque payload" wrapper rather than anything
#: specific to security config.
ENCAP_PREFIX = "%BeginEncap%"
ENCAP_SUFFIX = "%EndEncap%"


def strip_encapsulation(value):
    """Remove the ``%BeginEncap%`` / ``%EndEncap%`` wrapper if present."""
    if not value:
        return value
    text = value.strip()
    if text.startswith(ENCAP_PREFIX):
        text = text[len(ENCAP_PREFIX):]
    if text.endswith(ENCAP_SUFFIX):
        text = text[:-len(ENCAP_SUFFIX)]
    return text.strip()


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_properties(payload):
    """Parse the ``Prop="v", Prop2="v2"`` tail of a reply into a dict."""
    props = {}
    for item in _split_top_level(payload):
        if "=" not in item:
            continue
        key, _sep, value = item.partition("=")
        props[key.strip()] = _unquote(value)
    return props


def normalise_path(path):
    """Compare paths ignoring case and bracket-quoting of name segments."""
    return (path or "").replace("#[", "#").replace("]", "").casefold()


def parse_error(text):
    """Return ``(path, op, status)`` for an explicit error reply, else None.

    ``error Logs#0 $OP=constructor $STATUS="Unsupported Operation."``
    """
    if not text:
        return None
    match = _ERROR_RE.search(text)
    if not match:
        return None
    # Error details are SPACE-separated, unlike the comma-separated property
    # lists in an indi reply. Splitting on commas folds $OP and $STATUS into a
    # single key.
    detail = {}
    for item in _split_top_level(match.group("detail"), separator=" "):
        if "=" not in item:
            continue
        key, _sep, value = item.partition("=")
        detail[key.strip()] = _unquote(value)
    return (match.group("path"),
            detail.get("$OP") or detail.get("OP"),
            detail.get("$STATUS") or detail.get("STATUS"))


def parse_indi(text):
    """Parse a raw ``indi``/``sfr`` reply into ``{path: {prop: value}}``.

    ``indi NONE`` (the reply to an unknown path) yields an empty dict, which is
    the same shape as "no children", deliberately: both mean "nothing there".
    Multiple lines are merged, so this handles both a single-object read and a
    children listing.
    """
    result = {}
    if not text:
        return result
    for line in text.splitlines():
        # Strip the terminator FIRST. It sits after the last property with only
        # a space before it, so leaving it in appends " $DONE" to that
        # property's value.
        line = strip_done(line.strip()).strip()
        if not line or _NONE_RE.match(line):
            continue
        match = _REPLY_RE.match(line)
        if not match:
            continue
        _verb, path, payload = match.groups()
        if path.upper() == "NONE":
            continue
        result.setdefault(path, {}).update(parse_properties(payload))
    return result


def parse_schema(text):
    """Parse an ``rfs`` reply into ``{prop: {"ReadWrite": "RW", ...}}``.

    Property metadata arrives as a bracketed group, e.g.
    ``Subscription=[ReadWrite=RW,SyntaxType=TXT,IsStable=True]``. Properties
    without a bracketed group are returned with an empty dict rather than
    dropped, so a caller can still see that the property exists.
    """
    schema = {}
    for _path, props in parse_indi(text).items():
        for prop, raw in props.items():
            raw = raw.strip()
            if raw.startswith("[") and raw.endswith("]"):
                schema[prop] = parse_properties(raw[1:-1])
            else:
                schema[prop] = {}
    return schema


def is_writable(schema, prop):
    """True only if `schema` positively says `prop` is ReadWrite.

    Unknown properties and unknown access types are treated as not writable:
    ``rfs`` also reports a write-only ``WO`` access type that never appears in a
    ``get``, so "not RO" is not the same as "safe to set and verify".
    """
    return schema.get(prop, {}).get("ReadWrite", "").upper() == "RW"


def quote_value(value):
    """Always render `value` quoted."""
    if isinstance(value, bool):
        value = "True" if value else "False"
    return '"%s"' % str(value).replace('"', '\\"')


def render_value(value, bare=False):
    """Render a value for the wire, quoting unless it is safe to leave bare."""
    if isinstance(value, bool):
        return "True" if value else "False"
    text = str(value)
    if bare or _BARE_VALUE_RE.match(text):
        return text
    return quote_value(text)


def render_init_params(params):
    """Render ``init`` parameters: COMMA-separated, device-specific names.

    Two things about ``init`` cost a full spike to discover and are worth
    restating at the point of use:

    1. The parameter names are **not** the object's readable property names.
       A writer's ``RemoteEndpointUri`` property is supplied at creation as
       ``ip=`` (a bare address; the device builds the URI itself), its ``Name``
       as ``name=``, a subscription's ``SubscriptionTypeId`` as ``typeid=``.
    2. Parameters are **comma-separated**, not space-separated.

    Getting either wrong is a silent no-op that looks exactly like "creating
    this object type is not supported".
    """
    rendered = []
    for key, value in params:
        if value is None:
            continue
        # Not `render_value`: its "quote only when necessary" rule would emit
        # customname=device-gain bare, because that value happens to contain no
        # character that needs escaping. The proven command quotes it, and on a
        # protocol that ignores malformed input silently, matching the proven
        # form matters more than emitting the tidiest one. So the table decides,
        # not the value.
        # Matched case-insensitively: init parameter names are, and the device's
        # own Constructor output mixes conventions (Username, slotName,
        # autoReconnect), so the table must not depend on which one a caller
        # happened to write.
        if key.lower() in INIT_BARE_PARAMS:
            rendered.append("%s=%s" % (key, render_value(value, bare=True)))
        else:
            rendered.append("%s=%s" % (key, quote_value(value)))
    return ",".join(rendered)


class SapV2Client(object):
    """A single authenticated SapV2 session against one device.

    Usable as a context manager. Write verbs consult a subtree guard before
    touching anything; see ``subtrees.py`` for why that lives in the client
    rather than in each reconciler.
    """

    #: Cheap, always-present object used to prove a session is really
    #: authenticated. See :meth:`connect`.
    LOGIN_PROBE_PATH = "System#0"

    def __init__(self, host, port=DEFAULT_PORT, connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                 idle_timeout=DEFAULT_IDLE_TIMEOUT, read_timeout=DEFAULT_READ_TIMEOUT,
                 guard=None, check_mode=False, transcript=None, verify_login=True,
                 write_timeout=DEFAULT_WRITE_TIMEOUT, use_done=True):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.read_timeout = read_timeout
        #: How long to wait for a reply to a WRITE before taking silence as
        #: success. Much shorter than idle_timeout because a write almost never
        #: answers and, when it does, answers as fast as anything else.
        self.write_timeout = write_timeout
        self.guard = default_guard if guard is None else guard
        #: When True every write verb is recorded and skipped. Reads still run,
        #: because drift cannot be computed without them.
        self.check_mode = check_mode
        #: Ordered log of every command, with writes marked. Returned to the
        #: caller so a check-mode run is a readable plan and an enforcing run is
        #: an audit trail.
        self.transcript = [] if transcript is None else transcript
        #: Prove the session is authenticated with a read, rather than trusting
        #: the absence of a rejection. See :meth:`connect`.
        self.verify_login = verify_login
        #: Cleared for the session the first time a read asks for the reply
        #: terminator and the device does not echo it back.
        self._done_supported = bool(use_done)
        self._sock = None

    # -- session ---------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def connect(self, username, password):
        try:
            self._sock = socket.create_connection((self.host, self.port),
                                                  timeout=self.connect_timeout)
        except (socket.error, OSError) as exc:
            raise SapV2ConnectionError("cannot connect to %s:%s - %s"
                                       % (self.host, self.port, exc))
        self._sock.settimeout(self.idle_timeout)

        # A bare CRLF first: the device's line parser may hold a partial line
        # from a previous session, and the login would otherwise be appended to
        # it and silently discarded.
        #
        # Then DISCARD whatever that produced, which is normally nothing. This
        # used to be a full _drain(), which waits read_timeout - 15 seconds -
        # for a reply to a newline. Nothing ever answers a newline, so every
        # module invocation opened with a 15 second stall, and a role running
        # two modules against a device paid it twice.
        self._write(CRLF)
        time.sleep(0.2)
        self._flush()

        # The device DOES answer a login ("login successful", or a failure
        # string), and answers immediately. So wait briefly for it rather than
        # the full read ceiling: a firmware that stays silent is not an error
        # here, because the inert-session probe below is the real check.
        self._write("Login %s %s%s" % (username, password, CRLF))
        reply = self._drain(first_byte_timeout=DEFAULT_LOGIN_TIMEOUT,
                            idle=self.write_timeout).strip()
        if "fail" in reply.lower() or "denied" in reply.lower():
            raise SapV2AuthError("login rejected by %s: %s" % (self.host, reply[:200]))
        self.transcript.append({"verb": "login", "command": "Login <user> <redacted>",
                                "write": False, "response": reply[:200]})

        # There is no positive acknowledgement of a SUCCESSFUL login either, so
        # the check above only catches an explicit rejection. A bad credential
        # can instead leave the session inert: commands are accepted and every
        # read comes back empty. Left unchecked that surfaces much later as
        # "the writer does not exist" or an empty drift report against a device
        # that is actually fine - and in check mode it would report a large,
        # entirely fictional plan.
        #
        # So prove the session works with one cheap read of an object every
        # device has, and fail as an auth error at the point of connection.
        if self.verify_login and not self.get(self.LOGIN_PROBE_PATH):
            raise SapV2AuthError(
                "connected to %s but the session is inert: reading %s returned "
                "nothing. SapV2 does not acknowledge a successful login, so "
                "this is what a rejected credential looks like."
                % (self.host, self.LOGIN_PROBE_PATH))
        return self

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except (socket.error, OSError):
                pass
            self._sock = None

    # -- wire ------------------------------------------------------------

    def _write(self, text):
        if self._sock is None:
            raise SapV2ConnectionError("not connected to %s" % self.host)
        try:
            self._sock.sendall(text.encode("utf-8"))
        except (socket.error, OSError) as exc:
            raise SapV2ConnectionError("send to %s failed: %s" % (self.host, exc))

    def _flush(self):
        """Discard anything still pending before issuing a new command.

        Insurance against desynchronisation: if a previous reply arrived late
        it must not be read as this command's reply.
        """
        try:
            self._sock.settimeout(0.05)
            while True:
                if not self._sock.recv(65536):
                    break
        except (socket.timeout, socket.error, OSError):
            pass

    def _drain(self, first_byte_timeout=None, expect_done=False, idle=None):
        """Read a reply: wait for it to START, then read until it goes quiet.

        With `expect_done`, "goes quiet" is replaced by "carries the terminator"
        and the read returns the moment it arrives. Idle framing stays as the
        fallback, for a firmware that does not echo it.

        Two different timeouts, and the distinction is load-bearing. Replies
        have no terminator, so an idle gap is the only frame boundary - but a
        single idle timeout also has to cover the device's think time before
        the first byte, and those are very different quantities. A busy device
        can take several seconds to begin answering while still streaming the
        reply in one burst once it starts.

        Collapsing them caused real misattribution: on a slower unit the read
        timed out empty, and that reply then arrived during the NEXT command
        and was parsed as its result - a `get System#0` returning another
        object's properties. So wait `first_byte_timeout` for the reply to
        begin, then only `idle_timeout` between chunks.
        """
        first = self.read_timeout if first_byte_timeout is None else first_byte_timeout
        buf = b""
        deadline = time.time() + self.read_timeout
        started = False
        try:
            self._sock.settimeout(first)
        except (socket.error, OSError):
            pass
        while time.time() < deadline:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                break
            except (socket.error, OSError) as exc:
                raise SapV2ConnectionError("recv from %s failed: %s" % (self.host, exc))
            if not chunk:
                break
            buf += chunk
            if not started:
                started = True
                try:
                    self._sock.settimeout(self.idle_timeout if idle is None else idle)
                except (socket.error, OSError):
                    pass
            if expect_done and has_done(buf.decode("utf-8", "replace")):
                break
        return buf.decode("utf-8", "replace")

    def execute(self, command, is_write=False, expect_done=False):
        """Send one raw command and return its raw reply text.

        Prefer the typed verbs below. This exists for genuinely ad-hoc use and
        for the subtree stubs, and it still goes through the guard.
        """
        parts = command.split(" ", 2)
        verb = parts[0].lower()
        if is_write or verb in WRITE_VERBS:
            return self._write_command(command, verb, parts[1] if len(parts) > 1 else "")
        self.transcript.append({"verb": verb, "command": command, "write": False})
        # A leading CRLF as well as a trailing one: cheap insurance against a
        # residual partial line in the device parser between commands.
        self._flush()
        self._write(CRLF + command + CRLF)
        return self._drain(expect_done=expect_done)

    def _read(self, command):
        """Issue a read, using the reply terminator where the device echoes it.

        Every read asks for it. If a reply comes back WITHOUT it, this firmware
        does not support the modifier, so stop asking for the rest of the
        session and fall back to idle-gap framing - correct, just slower.

        An EMPTY reply proves nothing and must not trigger that. It usually
        means the read timed out, and the device has at least one way to block
        a read for tens of seconds: creating a TcpClientWriter makes it attempt
        the outbound connection, and subsequent reads queue behind that. An
        earlier version treated empty as "not supported", so one slow read
        permanently degraded the session to idle framing - and, because the
        fallback re-issues the command, doubled the wait that caused it.
        """
        if not self._done_supported:
            return self.execute(command)
        raw = self.execute("%s %s" % (command, DONE_MODIFIER), expect_done=True)
        if not raw.strip():
            return raw
        if has_done(raw):
            return raw
        self._done_supported = False
        self.transcript.append({
            "verb": "note", "write": False,
            "command": "$DONE not echoed by this device; falling back to "
                       "idle-gap framing for the rest of the session"})
        # Re-issue without the modifier rather than trusting a reply the device
        # may have rejected outright. Reads are idempotent, so this costs only
        # time, and only once.
        return self.execute(command)

    def _write_command(self, command, verb, target, properties=None):
        self.guard.assert_writable(target, verb=verb, properties=properties)
        entry = {"verb": verb, "command": command, "write": True,
                 "skipped": bool(self.check_mode)}
        self.transcript.append(entry)
        if self.check_mode:
            return ""
        self._flush()
        self._write(CRLF + command + CRLF)
        # Drain anyway. A write is normally silent, but it can return an
        # explicit `error ... $STATUS=...`, and leaving bytes unread would
        # desynchronise the next command.
        #
        # This waits `write_timeout`, not `idle_timeout`. Measured on a Core
        # PRO, any reply's first byte arrives in ~0.04s, so waiting 1.5s for
        # one that is almost never coming was most of the cost of a write-heavy
        # run. The terminator does not help here - writes return nothing at all,
        # even when rejected - so this is a straight bet that silence means
        # success, which read-back verification then checks. A late reply is
        # discarded by the _flush() before the next command rather than
        # misattributed to it.
        return self._drain(first_byte_timeout=self.write_timeout)

    # -- read verbs ------------------------------------------------------

    def get(self, path, prop=None):
        """Read one object's properties. Returns ``{}`` for an unknown path."""
        command = "get %s %s" % (path, prop) if prop else "get %s" % path
        return self._pick(parse_indi(self._read(command)), path)

    @staticmethod
    def _pick(parsed, path):
        """One object out of a parsed reply, matched on path and nothing else."""
        if path in parsed:
            return parsed[path]
        # Match only on a normalised form of the SAME path - never on "there
        # was exactly one reply". An earlier version did the latter, and when
        # a reply was misattributed it cheerfully returned a different
        # object's properties as though they belonged to this path.
        wanted = normalise_path(path)
        for reply_path, props in parsed.items():
            if normalise_path(reply_path) == wanted:
                return props
        return {}

    def children(self, path):
        """List an object's children as ``{child_path: {prop: value}}``.

        The trailing ``.`` is what makes this a listing rather than a read; the
        parent itself is filtered out of the result.

        ⚠️ A listing carries PATHS ONLY - measured on a Core PRO, the child
        entries come back with no properties at all. To read children's
        properties use :meth:`tree`, which gets them in the same command.
        """
        listing = parse_indi(self._read("get %s." % path))
        return dict((k, v) for k, v in listing.items() if k != path)

    def tree(self, path, depth=-1, include_self=True):
        """Read an object AND its descendants, in as few commands as possible.

        ``$MAX_DEPTH=-1`` returns everything below the path, so this replaces a
        listing plus one read per child. Measured on a Core PRO: a writer with
        27 subscriptions is 30 commands read child-by-child and 2 this way, and
        the whole of ``Logs#0`` - 118 objects across 16 writers, with their
        properties - comes back in a single 37KB reply.

        ⚠️ ``$MAX_DEPTH`` means strictly BELOW: the reply does NOT include the
        object named in the request. Measured, and worth knowing because the
        failure is silent - the caller simply finds nothing at its own path and
        concludes the object does not exist. Hence the second read, unless the
        caller already has the properties from somewhere else.

        Returns ``{path: {prop: value}}``.
        """
        found = parse_indi(self._read("get %s $MAX_DEPTH=%d" % (path, depth)))
        if include_self and not self._pick(found, path):
            own = self.get(path)
            if own:
                found[path] = own
        return found

    def rfs(self, path, prop=None):
        """Read the live schema for an object: ``{prop: {ReadWrite, SyntaxType}}``.

        With no property name this dumps the whole object. Note ``rfs`` on a
        *type* that has no instance returns ``sfr NONE``, so this cannot tell
        you what is creatable — only what an existing object allows.
        """
        command = "rfs %s %s" % (path, prop) if prop else "rfs %s" % path
        return parse_schema(self._read(command))

    def constructor(self, path):
        """Return the ``init`` command that would recreate `path`, or None.

        ``Constructor`` is a hidden property: it is absent from the object's
        property list and from an ``rfs`` schema dump, and must be asked for by
        name. Where the type supports it the reply is the literal creation
        command, wrapped in ``%BeginEncap%``::

            init Users#0.SapUser Username=Admin
            init Routers#0.AxiaAudioRouter id=1
            init MemorySlots#0.LatchingMemorySlot slotName=Cyclone_Mode_Latch

        This is the authoritative way to discover a type's init parameter
        names. They are genuinely inconsistent between types - ``Username``,
        ``id``, ``slotName``, ``FolderName`` - so reading them off the device
        beats guessing from the property names, which is a silent no-op when
        wrong. Not every type supports it: the whole ``Logs#0`` writer and
        subscription family returns nothing, which is why those init forms had
        to be established empirically.
        """
        value = self.get(path, "Constructor").get("Constructor")
        return strip_encapsulation(value) or None

    def exists(self, path):
        return bool(self.get(path))

    # -- write verbs -----------------------------------------------------

    def set(self, path, properties):
        """Set one or more RW properties. Returns nothing - verify by read-back.

        `properties` is an ordered sequence of ``(name, value)`` pairs or a
        mapping. Multiple properties go in one comma-separated command, which
        is the form proven on hardware.
        """
        items = list(properties.items()) if hasattr(properties, "items") else list(properties)
        if not items:
            return None
        payload = ",".join("%s=%s" % (k, render_value(v)) for k, v in items)
        self._write_command("set %s %s" % (path, payload), "set", path,
                            properties=[k for k, _v in items])
        return None

    def init(self, type_path, params):
        """Create an object under `type_path`. Returns nothing - verify by read-back.

        `params` is an ordered sequence of ``(name, value)`` pairs using the
        device's own init-parameter names, which are not the object's property
        names. See :func:`render_init_params`.
        """
        items = list(params.items()) if hasattr(params, "items") else list(params)
        # No `properties` passed to the guard: init parameter names are a
        # different namespace from the object's property names (`ip` creates
        # `RemoteEndpointUri`), so matching them against a runtime-property
        # deny-list would be comparing the wrong vocabulary. Creation is gated
        # by the subtree's classification alone.
        self._write_command("init %s %s" % (type_path, render_init_params(items)),
                            "init", type_path)
        return None

    def delete(self, path):
        """Delete an object. Returns nothing - verify by read-back."""
        self._write_command("del %s" % path, "del", path)
        return None
