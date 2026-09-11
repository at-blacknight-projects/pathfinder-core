#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pfc_logs
short_description: Manage the Logs subtree of a Telos/Axia PathfinderCore PRO
version_added: "0.1.0"
description:
  - Reconciles syslog writers, their log subscriptions and their
    MessageLogSettings on a PathfinderCore PRO appliance over the SapV2 API
    (raw TCP, port 9600).
  - Runs on the controller. PathfinderCore devices are not Ansible hosts, so
    target them with C(connection=local) or C(delegate_to) and identify the
    device with I(host).
  - SapV2 never acknowledges a write. A successful write and a rejected one are
    indistinguishable on the wire, so this module re-reads the device after
    every change and fails if the desired state did not take. A reported
    C(changed=true) always means verified.
  - Check mode performs the reads, computes the plan and writes nothing, which
    makes it a drift report suitable for running unattended on a schedule.
options:
  host:
    description: Hostname or address of the PathfinderCore device.
    type: str
    required: true
  port:
    description: SapV2 TCP port. The TLS variant on 9602 is not supported.
    type: int
    default: 9600
  username:
    description:
      - SapV2 account. Falls back to the C(PFC_USER) environment variable, which
        is how AWX custom credential types, Semaphore secret survey vars and CI
        variables should inject it.
    type: str
    required: true
  password:
    description:
      - Password for I(username). Falls back to the C(PFC_PASS) environment
        variable.
    type: str
    required: true
  writer:
    description:
      - The log writer to manage. A writer is identified by C(name) plus its
        endpoint, and on every type the endpoint property is read-only once the
        object exists, so changing it means delete-and-recreate.
      - Avoid C(.) in C(name). It is SapV2's path separator, so a dotted name
        has to be bracket-quoted in every subsequent command. A C(log_file)
        name is a filename and normally does contain one; the device handles
        the quoting itself.
    type: dict
    required: true
    suboptions:
      name:
        description:
          - Writer name, used as its address in the object tree. For
            C(log_file) this is the filename.
        type: str
        required: true
      type:
        description:
          - Which kind of writer. All four are measured against real hardware
            and they differ in important ways.
          - C(udp_syslog) - the only type emitting syslog framing, and the only
            one with in-band identity. UDP, port 514 fixed. Requires I(ip).
            Creatable.
          - C(tcp_client) - the device connects out. Plain text, no header or
            tag. B(Cannot be created) - every known init form is silently
            ignored by the device - so an existing one can be configured and
            deleted, but not made. This is the legacy path being retired.
          - C(tcp_listener) - the device listens and a collector connects in.
            Plain text. Requires I(port); creating one without it yields an
            object with no properties that can only be deleted. Creatable.
          - C(log_file) - writes locally on the device. Has B(no writable
            properties at all), so configuring one means its subscriptions and
            MessageLogSettings. Creatable.
        type: str
        choices: [udp_syslog, tcp_client, tcp_listener, log_file]
        default: udp_syslog
      ip:
        description:
          - Bare IP address of the receiver. Required for C(udp_syslog), which
            builds C(udp://<ip>:514/) from it.
          - Ignored by C(tcp_listener) (it always binds C(0.0.0.0)) and by
            C(log_file); supplying it for those is rejected rather than
            silently dropped.
        type: str
      port:
        description:
          - Listen port. Required for C(tcp_listener). Ignored by
            C(udp_syslog), whose port is fixed at 514.
        type: int
      properties:
        description:
          - Writable properties on the writer object itself. Only
            C(tcp_client) (C(GetOnConnect), C(OfflineMaxCacheCount)) and
            C(tcp_listener) (those plus C(Listening)) have any; C(udp_syslog)
            and C(log_file) have none, and naming one there is rejected.
        type: dict
  state:
    description:
      - C(present) reconciles the writer. C(absent) deletes it and every
        subscription under it.
    type: str
    choices: [present, absent]
    default: present
  subscriptions:
    description:
      - Desired log subscriptions, identified by I(typeid)
        (C(SubscriptionTypeId)).
      - The C(subscription) expression is writable and updates in place.
        C(severity) and C(customname) are read-only after creation, so a change
        to either is applied by deleting and recreating that subscription -
        safe, because a subscription holds no state.
      - Expressions must match what the device stores verbatim, including case.
    type: list
    elements: dict
    default: []
    suboptions:
      typeid:
        description: SubscriptionTypeId, the identity key.
        type: int
        required: true
      subscription:
        description: The SapV2 subscription expression.
        type: str
        required: true
      severity:
        description: Severity recorded on the device. Read-only after creation.
        type: str
        choices: [Informational, Warning, Error, Critical, Debug]
        default: Informational
      customname:
        description:
          - Friendly name stored on the device. Read-only after creation.
          - >-
            This does NOT reach the wire - syslog lines carry only
            C(PFC: <numeric id> <message>) - so it is device-side metadata for
            the GUI and local log files. Readable names for dashboards have to
            come from a value mapping keyed on the numeric id.
        type: str
  subscriptions_purge:
    description:
      - Whether to delete subscriptions present on the device but absent from
        I(subscriptions).
      - Defaults to false because subscription sets differ per device and were
        generally chosen by local operators. Turn it on only where the
        catalogue is genuinely authoritative for that device.
    type: bool
    default: false
  message_log_settings:
    description:
      - MessageLogSettings properties, all writable.
      - C(Lwrp), C(Lwcp), C(SapV2Internal) and C(SapV2External) take
        C(None), C(Incoming), C(Outgoing) or C(Both). C(In) and C(Out) look
        plausible and are silently ignored by the device, so they are rejected
        here at plan time.
      - A newly created writer defaults to everything off, so it is inert until
        these are set.
    type: dict
  rotation:
    description:
      - Log rotation and cleanup. C(max_file_size), C(max_count),
        C(minutes_between_search), C(skip_clean_logs) and
        C(check_rotation_after_max_writes).
      - These live under C(Logs#0), which is why they are here rather than in
        C(pfc_advanced_options) - each reconciler owns a subtree, and two
        modules on the same properties would conflict.
      - Device-scoped, unlike the rest of this module. Reconciling two writers
        on one device applies rotation twice. That is idempotent, but two tasks
        asking for different values would fight and nothing here can detect it,
        so set rotation on one task only.
      - This is the complete set SapV2 exposes. There is no max-age or
        total-size control. Omit it to leave rotation alone.
    type: dict
  startup_script:
    description:
      - The device's Advanced options script, as raw command lines copied from
        the GUI. Optional and purely advisory.
      - The script replays at boot and is NOT reachable over SapV2, so a value
        set here can be reverted at the next restart. Supplying the script lets
        the module warn which requested values that applies to, rather than
        leaving it to be discovered after a reboot.
      - Lines that cannot be parsed are reported rather than skipped quietly.
        An unreadable line is still replayed on every boot.
    type: list
    elements: str
  on_immutable_change:
    description:
      - What to do when the writer exists but its endpoint URI differs from
        I(writer.ip). C(RemoteEndpointUri) is read-only, so the only repair is
        delete-and-recreate, which interrupts log delivery.
      - C(fail) reports the conflict and changes nothing. C(replace) performs
        the delete and recreate.
      - Defaults to C(fail) because the safe estate procedure is additive -
        create the new writer under a different name, confirm delivery, then
        remove the old one.
    type: str
    choices: [fail, replace]
    default: fail
  idle_timeout:
    description:
      - Seconds of socket silence taken to mean a reply is complete. SapV2
        replies have no terminator, so this is the only available framing and
        every command costs at least this long.
    type: float
    default: 1.5
  read_timeout:
    description: Ceiling on collecting a single reply.
    type: float
    default: 15.0
notes:
  - Reading C(Users#0.SapUser#<name>) on these devices returns the account's
    password hash in the clear. This module never reads that subtree, but any
    credential given to it can.
  - C(Connected=True) on a UDP writer means nothing - there is no connection to
    be up. It is not a delivery signal and this module does not treat it as one.
author:
  - Adam Butler (@at-blacknight)
"""

EXAMPLES = r"""
- name: Report drift without changing anything
  at_blacknight.pathfinder_core.pfc_logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writer:
      name: alloy_site1
      ip: 192.0.2.10
    subscriptions: "{{ pfc_subscriptions }}"
  check_mode: true
  register: drift

- name: Create an inert writer, safe on production
  at_blacknight.pathfinder_core.pfc_logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writer:
      name: alloy_site2
      ip: 192.0.2.20
  # No subscriptions and MessageLogSettings defaulting to off means the writer
  # exists but emits nothing until a later run adds them.

- name: Full reconcile including protocol logging
  at_blacknight.pathfinder_core.pfc_logs:
    host: pfc-002.example.net
    username: "{{ lookup('env', 'PFC_USER') }}"
    password: "{{ lookup('env', 'PFC_PASS') }}"
    writer:
      name: alloy_site2
      ip: 192.0.2.20
    subscriptions:
      - typeid: 1001
        subscription: "sub Devices#0 Connected $MAX_DEPTH=-1"
        severity: Warning
        customname: device-connected
      - typeid: 7002
        subscription: "sub Devices#0 GAIN $MAX_DEPTH=-1"
        customname: device-gain
    message_log_settings:
      Lwrp: Both
      SapV2Internal: Both
      LoginFailures: true

- name: Set log rotation, warned about what the startup script will undo
  at_blacknight.pathfinder_core.pfc_logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writer:
      name: alloy_site1
      ip: 192.0.2.10
    rotation:
      max_file_size: 1
      max_count: 3
    startup_script:
      - 'SET Logs#0.LogRotator#0.RotateRule#0 MaxFileSize=100'
      - 'SET Logs#0.LogRotator#0.RotateRule#0 MaxCount=10'
  # => warns that both revert at the next reboot, and applies them anyway

- name: Retire the legacy writer once the replacement is confirmed
  at_blacknight.pathfinder_core.pfc_logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writer:
      name: old_tcp_target
      ip: 192.0.2.21
    state: absent
"""

RETURN = r"""
changed:
  description: Whether any write was made, verified by read-back.
  returned: always
  type: bool
plan:
  description:
    - The ordered actions required. Populated in check mode too, where it is
      the drift report.
  returned: always
  type: list
  elements: dict
  sample:
    - action: subscription_create
      summary: create subscription 1001 (device-connected) on alloy_site1
      destructive: false
transcript:
  description:
    - Every command issued, in order, with writes marked and passwords
      redacted. Writes show C(skipped=true) in check mode.
  returned: always
  type: list
  elements: dict
writer:
  description: The device's state after reconciling, as read back.
  returned: success
  type: dict
rotation:
  description: Rotation settings as read from the device.
  returned: always
  type: dict
will_revert_at_reboot:
  description:
    - Warnings for requested values the Advanced options script will undo.
      Empty when no I(startup_script) was given, which means "not checked",
      NOT "nothing will revert".
  returned: always
  type: list
  elements: str
startup_script_unparsed:
  description: Script lines that could not be interpreted, with the reason.
  returned: always
  type: list
  elements: dict
verified:
  description:
    - Whether a read-back assertion ran and passed. False in check mode, where
      nothing was written and so nothing needed verifying.
  returned: always
  type: bool
"""

from ansible.module_utils.basic import AnsibleModule, env_fallback

from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils.sapv2 import (
    SapV2Client,
    SapV2Error,
)
from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils import (
    startup as startup_script,
)
from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils.logs import (
    apply_plan,
    apply_rotation,
    plan_rotation,
    read_rotation,
    rotation_writes,
    validate_rotation,
    verify_rotation,
    plan as build_plan,
    read_actual,
    render_state,
    validate_message_log_settings,
    validate_subscriptions,
    validate_writer,
    verify,
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
            writer=dict(type="dict", required=True, options=dict(
                name=dict(type="str", required=True),
                ip=dict(type="str"),
                type=dict(type="str", default="udp_syslog",
                          choices=["udp_syslog", "tcp_client", "tcp_listener",
                                   "log_file"]),
                port=dict(type="int"),
                properties=dict(type="dict"),
            )),
            state=dict(type="str", default="present", choices=["present", "absent"]),
            subscriptions=dict(type="list", elements="dict", default=[], options=dict(
                typeid=dict(type="int", required=True),
                subscription=dict(type="str", required=True),
                severity=dict(type="str", default="Informational",
                              choices=["Informational", "Warning", "Error",
                                       "Critical", "Debug"]),
                customname=dict(type="str"),
            )),
            subscriptions_purge=dict(type="bool", default=False),
            message_log_settings=dict(type="dict"),
            rotation=dict(type="dict"),
            startup_script=dict(type="list", elements="str"),
            on_immutable_change=dict(type="str", default="fail",
                                     choices=["fail", "replace"]),
            idle_timeout=dict(type="float", default=1.5),
            read_timeout=dict(type="float", default=15.0),
        ),
        supports_check_mode=True,
    )

    params = module.params
    desired = {
        "name": params["writer"]["name"],
        "ip": params["writer"].get("ip"),
        "port": params["writer"].get("port"),
        "type": params["writer"].get("type") or "udp_syslog",
        "properties": params["writer"].get("properties") or {},
        "state": params["state"],
        "subscriptions": params["subscriptions"] or [],
        "message_log_settings": params["message_log_settings"] or {},
    }

    rotation = params["rotation"] or {}
    problems = (validate_writer(desired)
                + validate_rotation(rotation)
                + validate_subscriptions(desired["subscriptions"])
                + validate_message_log_settings(desired["message_log_settings"]))
    if problems:
        # Fail before opening a session. Every one of these would otherwise be
        # accepted on the wire and silently ignored by the device.
        module.fail_json(msg="invalid desired state: %s" % "; ".join(problems))

    if "." in desired["name"]:
        module.warn(
            "writer name %r contains '.', which is SapV2's path separator. The "
            "object will only be addressable as #[%s]. Underscores are the "
            "safer convention." % (desired["name"], desired["name"]))

    client = SapV2Client(
        host=params["host"],
        port=params["port"],
        idle_timeout=params["idle_timeout"],
        read_timeout=params["read_timeout"],
        check_mode=module.check_mode,
    )

    result = {"changed": False, "plan": [], "transcript": client.transcript,
              "verified": False, "writer": {}, "rotation": {},
              "will_revert_at_reboot": [], "startup_script_unparsed": []}

    try:
        client.connect(params["username"], params["password"])

        actual = read_actual(client, desired["name"], desired["type"])
        actions = build_plan(
            desired, actual,
            on_immutable_change=params["on_immutable_change"],
            purge_subscriptions=params["subscriptions_purge"],
        )
        result["plan"] = [action.to_dict() for action in actions]
        # Populated whether or not --diff was passed; Ansible only renders it
        # when asked.
        result["diff"] = render_state(
            desired, actual, purge_subscriptions=params["subscriptions_purge"])

        # Rotation is device-scoped rather than per-writer, so it is planned
        # separately and only when asked for.
        rotation_actual = read_rotation(client) if rotation else {}
        rotation_actions = plan_rotation(rotation, rotation_actual)
        actions = actions + rotation_actions
        result["plan"] = [action.to_dict() for action in actions]
        result["rotation"] = rotation_actual

        # The Advanced options startup script replays at boot and cannot be
        # read or written over SapV2, so warn where it will undo this.
        commands, unparsed = startup_script.parse_script(params["startup_script"])
        result["startup_script_unparsed"] = [
            {"line": line, "reason": reason} for line, reason in unparsed]
        for line, reason in unparsed:
            module.warn("unparsed startup script line (%s): %s" % (reason, line))
        reverting = startup_script.conflicts(rotation_writes(rotation), commands)
        result["will_revert_at_reboot"] = reverting
        for warning in reverting:
            module.warn(warning)

        blocked = [a for a in actions if a.kind.startswith("blocked_")]
        if blocked:
            module.fail_json(msg=blocked[0].summary, **result)

        result["changed"] = bool(actions)

        if module.check_mode:
            result["writer"] = actual
            module.exit_json(**result)

        if actions:
            apply_plan(client, desired, actions)
            apply_rotation(client, rotation_actions)
            result["writer"] = verify(
                client, desired,
                purge_subscriptions=params["subscriptions_purge"])
            if rotation:
                result["rotation"] = verify_rotation(client, rotation)
            result["verified"] = True
        else:
            result["writer"] = actual
            # Nothing was written, so there is nothing to verify. Saying
            # verified=true here would overstate what actually happened.

        module.exit_json(**result)

    except SapV2Error as exc:
        module.fail_json(msg=str(exc), **result)
    finally:
        client.close()


if __name__ == "__main__":
    main()
