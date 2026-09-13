#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: access
short_description: Manage System#0.Access#0 security config (NOT IMPLEMENTED)
version_added: "0.1.0"
description:
  - B(This module is a stub and always fails.) It exists to hold the design
    constraints already measured for C(System#0.Access#0).
  - Implemented alongside C(users) - together they are the declarative,
    high-value half of the hardening work.
options:
  host:
    description: Hostname or address of the PathfinderCore device.
    type: str
    required: true
author:
  - Adam Butler (@at-blacknight)
notes:
  - "B(The original premise for this module was wrong.) C(System#0.Access#0)
    was scoped as a declarative subtree needing read-modify-write of a JSON
    blob. Measured on a Core PRO, its only real property C(SecurityJson) is
    B(ReadWrite=RO) - it cannot be written over SapV2 at all. The boundary
    registry classifies this subtree C(read_only) accordingly, and the guard
    refuses writes to it."
  - "So the useful scope here is B(read-only): parse C(SecurityJson) for drift
    detection, audit and reporting. That is still worth having for the
    hardening work - it just is not a reconciler."
  - "The writable access-control surface is elsewhere:
    C(Users#0.SapUser#<name>.UserSecurity) exposes C(IsAdmin),
    C(SecurityPaths), C(MenuItems), C(CanChangeLocks) and C(LocksDoNotApply)
    as discrete RW properties. Enforcement belongs in C(users)."
  - "C(SecurityJson) arrives wrapped in C(%BeginEncap%) markers, which must be
    stripped before parsing. Note the device reports its C(SyntaxType) as
    C(NUM) despite the content being JSON text, so do not trust the declared
    syntax type here."
  - "C(System#0.Access#0.CustomCerts#0) exists as a child but exposed only
    C(FriendlyName) on the surveyed device. Re-survey before assuming it is
    empty in general."
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
    module.fail_json(
        msg="access is not implemented, and will not be a reconciler. "
            "System#0.Access#0.SecurityJson is ReadWrite=RO on measured "
            "firmware, so this subtree cannot be written over SapV2 at all - "
            "the boundary registry classifies it read_only. A read-only "
            "reporter for drift and audit is still worth building. "
            "Enforcement of access control belongs in users, against the "
            "UserSecurity child properties, which are RW.")


if __name__ == "__main__":
    main()
