#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: logs
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
  - >-
    Takes the device's whole set of writers at once, so one task can stand up a
    replacement writer and retire the one it replaces. Creates and
    configuration are applied and verified first; the deletes run only if all
    of that verified. See I(writers).
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
  writers:
    description:
      - The device's log writers, as a list. One session is opened for the
        whole list, which matters because each session costs a connect, a
        login and an authentication probe.
      - >-
        Order of execution is not list order. Every C(present) entry is applied
        and read back first, and the C(absent) entries run only once all of
        that has verified. A writer being retired is therefore never deleted on
        a run where its replacement failed to come up.
      - >-
        A writer is identified by I(type) plus I(name), so the same name under
        two types is two different objects. The same pair twice is rejected.
      - >-
        I(state), I(subscriptions), I(subscriptions_purge) and
        I(message_log_settings) may be set here per writer, or once at task
        level to apply to every writer that does not state its own. Omitting one
        inherits the task-level value; setting it to an empty list or dict does
        not.
    type: list
    elements: dict
    required: true
    suboptions:
      name:
        description:
          - Writer name, used as its address in the object tree. For
            C(log_file) this is the filename.
          - Avoid C(.) in a name. It is SapV2's path separator, so a dotted
            name has to be bracket-quoted in every subsequent command. A
            C(log_file) name is a filename and normally does contain one; the
            device handles that quoting itself.
        type: str
        required: true
      type:
        description:
          - Which kind of writer. All four are measured against real hardware
            and they differ in important ways.
          - C(udp_syslog) - the only type emitting syslog framing, and the only
            one with in-band identity. UDP, port 514 fixed. Requires I(ip).
            Creatable.
          - >-
            C(tcp_client) - the device connects out. Plain text, no header or
            tag, so nothing identifies the sender once traffic is NATted; prefer
            C(udp_syslog) for new work. Creatable, and requires I(ip). Creating
            one makes the device dial out immediately and its replies queue
            behind that, so the create and its read-back can take ~30 seconds
            against an endpoint that is not listening. That is the device, not
            a timeout to tune.
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
          - C(present) reconciles this writer. C(absent) deletes it and every
            subscription under it.
          - Defaults to the task-level I(state).
        type: str
        choices: [present, absent]
      subscriptions:
        description:
          - Desired log subscriptions for this writer. Defaults to the
            task-level I(subscriptions).
        type: list
        elements: dict
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
            description: Friendly name stored on the device. Read-only after creation.
            type: str
      subscriptions_purge:
        description:
          - Whether to delete subscriptions on this writer that
            I(subscriptions) does not list. Defaults to the task-level
            I(subscriptions_purge).
        type: bool
      message_log_settings:
        description:
          - MessageLogSettings for this writer. Defaults to the task-level
            I(message_log_settings).
        type: dict
  state:
    description:
      - Default I(state) for I(writers) entries that do not set their own.
    type: str
    choices: [present, absent]
    default: present
  subscriptions:
    description:
      - Default subscriptions for I(writers) entries that do not set their own,
        identified by I(typeid) (C(SubscriptionTypeId)).
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
      - Default for I(writers) entries that do not set their own. Whether to
        delete subscriptions present on the device but absent from the writer's
        subscription list.
      - Defaults to false because subscription sets differ per device and were
        generally chosen by local operators. Turn it on only where the
        catalogue is genuinely authoritative for that device.
    type: bool
    default: false
  message_log_settings:
    description:
      - Default MessageLogSettings for I(writers) entries that do not set their
        own. All of these are writable.
      - C(Lwrp), C(Lwcp), C(SapV2Internal) and C(SapV2External) take
        C(None), C(Incoming), C(Outgoing) or C(Both). C(In) and C(Out) look
        plausible and are silently ignored by the device, so they are rejected
        here at plan time.
      - >-
        C(AuditGet), C(AuditSet), C(LoginFailures), C(LoginSuccesses),
        C(AccessViolations) and C(SkipWebClientSapMessages) are booleans. The
        last suppresses SAP traffic from the device's own web client, which is
        the only per-source volume control a writer has.
      - A newly created writer defaults to everything off, so it is inert until
        these are set.
    type: dict
  unmanaged_writers:
    description:
      - What to do about writers on the device that I(writers) does not name.
      - >-
        C(ignore) (the default) leaves them completely alone and does not even
        look. C(report) lists them in the C(unmanaged) return value without
        changing anything. C(purge) deletes those it is allowed to.
      - >-
        C(report) lists writers of B(every) type, because the honest answer to
        "what else is on this device" includes the dozen LogFileWriters a Core
        PRO ships with and any legacy TCP writer - the latter being exactly what
        an estate migration wants surfaced.
      - >-
        C(purge) is narrower, and deliberately: it only ever deletes writers of
        the B(types) named in I(writers). That is what stops a task managing one
        C(udp_syslog) writer from sweeping away the device's own log files.
        Measured on a real device - 15 writers present, a playbook naming one -
        an unscoped purge would delete all 15, while the scoped one deletes
        exactly the stale writer that was the point.
      - >-
        Each entry carries C(in_purge_scope) saying which of the two it falls
        under, so a report of five writers cannot be mistaken for five pending
        deletions. Out-of-scope entries are reported with C(action=out_of_scope).
      - >-
        C(purge) never deletes a type that cannot be recreated. A C(tcp_client)
        is skipped and reported rather than removed, because a purge is the one
        place a writer would be deleted without anyone naming it, and that
        deletion would be one-way.
      - Purge deletions run in the same phase as C(state=absent) entries, so
        they happen only after every C(present) writer has been read back.
      - An empty I(writers) names no types, so nothing is ever in scope.
    type: str
    choices: [ignore, report, purge]
    default: ignore
  rotation:
    description:
      - Log rotation and cleanup. C(max_file_size), C(max_count),
        C(minutes_between_search), C(skip_clean_logs) and
        C(check_rotation_after_max_writes).
      - These live under C(Logs#0), which is why they are here rather than in
        C(startup_script) - each reconciler owns a subtree, and two modules
        writing the same properties would conflict.
      - Device-scoped rather than per-writer, which is why it sits beside
        I(writers) rather than inside an entry. Two tasks asking for different
        values on one device would fight and nothing here can detect it, so set
        rotation on one task only.
      - This is the complete set SapV2 exposes. There is no max-age or
        total-size control. Omit it to leave rotation alone.
    type: dict
  startup_script:
    description:
      - The device's Advanced options script, as raw command lines. Optional and
        purely advisory here; C(startup_script) is the module that manages
        it.
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
      - What to do when a writer exists but its endpoint differs from the one
        asked for. The endpoint property is read-only on every type, so the
        only repair is delete-and-recreate, which interrupts log delivery.
      - C(fail) reports the conflict and changes nothing. C(replace) performs
        the delete and recreate.
      - Defaults to C(fail) because the safe estate procedure is additive -
        create the new writer under a different name, confirm delivery, then
        remove the old one. That is a single task now that I(writers) is a list.
    type: str
    choices: [fail, replace]
    default: fail
  idle_timeout:
    description:
      - Seconds of socket silence taken to mean a reply is complete.
      - >-
        This is the FALLBACK, not the normal path. Reads ask for the SapV2
        C($DONE) terminator and return the moment it arrives, so this only
        applies to a firmware that does not echo it - in which case every read
        costs at least this long again.
    type: float
    default: 1.5
  write_timeout:
    description:
      - Seconds to wait for a reply to a write before taking silence as
        success.
      - >-
        A write returns nothing on measured firmware - including when the value
        was rejected, the property unknown or the path wrong - so this is a wait
        for an C(error) reply that almost never comes, and read-back is what
        actually establishes the write took. Any reply that does come arrives in
        well under 0.1s, so the default is a wide margin.
      - >-
        Writes dominate a from-scratch reconcile (one per subscription), so this
        is the main remaining lever on how long one takes.
    type: float
    default: 0.35
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
  - >-
    The singular C(writer) option was replaced by I(writers) in 0.1.0-alpha.9.
    Wrap the old dict in a list to migrate; nothing else about it changed.
author:
  - Adam Butler (@at-blacknight)
"""

EXAMPLES = r"""
- name: Report drift without changing anything
  at_blacknight.pathfinder_core.logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writers:
      - name: alloy_site1
        ip: 192.0.2.10
    subscriptions: "{{ pfc_subscriptions }}"
  check_mode: true
  register: drift

- name: Create an inert writer, safe on production
  at_blacknight.pathfinder_core.logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writers:
      - name: alloy_site2
        ip: 192.0.2.20
  # No subscriptions and MessageLogSettings defaulting to off means the writer
  # exists but emits nothing until a later run adds them.

- name: Cut over in one task - the delete runs only if the create verified
  at_blacknight.pathfinder_core.logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writers:
      - name: alloy_site1
        ip: 192.0.2.10
      - name: old_tcp_target
        type: tcp_client
        state: absent
    subscriptions: "{{ pfc_subscriptions }}"
    message_log_settings:
      Lwrp: Both
      SapV2Internal: Both
  # Task-level subscriptions and settings apply to alloy_site1. The absent
  # entry ignores them: nothing is configured on a writer being deleted.

- name: Two writers with deliberately different subscription sets
  at_blacknight.pathfinder_core.logs:
    host: pfc-002.example.net
    username: "{{ lookup('env', 'PFC_USER') }}"
    password: "{{ lookup('env', 'PFC_PASS') }}"
    writers:
      - name: alloy_site2
        ip: 192.0.2.20
        subscriptions: "{{ pfc_subscriptions }}"
      - name: audit.log
        type: log_file
        subscriptions:
          - typeid: 1001
            subscription: "sub Devices#0 Connected $MAX_DEPTH=-1"
            severity: Warning
            customname: device-connected
    message_log_settings:
      Lwrp: Both
      SapV2Internal: Both
      LoginFailures: true

- name: Set log rotation, warned about what the startup script will undo
  at_blacknight.pathfinder_core.logs:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    writers:
      - name: alloy_site1
        ip: 192.0.2.10
    rotation:
      max_file_size: 1
      max_count: 3
    startup_script:
      - 'SET Logs#0.LogRotator#0.RotateRule#0 MaxFileSize=100'
      - 'SET Logs#0.LogRotator#0.RotateRule#0 MaxCount=10'
  # => warns that both revert at the next reboot, and applies them anyway
"""

RETURN = r"""
changed:
  description: Whether any write was made, verified by read-back.
  returned: always
  type: bool
plan:
  description:
    - The ordered actions required, across every writer. Populated in check
      mode too, where it is the drift report.
    - >-
      Ordered as it will be executed, which is not input order - every
      C(present) writer, then rotation, then the C(absent) writers. Each entry
      carries C(writer) and C(type), or C(scope=device) for rotation.
  returned: always
  type: list
  elements: dict
  sample:
    - action: subscription_create
      summary: create subscription 1001 (device-connected) on alloy_site1
      destructive: false
      writer: alloy_site1
      type: udp_syslog
      scope: writer
transcript:
  description:
    - Every command issued, in order, with writes marked and passwords
      redacted. Writes show C(skipped=true) in check mode.
  returned: always
  type: list
  elements: dict
writers:
  description:
    - One entry per requested writer, in input order, holding the device's
      state as last read.
  returned: always
  type: list
  elements: dict
  contains:
    name:
      description: The writer's name.
      returned: always
      type: str
    type:
      description: The writer type key.
      returned: always
      type: str
    path:
      description: Its full SapV2 object path.
      returned: always
      type: str
    changed:
      description: Whether this writer had any planned action.
      returned: always
      type: bool
    verified:
      description: Whether this writer was written to and read back successfully.
      returned: always
      type: bool
    exists:
      description: Whether the writer exists on the device.
      returned: always
      type: bool
    properties:
      description: Properties of the writer object.
      returned: always
      type: dict
    subscriptions:
      description: Subscriptions under the writer, keyed by SubscriptionTypeId.
      returned: always
      type: dict
    message_log_settings:
      description: The writer's MessageLogSettings.
      returned: always
      type: dict
unmanaged:
  description:
    - >-
      Writers found on the device that I(writers) did not name - of every type,
      with C(in_purge_scope) marking those a purge would consider and
      C(endpoint) recording where each pointed while that is still readable.
    - >-
      C(action) is C(reported) (in scope, untouched), C(out_of_scope) (a type
      this task does not manage, never touched), C(delete), or C(skipped) (in
      scope but not recreatable, so not deleted).
    - >-
      Always empty when I(unmanaged_writers) is C(ignore), which means "not
      looked for", NOT "there were none".
  returned: always
  type: list
  elements: dict
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
    DEFAULT_WRITER_TYPE,
    INHERITED_KEYS,
    plan_device,
    resolve_writers,
    rotation_writes,
    validate_rotation,
    validate_writers,
)


