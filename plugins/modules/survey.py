#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: survey
short_description: READ-ONLY schema survey of a PathfinderCore object tree
version_added: "0.1.0"
description:
  - Walks a PathfinderCore PRO object tree and records each object type's
    schema using C(rfs), which reports C(ReadWrite) and C(SyntaxType) per
    property and is the only runtime introspection the device offers.
  - Issues only read verbs, so it is non-destructive. Note that a device with
    C(MessageLogSettings.AuditGet) enabled will log every read, so a full sweep
    against such a device produces a burst of syslog traffic.
  - Surveys one representative instance per object type rather than every
    instance, because schema is a property of the type. This keeps a tree with
    thousands of devices to a few dozen commands.
  - Reports C(write_only) properties separately. A write-only property cannot
    be read back, so on a protocol that does not acknowledge writes it can
    never be verified - a reconciler should refuse to manage one rather than
    claim an unverifiable success.
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
  roots:
    description:
      - Object tree roots to survey. Defaults to the known top-level subtrees.
      - Kept explicit rather than discovered so a survey cannot wander into
        something expensive by accident.
    type: list
    elements: str
  max_instances_per_type:
    description: How many instances of each type to run C(rfs) against.
    type: int
    default: 1
  max_children:
    description:
      - Cap on children expanded per object. Use it to bound a first survey of
        a large tree.
    type: int
  probe_constructor:
    description:
      - Also issue C(constructor) against each sampled object. The vendor
        documentation claims this returns the C(init) messages needed to
        recreate an object, which would give the init parameter names for free.
        It returns nothing on the Logs subtree; whether it works elsewhere is
        exactly what this probe answers.
    type: bool
    default: true
  idle_timeout:
    description: Seconds of socket silence taken to mean a reply is complete.
    type: float
    default: 1.5
  read_timeout:
    description: Ceiling on collecting a single reply.
    type: float
    default: 15.0
author:
  - Adam Butler (@at-blacknight)
"""

EXAMPLES = r"""
- name: Survey the sandbox device and save the snapshot
  at_blacknight.pathfinder_core.survey:
    host: pfc-sandbox.example.net
    username: "{{ lookup('env', 'PFC_USER') }}"
    password: "{{ lookup('env', 'PFC_PASS') }}"
  register: pfc_schema

- name: Write the snapshot out as a test fixture
  ansible.builtin.copy:
    content: "{{ pfc_schema.snapshot | to_nice_json }}"
    dest: ./pfc-schema-{{ inventory_hostname }}.json
  delegate_to: localhost

- name: Show every property that can never be verified
  ansible.builtin.debug:
    var: pfc_schema.write_only

- name: Bounded first look at a large tree
  at_blacknight.pathfinder_core.survey:
    host: "{{ inventory_hostname }}"
    username: "{{ pfc_username }}"
    password: "{{ pfc_password }}"
    roots:
      - Devices#0
    max_children: 5
    probe_constructor: false
"""

RETURN = r"""
snapshot:
  description:
    - Schema per object type, keyed by type path. Each entry carries the
      sampled instance, the raw C(rfs) schema, properties grouped by access
      type, and the C(constructor) reply if one was probed.
  returned: always
  type: dict
write_only:
  description:
    - Every write-only property found, as C(type.property) pairs. These cannot
      be read back and therefore cannot be verified.
  returned: always
  type: list
  elements: list
constructor_supported:
  description:
    - Type paths where C(constructor) returned something other than nothing.
      An empty list confirms the behaviour already observed on the Logs
      subtree.
  returned: always
  type: list
  elements: str
types_seen:
  description: Number of distinct object types surveyed.
  returned: always
  type: int
"""

from ansible.module_utils.basic import AnsibleModule, env_fallback

from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils.sapv2 import (
    SapV2Client,
    SapV2Error,
)
from ansible_collections.at_blacknight.pathfinder_core.plugins.module_utils.survey import (
    survey,
    unverifiable_properties,
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
            roots=dict(type="list", elements="str"),
            max_instances_per_type=dict(type="int", default=1),
            max_children=dict(type="int"),
            probe_constructor=dict(type="bool", default=True),
            idle_timeout=dict(type="float", default=1.5),
            read_timeout=dict(type="float", default=15.0),
        ),
        # Read-only by construction, so check mode is a no-op rather than a
        # different code path.
        supports_check_mode=True,
    )

    params = module.params
    client = SapV2Client(
        host=params["host"],
        port=params["port"],
        idle_timeout=params["idle_timeout"],
        read_timeout=params["read_timeout"],
    )

    try:
        client.connect(params["username"], params["password"])
        snapshot = survey(
            client,
            roots=params["roots"],
            max_instances_per_type=params["max_instances_per_type"],
            probe_constructor=params["probe_constructor"],
            max_children=params["max_children"],
        )
    except SapV2Error as exc:
        module.fail_json(msg=str(exc))
    finally:
        client.close()

    module.exit_json(
        changed=False,
        snapshot=snapshot,
        write_only=[list(pair) for pair in unverifiable_properties(snapshot)],
        constructor_supported=sorted(
            t for t, entry in snapshot.items() if entry.get("constructor")),
        types_seen=len(snapshot),
    )


if __name__ == "__main__":
    main()
