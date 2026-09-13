#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: users
short_description: STUB, always fails - SapV2 accounts on a PathfinderCore PRO
version_added: "0.1.0"
description:
  - B(This module is a stub and always fails.) It exists to hold the design
    constraints already measured for the C(Users#0) subtree so that whoever
    implements it does not rediscover them.
  - Second in the implementation order after C(logs), chosen for value
    rather than blast radius - account and access control is what a hardening
    programme actually wants.
options:
  host:
    description: Hostname or address of the PathfinderCore device.
    type: str
    required: true
author:
  - Adam Butler (@at-blacknight)
notes:
  - "SECURITY: C(get Users#0.SapUser#<name>) returns the account's password
    hash in the clear - Apache MD5 (C($apr1$)), which is weak and
    offline-crackable. Any credential with API read access can harvest every
    admin hash. Any implementation must mark reads of this subtree C(no_log)
    and must never place a hash in a return value, a diff or a transcript."
  - "MEASURED SCHEMA (Core PRO): C(SapUser) exposes C(Username) B(RO),
    C(Password) B(RW), plus C(FriendlyName)/C(SapObjectType)/C(SubVersion) RO.
    Password is read-write, not write-only, so a password change CAN be
    verified - but a read returns the stored C($apr1$) hash, not the
    plaintext. Verification therefore means recomputing Apache MD5 with the
    salt embedded in the returned hash and comparing, not comparing strings.
    A weaker fallback is asserting the hash changed."
  - "C(Username) being read-only makes a rename a delete-and-recreate."
  - "The real access-control surface is the C(UserSecurity) child, not the
    user: C(IsAdmin) (BOL), C(SecurityPaths) (TXT), C(MenuItems) (TXT),
    C(CanChangeLocks) (BOL) and C(LocksDoNotApply) (BOL) are all B(RW). These
    are discrete properties, which makes them far more tractable than the
    single-blob model C(System#0.Access#0) turned out to have."
  - "INIT PARAMETERS, read off the device's own C(Constructor) hidden
    property: C(init Users#0.SapUser Username=<name>) and
    C(init Users#0.SapUser#<name>.UserSecurity Name=<name>). Do not guess
    these - the vocabulary varies per type across the tree
    (C(Username), C(id), C(slotName), C(FolderName)) and a wrong name is a
    silent no-op."
  - "Known objects: C(SapUser#<name>) (observed: Admin, ClusterAdmin, panels),
    C(WhiteList#0) and C(GuidTokens#0)."
  - "SECURITY: reading a SapUser returns its password hash in the clear, so
    every read here is secret. Never place a hash in a return value, a diff or
    the command transcript."
  - "Deleting or locking out the account being authenticated with will end the
    run mid-flight. Guard against operating on the connected user explicitly."
"""

EXAMPLES = r"""
# Not implemented. See logs for the reconciler pattern this will follow.
"""

RETURN = r"""
msg:
  description: Why the module refused to run.
  returned: always
  type: str
"""

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(host=dict(type="str", required=True)),
        supports_check_mode=True,
    )
    # Fails rather than no-ops on purpose. A stub that quietly reports
    # changed=false would let a playbook claim it had reconciled accounts.
    module.fail_json(
        msg="users is not implemented. The schema is now measured and the "
            "design is unblocked: SapUser has Username RO and Password RW, "
            "the access-control properties (IsAdmin, SecurityPaths, "
            "MenuItems) live on the UserSecurity child and are all RW, and "
            "the init forms are 'init Users#0.SapUser Username=<name>' and "
            "'init Users#0.SapUser#<name>.UserSecurity Name=<name>'. The "
            "remaining design question is password verification: a read "
            "returns the $apr1$ hash, so verifying a password write means "
            "recomputing Apache MD5 with the embedded salt. See the module "
            "documentation.")


if __name__ == "__main__":
    main()
