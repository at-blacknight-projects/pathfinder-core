#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: startup_script
short_description: Manage a PathfinderCore's Advanced options startup script
version_added: "0.2.0"
description:
  - Manages the device's Advanced options script - the list of SapV2 commands
    PathfinderCore replays at every boot.
  - That script is the device's real boot-time desired state, and nothing else
    checks it. It is B(not) in the SapV2 object tree; every root was enumerated
    and no object holds it. It lives behind the web admin, so this module
    speaks HTTP rather than SapV2.
  - Supply I(lines) to manage the script, or omit it to report - the module
    always returns the current script, the device's factory reference, and the
    difference between them.
  - Optionally give SapV2 credentials as well and it will also compare the
    script against the device's live state, reporting lines the device is not
    actually running and lines that target a property the device does not
    expose (which therefore do nothing on every boot).
options:
  host:
    description: Hostname or address of the PathfinderCore device.
    type: str
    required: true
  port:
    description:
      - Web admin port. Production units answer on 443 or 80; the development
        clone uses 8080. There is no safe default beyond HTTPS, so set it.
    type: int
    default: 443
  scheme:
    description: C(https) or C(http).
    type: str
    choices: [https, http]
    default: https
  validate_certs:
    description: Whether to verify the web admin's TLS certificate.
    type: bool
    default: true
  username:
    description: Admin account. Falls back to C(PFC_USER).
    type: str
    required: true
  password:
    description: Password for I(username). Falls back to C(PFC_PASS).
    type: str
    required: true
  lines:
    description:
      - The complete desired script, one command per line. B(Replaces the
        whole document) - the save field carries the entire script, so any
        line omitted here is deleted from the device.
      - Omit to leave the script alone and only report on it.
    type: list
    elements: str
  sapv2_port:
    description:
      - SapV2 port, used only for the optional live-state comparison. Set
        I(compare_live) to enable that.
    type: int
    default: 9600
  compare_live:
    description:
      - Also connect over SapV2 and compare the script against what the device
        is currently running. Reports lines whose value differs and lines
        targeting a property the device does not expose.
    type: bool
    default: false
notes:
  - "The save replaces the entire script, which makes a partial write
    destructive. This module therefore always fetches first and only ever
    sends a complete document, refuses to save an empty one, and verifies by
    re-fetching."
  - "The page carries TWO scripts - the live one and a read-only factory
    reference - and assigns both to the same JavaScript variable. They are
    told apart by the textarea they are written into. Getting that wrong would
    replace a customised script with stock values, so the module refuses to
    proceed if it cannot positively identify the live one."
  - "Changes take effect at the next restart, not immediately. To change
    behaviour now as well, set the corresponding live value too - for the
    Logs#0 rotation settings that is
    M(at_blacknight.pathfinder_core.logs) and its C(rotation) parameter."
author:
  - Adam Butler (@at-blacknight)
"""

EXAMPLES = r"""
- name: Report the script, and what the device is actually running
  at_blacknight.pathfinder_core.startup_script:
    host: "{{ inventory_hostname }}"
    port: 8080
    scheme: http
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    compare_live: true
  register: startup

- name: Lines the device is not actually running
  ansible.builtin.debug:
    var: startup.live_drift

- name: Lines that do nothing at all, every boot
  ansible.builtin.debug:
    var: startup.dead_lines

- name: Keep smaller log files, durably
  at_blacknight.pathfinder_core.startup_script:
    host: "{{ inventory_hostname }}"
    port: 8080
    scheme: http
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    # The COMPLETE script - anything omitted is removed from the device.
    lines: "{{ startup_script }}"

- name: Drift check across the estate, writing nothing
  at_blacknight.pathfinder_core.startup_script:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    lines: "{{ startup_script }}"
  check_mode: true
"""

RETURN = r"""
changed:
  description: Whether the script was rewritten, verified by re-fetching.
  returned: always
  type: bool
script:
  description: The device's current script, one line per entry.
  returned: always
  type: list
  elements: str
factory_defaults:
  description:
    - The read-only factory reference the page shows alongside the live
      script, if present.
  returned: always
  type: list
  elements: str
