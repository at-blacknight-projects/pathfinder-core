#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pfc_advanced_options
short_description: Manage a PathfinderCore's Advanced options, including log rotation
version_added: "0.1.0"
description:
  - Manages the device-wide service settings that PathfinderCore groups under
    "Advanced options", including the complete log rotation and cleanup set.
  - These are not one subtree. The vendor page writes single properties on the
    ROOT objects of many - C(Devices#0), C(Routers#0), C(Clustering#0),
    C(LogicFlows#0), C(Logs#0), C(MemorySlots#0), C(UserPanels#0) and
    C(System#0.FloatingIps#0) - which is why this is one module rather than
    several, and why log rotation lives here rather than in its own module.
  - Options are addressed by stable snake_case names so playbooks never embed
    SapV2 paths.
  - >-
    Every write is verified by reading the value back, and that is not belt
    and braces here. At least one of these properties (C(default_theme))
    validates its input and silently discards a value it does not like, and
    another (C(floating_ips_use_unicast)) appears on the vendor's own list
    while being read-only on measured firmware.
options:
  host:
    description: Hostname or address of the PathfinderCore device.
    type: str
    required: true
  port:
    description: SapV2 TCP port.
    type: int
    default: 9600
  username:
    description: SapV2 account. Falls back to C(PFC_USER).
    type: str
    required: true
  password:
    description: Password for I(username). Falls back to C(PFC_PASS).
    type: str
    required: true
  options:
    description:
      - Desired values, keyed by option name. Omitted options are left alone -
        there is no purge, because these are device-wide settings with no
        natural "unmanaged" state.
      - Rotation and cleanup are C(rotate_max_file_size), C(rotate_max_count),
        C(check_rotation_after_max_writes), C(skip_clean_logs) and
        C(minutes_between_search).
      - An unknown, read-only or unsupported name is rejected before anything
        is sent, rather than written and silently ignored.
    type: dict
  idle_timeout:
    description: Seconds of socket silence taken to mean a reply is complete.
    type: float
    default: 1.5
  read_timeout:
    description: Ceiling on collecting a single reply.
    type: float
    default: 15.0
notes:
  - "DURABILITY - measured across a real reboot, with one caveat. The Advanced
    options page is a script of API commands the device runs at startup, and
    that script is not reachable over SapV2, so there was a real possibility
    that a value set here would be stamped back at boot. It is not. On a Core
    PRO rebooted with C(StartupFileProcessed) going True, rotation values that
    differ from the vendor's documented defaults (C(MaxFileSize) 100 against a
    default of 1, C(MaxCount) 10 against 3) survived unchanged, along with
    every other option in this module. So the startup script ran and did not
    reassert the defaults."
  - "The caveat: that proves the values on THAT host survive, which is
    consistent either with the device persisting them or with that host's
    startup script carrying the same values. It does not prove that a value
    this module writes for the first time will survive. The airtight test is
    to set a novel value and reboot again. Until then C(durable) reports
    C(observed) rather than C(guaranteed), and anything that absolutely must
    survive a reboot should also be set in the startup script."
  - C(Ready) on the service roots is deliberately not exposed. Toggling it does
    not restart anything, and the one direction that does something disables
    the service.
author:
  - Adam Butler (@at-blacknight)
"""

EXAMPLES = r"""
- name: Report every advanced option without changing anything
  at_blacknight.pathfinder_core.pfc_advanced_options:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
  register: adv

- name: Show rotation settings against the vendor defaults
  ansible.builtin.debug:
    msg: "{{ adv.report | dict2items
             | selectattr('key', 'search', 'rotat|clean|minutes')
             | list }}"

- name: Keep more, smaller log files
  at_blacknight.pathfinder_core.pfc_advanced_options:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    options:
      rotate_max_file_size: 50
      rotate_max_count: 20
      minutes_between_search: 10

- name: Drift check across the estate, writing nothing
  at_blacknight.pathfinder_core.pfc_advanced_options:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    options: "{{ pfc_advanced_baseline }}"
  check_mode: true
