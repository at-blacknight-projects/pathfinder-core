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
  startup_script:
    description:
      - The device's Advanced options script, as raw command lines copied from
        the GUI. Optional, and the only way the module can know its contents -
        the script is not reachable over SapV2.
      - Raw lines rather than a mapping, because the script is not a settings
        form. It carries more than one verb and parameter names that are not
        property names, so a mapping keyed on this module's option names would
        silently drop everything else.
      - Supplying it produces three things - a warning for each requested value
        the script will revert, a boot-versus-live drift report covering every
        line rather than only the modelled options, and a list of script lines
        targeting properties the device does not expose, which therefore do
        nothing on every boot.
    type: list
    elements: str
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
  - "CHANGES ARE RUNTIME-ONLY AND WILL REVERT AT REBOOT for any option the
    device's Advanced options script sets. That script is a list of API
    commands the device replays at startup, it is per-host and editable in the
    GUI, and it is NOT reachable over SapV2 - there is no startup-file object
    anywhere in the tree. So this module changes the live value, verifies it,
    and cannot change the script."
  - "This was measured, and the first reading of it was wrong. Across a real
    reboot, rotation values differing from the vendor's factory defaults
    (C(MaxFileSize) 100 against a factory 1) survived - which looked like
    persistence. It was not: that host's Advanced options script contains
    C(SET Logs#0.LogRotator#0.RotateRule#0 MaxFileSize=100) explicitly, so the
    script reasserted the same number. Setting a DIFFERENT value would have
    been reverted."
  - "Practical consequence. Read the device's Advanced options page before
    relying on this module to hold a value. If the option appears there, treat
    a change here as temporary and edit the script too. If it does not appear
    there, whether it persists is still unmeasured."
  - "Supply I(startup_script) to have the module tell you which of
    your requested changes will be undone at the next reboot, rather than
    discovering it after one."
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

- name: Change rotation, and be told what the startup script will undo
  at_blacknight.pathfinder_core.pfc_advanced_options:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    options:
      rotate_max_file_size: 1
      rotate_max_count: 3
    # Transcribed from the device's Advanced options page. The module cannot
    # read it, so this is the only way it can warn you.
    startup_script_options:
      rotate_max_file_size: 100
      rotate_max_count: 10
  # => warns that both revert at the next reboot, and applies them anyway

- name: Audit the startup script against what the device is actually running
  at_blacknight.pathfinder_core.pfc_advanced_options:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    startup_script: "{{ pfc_startup_script }}"
  register: adv

- name: Lines whose value does not match the live device
  ansible.builtin.debug:
    var: adv.startup_script_drift

- name: Lines that do nothing at all, every boot
  ansible.builtin.debug:
    var: adv.startup_script_absent

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
    - Always C(runtime_only). The device's Advanced options startup script
      replays at boot and cannot be read or written over SapV2, so any option
      it sets reverts; see the module notes.
  returned: always
  type: str
will_revert_at_reboot:
  description:
    - Warnings for requested values the startup script will undo. Empty when
      no I(startup_script) was supplied, which means "not checked", NOT
      "nothing will revert".
  returned: always
  type: list
  elements: str
startup_script_drift:
  description:
    - Script lines whose value differs from what the device currently holds,
      covering every line rather than only the modelled options.
  returned: always
  type: list
  elements: dict
startup_script_absent:
  description:
    - Script lines targeting a property the device does not expose. These do
      nothing on every boot and are otherwise invisible.
  returned: always
  type: list
  elements: dict
startup_script_unparsed:
  description: Script lines that could not be interpreted, with the reason.
  returned: always
  type: list
  elements: dict
"""

from ansible.module_utils.basic import AnsibleModule, env_fallback

from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils.sapv2 import (
    SapV2Client,
    SapV2Error,
    SapV2VerifyError,
)
from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils import (
    advanced,
    startup as startup_script,
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
            startup_script=dict(type="list", elements="str"),
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
    # Runtime-only: the Advanced options startup script replays at boot and
    # cannot be read or written over SapV2, so any option it sets will revert.
    result = {"changed": False, "plan": [], "report": {},
              "durable": "runtime_only", "will_revert_at_reboot": [],
              "startup_script_unparsed": [], "startup_script_drift": [],
              "startup_script_absent": []}

    try:
        client.connect(module.params["username"], module.params["password"])

        commands, unparsed = startup_script.parse_script(
            module.params["startup_script"])

        # Read every distinct object once, rather than once per option - and
        # include the objects the SCRIPT mentions, not just the ones this
        # module models. boot_vs_live deliberately skips a path it was not
        # given rather than guessing, so reading only the modelled paths would
        # silently leave most of the script unchecked. That is the opposite of
        # the point: script lines for subtrees owned by other modules (all the
        # Logs#0 rotation lines, for instance) are exactly the ones nothing
        # else looks at.
        paths = {o.path for o in advanced.OPTIONS}
        paths.update(c.path for c in commands if c.is_property)
        actual = {}
        for path in sorted(paths):
            actual[path] = client.get(path)

        # Refuse anything the device does not actually expose, measured
        # against this device rather than against the module's own table.
        unsupported = advanced.missing_properties(wanted, actual)
        if unsupported:
            result["report"] = advanced.report(actual)
            module.fail_json(msg="unsupported on this device: %s"
                                 % "; ".join(unsupported), **result)

        # Warn where the startup script will undo this at the next reboot.
        # Applied anyway: the immediate effect is normally the point, and
        # failing would be worse than saying so clearly.
        result["startup_script_unparsed"] = [
            {"line": line, "reason": reason} for line, reason in unparsed]
        for line, reason in unparsed:
            module.warn("unparsed startup script line (%s): %s" % (reason, line))

        # Boot-state vs live-state, for every line in the script - not just
        # the options this module knows about. Absent-property lines are the
        # ones that silently do nothing on every boot.
        drift, absent = startup_script.boot_vs_live(commands, actual)
        result["startup_script_drift"] = drift
        result["startup_script_absent"] = absent

        reverting = startup_script.conflicts(advanced.intended_writes(wanted), commands)
        result["will_revert_at_reboot"] = reverting
        for warning in reverting:
            module.warn(warning)

        changes = advanced.plan(wanted, actual)
        result["plan"] = [
            {"option": key, "path": option.path, "property": option.prop,
             "current": current, "wanted": value}
            for key, option, current, value in changes
        ]
        result["report"] = advanced.report(actual)
        result["diff"] = advanced.render_state(wanted, actual)
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