differs_from_factory:
  description: Lines present in one of the script and the factory reference but not the other.
  returned: always
  type: dict
live_drift:
  description:
    - Script lines whose value differs from what the device currently holds.
      Only populated when I(compare_live) is set.
  returned: always
  type: list
  elements: dict
dead_lines:
  description:
    - Script lines targeting a property the device does not expose. These do
      nothing on every boot and are otherwise invisible. Only populated when
      I(compare_live) is set.
  returned: always
  type: list
  elements: dict
unparsed:
  description: Script lines that could not be interpreted, with the reason.
  returned: always
  type: list
  elements: dict
"""

from ansible.module_utils.basic import AnsibleModule, env_fallback

from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils import (
    startup as startup_script,
    webadmin,
)
from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils.sapv2 import (
    SapV2Client,
    SapV2Error,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=443),
            scheme=dict(type="str", default="https", choices=["https", "http"]),
            validate_certs=dict(type="bool", default=True),
            username=dict(type="str", required=True,
                          fallback=(env_fallback, ["PFC_USER"])),
            password=dict(type="str", required=True, no_log=True,
                          fallback=(env_fallback, ["PFC_PASS"])),
            lines=dict(type="list", elements="str"),
            sapv2_port=dict(type="int", default=9600),
            compare_live=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )

    params = module.params
    client = webadmin.WebAdminClient(
        host=params["host"],
        username=params["username"],
        password=params["password"],
        port=params["port"],
        scheme=params["scheme"],
        validate_certs=params["validate_certs"],
    )

    result = {"changed": False, "script": [], "factory_defaults": [],
              "differs_from_factory": {}, "live_drift": [], "dead_lines": [],
              "unparsed": []}

    try:
        current_text, factory_text = client.fetch()
        current = webadmin.normalise(current_text)
        factory = webadmin.normalise(factory_text)
        result["script"] = current
        result["factory_defaults"] = factory
        result["differs_from_factory"] = {
            "only_in_script": [line for line in current if line not in factory],
            "only_in_factory": [line for line in factory if line not in current],
        }

        commands, unparsed = startup_script.parse_script(current)
        result["unparsed"] = [{"line": line, "reason": reason}
                              for line, reason in unparsed]
        for line, reason in unparsed:
            module.warn("unparsed script line (%s): %s" % (reason, line))

        if params["compare_live"]:
            sap = SapV2Client(host=params["host"], port=params["sapv2_port"])
            try:
                sap.connect(params["username"], params["password"])
                # Read exactly the objects the script touches - anything else
                # would be guesswork, and boot_vs_live skips what it is not
                # given rather than assuming.
                live = {}
                for path in sorted({c.path for c in commands if c.is_property}):
                    live[path] = sap.get(path)
                drift, dead = startup_script.boot_vs_live(commands, live)
                result["live_drift"] = drift
                result["dead_lines"] = dead
            finally:
                sap.close()

        desired = params["lines"]
        if desired is None:
            module.exit_json(**result)

        desired = [line.strip() for line in desired if line.strip()]
        result["changed"] = desired != current
        result["diff"] = {
            "before": "\n".join(current) + "\n",
            "after": "\n".join(desired) + "\n",
            "before_header": "%s advanced options" % params["host"],
            "after_header": "%s advanced options" % params["host"],
        }

        if not result["changed"] or module.check_mode:
            module.exit_json(**result)

        client.save(webadmin.render(desired))

        # Verify by re-fetching. The save returns a page, not a result, so
        # this is the only confirmation that it took.
        after_text, _factory = client.fetch()
        after = webadmin.normalise(after_text)
        if after != desired:
            missing = [line for line in desired if line not in after]
            extra = [line for line in after if line not in desired]
            module.fail_json(
                msg="the script was saved but reads back differently. missing: "
                    "%s; unexpected: %s" % (missing or "none", extra or "none"),
                **result)
        result["script"] = after
        module.exit_json(**result)

    except (webadmin.WebAdminError, SapV2Error) as exc:
        module.fail_json(msg=str(exc), **result)


if __name__ == "__main__":
    main()
