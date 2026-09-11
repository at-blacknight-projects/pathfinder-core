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

#: Seconds of socket silence taken to mean "the reply is complete". SapV2 has no
#: terminator and no reply to a write, so this idle gap is the only framing we
#: get. Every command therefore costs at least this long.
DEFAULT_IDLE_TIMEOUT = 1.5

#: Absolute ceiling on collecting one reply, in case a subscription firehose
#: means the socket never actually goes idle.
DEFAULT_READ_TIMEOUT = 15.0

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
INIT_BARE_PARAMS = frozenset(["name", "ip", "port", "typeid", "severity"])

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
        line = line.strip()
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
        if key in INIT_BARE_PARAMS:
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

    def __init__(self, host, port=DEFAULT_PORT, connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                 idle_timeout=DEFAULT_IDLE_TIMEOUT, read_timeout=DEFAULT_READ_TIMEOUT,
                 guard=None, check_mode=False, transcript=None):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.read_timeout = read_timeout
        self.guard = default_guard if guard is None else guard
        #: When True every write verb is recorded and skipped. Reads still run,
        #: because drift cannot be computed without them.
        self.check_mode = check_mode
        #: Ordered log of every command, with writes marked. Returned to the
        #: caller so a check-mode run is a readable plan and an enforcing run is
        #: an audit trail.
        self.transcript = [] if transcript is None else transcript
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
        self._write(CRLF)
        time.sleep(0.2)
        self._drain()

        self._write("Login %s %s%s" % (username, password, CRLF))
        reply = self._drain().strip()
        # There is no positive acknowledgement to a successful login either, so
        # this checks only for an explicit rejection. A wrong password on some
        # firmware just leaves the session inert, which the first read exposes.
        if "fail" in reply.lower() or "denied" in reply.lower():
            raise SapV2AuthError("login rejected by %s: %s" % (self.host, reply[:200]))
        self.transcript.append({"verb": "login", "command": "Login <user> <redacted>",
                                "write": False, "response": reply[:200]})
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

    def _drain(self):
        """Read until the socket goes idle for `idle_timeout`.

        SapV2 replies have no terminator and writes produce no reply at all, so
        an idle gap is the only available frame boundary.
        """
        buf = b""
        started = time.time()
        while time.time() - started < self.read_timeout:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                break
            except (socket.error, OSError) as exc:
                raise SapV2ConnectionError("recv from %s failed: %s" % (self.host, exc))
            if not chunk:
                break
            buf += chunk
        return buf.decode("utf-8", "replace")

    def execute(self, command, is_write=False):
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
        self._write(CRLF + command + CRLF)
        return self._drain()

    def _write_command(self, command, verb, target, properties=None):
        self.guard.assert_writable(target, verb=verb, properties=properties)
        entry = {"verb": verb, "command": command, "write": True,
                 "skipped": bool(self.check_mode)}
        self.transcript.append(entry)
        if self.check_mode:
            return ""
        self._write(CRLF + command + CRLF)
        # Drain anyway. Nothing useful comes back from a write, but leaving
        # bytes unread would desynchronise the next command's reply.
        return self._drain()

    # -- read verbs ------------------------------------------------------

    def get(self, path, prop=None):
        """Read one object's properties. Returns ``{}`` for an unknown path."""
        command = "get %s %s" % (path, prop) if prop else "get %s" % path
        parsed = parse_indi(self.execute(command))
        if path in parsed:
            return parsed[path]
        # Some firmware echoes a normalised path (case or bracket differences),
        # so fall back to the sole reply when the read was unambiguous.
        if len(parsed) == 1:
            return list(parsed.values())[0]
        return {}

    def children(self, path):
        """List an object's children as ``{child_path: {prop: value}}``.

        The trailing ``.`` is what makes this a listing rather than a read; the
        parent itself is filtered out of the result.
        """
        listing = parse_indi(self.execute("get %s." % path))
        return dict((k, v) for k, v in listing.items() if k != path)

    def rfs(self, path, prop=None):
        """Read the live schema for an object: ``{prop: {ReadWrite, SyntaxType}}``.

        With no property name this dumps the whole object. Note ``rfs`` on a
        *type* that has no instance returns ``sfr NONE``, so this cannot tell
        you what is creatable — only what an existing object allows.
        """
        command = "rfs %s %s" % (path, prop) if prop else "rfs %s" % path
        return parse_schema(self.execute(command))

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