"""

RETURN = r"""
changed:
  description: Whether any value was written and verified.
  returned: always
  type: bool
plan:
  description:
    - The changes required, as option/path/property/current/wanted. Populated
      in check mode too, where it is the drift report.
  returned: always
  type: list
  elements: dict
report:
  description:
    - Every known advanced option with its current value, measured access,
      whether this module supports it, and the vendor default where one is
      documented.
  returned: always
  type: dict
durable:
  description:
    - Always C(observed). Values in this module were seen to survive a real
      reboot on a Core PRO whose startup file was processed, but a
      first-written novel value has not been reboot-tested; see the module
      notes.
  returned: always
  type: str
"""

from ansible.module_utils.basic import AnsibleModule, env_fallback

from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils.sapv2 import (
    SapV2Client,
    SapV2Error,
    SapV2VerifyError,
)
from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils import (
    advanced,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=9600),
            username=dict(type="str", required=True,
                          fallback=(env_fallback, ["PFC_USER"])),
            password=dict(type="str", required=True, no_log=True,
                          fallback=(env_fallback, ["PFC_PASS"])),
            options=dict(type="dict"),
            idle_timeout=dict(type="float", default=1.5),
            read_timeout=dict(type="float", default=15.0),
        ),
        supports_check_mode=True,
    )

    wanted = module.params["options"] or {}
    problems = advanced.validate(wanted)
    if problems:
        # Fail before opening a session: every one of these would otherwise be
        # accepted on the wire and quietly do nothing.
        module.fail_json(msg="invalid advanced options: %s" % "; ".join(problems))

    client = SapV2Client(
        host=module.params["host"],
        port=module.params["port"],
        idle_timeout=module.params["idle_timeout"],
        read_timeout=module.params["read_timeout"],
        check_mode=module.check_mode,
    )

    # Not "guaranteed": survival was observed across a real reboot, but only
    # for values the host already had. See the module notes.
    result = {"changed": False, "plan": [], "report": {},
              "durable": "observed"}

    try:
        client.connect(module.params["username"], module.params["password"])

        # Read every distinct object once, rather than once per option.
        actual = {}
        for path in sorted({o.path for o in advanced.OPTIONS}):
            actual[path] = client.get(path)

        # Refuse anything the device does not actually expose, measured
        # against this device rather than against the module's own table.
        absent = advanced.missing_properties(wanted, actual)
        if absent:
            result["report"] = advanced.report(actual)
            module.fail_json(msg="unsupported on this device: %s"
                                 % "; ".join(absent), **result)

        changes = advanced.plan(wanted, actual)
        result["plan"] = [
            {"option": key, "path": option.path, "property": option.prop,
             "current": current, "wanted": value}
            for key, option, current, value in changes
        ]
        result["report"] = advanced.report(actual)
        result["changed"] = bool(changes)

        if module.check_mode or not changes:
            module.exit_json(**result)

        for _key, option, _current, value in changes:
            client.set(option.path, [(option.prop, value)])

        # Read back and confirm. This is what catches a property that accepts
        # a write and discards it.
        after = {}
        for path in sorted({option.path for _k, option, _c, _v in changes}):
            after[path] = client.get(path)
        failed = []
        for key, option, _current, value in changes:
            got = advanced.normalise(after.get(option.path, {}).get(option.prop, ""))
            if got != value:
                failed.append("%s (%s.%s) is %r, wanted %r%s"
                              % (key, option.path, option.prop, got, value,
                                 " - " + option.note if option.note else ""))
        if failed:
            raise SapV2VerifyError(
                "read-back verification failed. SapV2 never reports a rejected "
                "write, so these were sent and silently did not take: %s"
                % "; ".join(failed))

        actual.update(after)
        result["report"] = advanced.report(actual)
        module.exit_json(**result)

    except SapV2Error as exc:
        module.fail_json(msg=str(exc), **result)
    finally:
        client.close()


if __name__ == "__main__":
    main()