def subscription_spec():
    # A factory rather than a shared dict: the same spec appears twice (task
    # level and inside writers), and AnsibleModule annotates the spec it is
    # given, so handing it the same object twice invites surprises.
    return dict(
        typeid=dict(type="int", required=True),
        subscription=dict(type="str", required=True),
        severity=dict(type="str", default="Informational",
                      choices=["Informational", "Warning", "Error",
                               "Critical", "Debug"]),
        customname=dict(type="str"),
    )


def report(writer):
    """One WriterPlan as its entry in the `writers` return value."""
    return {
        "name": writer.name,
        "type": writer.type_key,
        "state": writer.state,
        "path": writer.path,
        "changed": writer.changed,
        "verified": writer.verified,
        "exists": writer.actual.get("exists", False),
        "properties": writer.actual.get("properties", {}),
        "subscriptions": writer.actual.get("subscriptions", {}),
        "message_log_settings": writer.actual.get("message_log_settings", {}),
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            port=dict(type="int", default=9600),
            username=dict(type="str", required=True,
                          fallback=(env_fallback, ["PFC_USER"])),
            password=dict(type="str", required=True, no_log=True,
                          fallback=(env_fallback, ["PFC_PASS"])),
            writers=dict(type="list", elements="dict", required=True, options=dict(
                name=dict(type="str", required=True),
                type=dict(type="str", default=DEFAULT_WRITER_TYPE,
                          choices=["udp_syslog", "tcp_client", "tcp_listener",
                                   "log_file"]),
                ip=dict(type="str"),
                port=dict(type="int"),
                properties=dict(type="dict"),
                state=dict(type="str", choices=["present", "absent"]),
                subscriptions=dict(type="list", elements="dict",
                                   options=subscription_spec()),
                subscriptions_purge=dict(type="bool"),
                message_log_settings=dict(type="dict"),
            )),
            state=dict(type="str", default="present", choices=["present", "absent"]),
            subscriptions=dict(type="list", elements="dict", default=[],
                               options=subscription_spec()),
            subscriptions_purge=dict(type="bool", default=False),
            message_log_settings=dict(type="dict"),
            rotation=dict(type="dict"),
            startup_script=dict(type="list", elements="str"),
            unmanaged_writers=dict(type="str", default="ignore",
                                   choices=["ignore", "report", "purge"]),
            on_immutable_change=dict(type="str", default="fail",
                                     choices=["fail", "replace"]),
            idle_timeout=dict(type="float", default=1.5),
            write_timeout=dict(type="float", default=0.35),
            read_timeout=dict(type="float", default=15.0),
        ),
        supports_check_mode=True,
    )

    params = module.params
    writers = resolve_writers(
        params["writers"],
        dict((key, params[key]) for key in INHERITED_KEYS))
    rotation = params["rotation"] or {}

    problems = validate_writers(writers) + validate_rotation(rotation)
    if problems:
        # Fail before opening a session. Every one of these would otherwise be
        # accepted on the wire and silently ignored by the device.
        module.fail_json(msg="invalid desired state: %s" % "; ".join(problems))

    for entry in writers:
        # A log_file writer is named after its file, so a dot is expected there
        # and the device does the bracket-quoting itself.
        if "." in entry["name"] and entry["type"] != "log_file":
            module.warn(
                "writer name %r contains '.', which is SapV2's path separator. "
                "The object will only be addressable as #[%s]. Underscores are "
                "the safer convention." % (entry["name"], entry["name"]))

    client = SapV2Client(
        host=params["host"],
        port=params["port"],
        idle_timeout=params["idle_timeout"],
        write_timeout=params["write_timeout"],
        read_timeout=params["read_timeout"],
        check_mode=module.check_mode,
    )

    result = {"changed": False, "plan": [], "transcript": client.transcript,
              "verified": False, "writers": [], "rotation": {}, "diff": [],
              "will_revert_at_reboot": [], "startup_script_unparsed": [],
              "unmanaged": []}

    try:
        client.connect(params["username"], params["password"])

        planned = plan_device(client, writers, rotation,
                              on_immutable_change=params["on_immutable_change"],
                              unmanaged=params["unmanaged_writers"])

        def read_state():
            """Refresh the parts of the result that a write can change."""
            result["rotation"] = planned.rotation_actual
            result["writers"] = [report(w) for w in planned.writers]
            result["unmanaged"] = planned.unmanaged

        result["plan"] = [action.to_dict() for action in planned.actions]
        # Computed before applying: "before" has to be the state the device was
        # in when the plan was made, and applying replaces those reads.
        result["diff"] = planned.diff()
        read_state()

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

        # Report every blocked action, not just the first: with several writers
        # in one task, fixing them one run at a time is needless.
        if planned.blocked:
            module.fail_json(
                msg="; ".join(a.summary for a in planned.blocked), **result)

        result["changed"] = planned.changed
        if module.check_mode or not planned.changed:
            module.exit_json(**result)

        failures = planned.apply(client)
        read_state()

        if failures:
            pending = sum(len(w.actions) for w in planned.deleting)
            module.fail_json(
                msg=("read-back verification failed%s. %s"
                     % ((", so the %d delete(s) in this plan were NOT performed "
                         "- whatever they would have retired is still in place"
                         % pending) if pending else "",
                        " | ".join(failures))),
                **result)

        result["verified"] = True
        module.exit_json(**result)

    except SapV2Error as exc:
        module.fail_json(msg=str(exc), **result)
    finally:
        client.close()


if __name__ == "__main__":
    main()
