# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP client for the PathfinderCore web admin.

The second transport in this collection, and the reason it exists: the
Advanced options startup script is not in the SapV2 object tree at all - every
root was enumerated and nothing holds it - but it IS readable and writable
through the device's web admin.

    GET  /admin-advancedoptions.php       current script + factory defaults
    POST /admin-advancedoptions-save.php  options=<the entire script>

Two things make this more delicate than it looks
------------------------------------------------
**The save is whole-document.** ``options`` carries the complete script, so
anything not included is deleted. It must always be read-modify-write, never
constructed from desired state alone - the same hazard as the SecurityJson
blob, with the same consequence.

**The script is not in the HTML.** Both textareas are empty in the markup and
are populated by JavaScript, so the value has to be lifted out of a JS string
literal. A naive regex splits that literal at the first escaped quote - the
real script's first line contains ``localAxiaIP="0.0.0.0"`` - and silently
truncates it. Combined with a whole-document save, that turns a parsing slip
into data loss, so the literal is scanned properly rather than matched.

Port varies by device: production units answer on 443 or 80, the development
clone on 8080. It is a parameter, never a constant.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
from urllib.parse import urlencode

# NOTE: the HTTP call itself is imported lazily inside _request, from
# ansible.module_utils.urls. Everything else in this file - the JS string
# scanner, script extraction, normalise/render - is deliberately stdlib-only
# so it can be unit-tested without Ansible present. That is where the bugs
# live: three separate parsing faults here would each have silently corrupted
# or replaced a device's script, whereas the request itself is a thin call.


OPTIONS_PAGE = "/admin-advancedoptions.php"
OPTIONS_SAVE = "/admin-advancedoptions-save.php"

#: The device uses CRLF in the script. Preserved on save so a round-trip does
#: not rewrite every line and show a whole-file diff for a one-line change.
LINE_SEP = "\r\n"

#: The page assigns the scripts to plain JS variables - measured firmware uses
#: `var fileData = "..."` - rather than setting textarea values directly, so
#: this matches a variable assignment rather than a `.value =`.
_ASSIGN_RE = re.compile(
    r"""(?:var\s+)?(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?P<quote>["'])""")

#: `document.getElementById("options").value=fileData;` - the id is the only
#: thing that distinguishes the live script from the factory reference, since
#: both are assigned from the same variable.
_ELEMENT_RE = re.compile(
    r"""getElementById\(\s*["'](?P<element>[A-Za-z0-9_-]+)["']\s*\)\s*"""
    r"""\.\s*value\s*=\s*(?P<var>[A-Za-z_$][A-Za-z0-9_$]*)""")


class WebAdminError(Exception):
    """The web admin could not be reached, or refused the request."""


def _scan_js_string(text, start, quote):
    """Return the decoded JS string literal beginning at `start`.

    Scans character by character honouring backslash escapes, because the
    script contains escaped quotes and a regex stopping at the first quote
    truncates it.
    """
    out = []
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            nxt = text[index + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
            index += 2
            continue
        if char == quote:
            return "".join(out), index + 1
        out.append(char)
        index += 1
    raise WebAdminError("unterminated string literal in the Advanced options page")


def extract_scripts(html):
    """Return ``{textarea id: script text}`` from the page's JavaScript.

    The markup's textareas are empty; the page fills them in script, and it
    does so by REUSING ONE VARIABLE::

        var fileData = "<current script>";
        document.getElementById("options").value=fileData;
            fileData = "<factory defaults>";
        document.getElementById("defaultoptions").value=fileData;

    So the variable name identifies nothing - keying on it returns whichever
    assignment came last. That matters more than it sounds: the last one is the
    factory defaults, and saving those back would silently replace a device's
    customised script with stock values.

    The textarea id is the only reliable discriminator, so this walks the
    document in order and attributes each string to the id it is assigned into.
    """
    assignments = {}
    found = {}
    position = 0
    length = len(html)

    while position < length:
        assign = _ASSIGN_RE.search(html, position)
        use = _ELEMENT_RE.search(html, position)
        if assign is None and use is None:
            break
        # Process whichever comes first, so ordering is respected.
        if use is None or (assign is not None and assign.start() < use.start()):
            try:
                value, end = _scan_js_string(html, assign.end(), assign.group("quote"))
            except WebAdminError:
                position = assign.end()
                continue
            assignments[assign.group("target")] = value
            position = end
        else:
            value = assignments.get(use.group("var"))
            if value is not None:
                found[use.group("element")] = value
            position = use.end()
    return found


class WebAdminClient(object):
    """Fetch and save the Advanced options script over HTTP."""

    def __init__(self, host, username, password, port=443, scheme="https",
                 validate_certs=True, timeout=30):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.scheme = scheme
        self.validate_certs = validate_certs
        self.timeout = timeout

    def _request(self, path, data=None):
        # Imported here, not at module scope, so the parsing helpers above
        # stay importable without Ansible.
        from ansible.module_utils.urls import open_url
        from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError

        url = "%s://%s:%d%s" % (self.scheme, self.host, self.port, path)
        body = urlencode(data).encode("utf-8") if data is not None else None
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            response = open_url(
                url, data=body, headers=headers,
                method="POST" if data is not None else "GET",
                url_username=self.username, url_password=self.password,
                force_basic_auth=True,
                validate_certs=self.validate_certs, timeout=self.timeout)
            return response.read().decode("utf-8", "replace")
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # pragma: no cover - best effort
                pass
            if exc.code in (401, 403):
                raise WebAdminError(
                    "web admin rejected the credentials for %s (HTTP %d). Note "
                    "this is a different login surface from SapV2, even though "
                    "the same account normally works on both."
                    % (url, exc.code))
            raise WebAdminError("HTTP %d from %s: %s" % (exc.code, url, detail))
        except URLError as exc:
            raise WebAdminError(
                "cannot reach the web admin at %s - %s. Production units "
                "answer on 443 or 80; the development clone uses 8080, so "
                "check the port before assuming the interface is absent."
                % (url, exc.reason))

    def fetch(self):
        """Return ``(current_script, factory_defaults)``.

        `factory_defaults` is the read-only reference the page shows alongside
        the live script, and is None if the page does not carry one.
        """
        html = self._request(OPTIONS_PAGE)
        scripts = extract_scripts(html)
        if not scripts:
            raise WebAdminError(
                "the Advanced options page was fetched but no script could be "
                "read from it. The page populates its textareas from "
                "JavaScript, so a firmware change to that markup would break "
                "this - refusing rather than treating it as an empty script, "
                "which would delete everything on save.")
        current = scripts.get("options")
        if current is None:
            raise WebAdminError(
                "the Advanced options page was fetched but the live script "
                "(textarea id 'options') could not be identified. Refusing "
                "rather than guessing: the page also carries the FACTORY "
                "DEFAULTS, and mistaking those for the live script would "
                "replace a device's customised script with stock values on "
                "the next save. Found ids: %s" % ", ".join(sorted(scripts)))
        return current, scripts.get("defaultoptions")

    def save(self, script):
        """Write the ENTIRE script. Verify with :meth:`fetch` afterwards.

        Refuses an empty script: the field is whole-document, so saving
        nothing erases every line, and that is never what a caller meant.
        """
        if not (script or "").strip():
            raise WebAdminError(
                "refusing to save an empty Advanced options script - the save "
                "replaces the whole document, so this would erase every line.")
        self._request(OPTIONS_SAVE, data={"options": script})


def normalise(script):
    """Split a script into comparable lines, ignoring blank lines and EOL style."""
    return [line.strip() for line in (script or "").replace("\r\n", "\n").split("\n")
            if line.strip()]


def render(lines):
    """Join lines using the device's own CRLF, with a trailing separator."""
    return LINE_SEP.join(lines) + LINE_SEP
