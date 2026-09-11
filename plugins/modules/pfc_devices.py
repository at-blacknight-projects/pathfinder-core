#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Adam Butler
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pfc_devices
short_description: Manage Devices#0 definitions on a PathfinderCore PRO (NOT IMPLEMENTED)
version_added: "0.1.0"
description:
  - B(This module is a stub and always fails.) It exists to hold the design
    constraints for the C(Devices#0) subtree.
  - >-
    Third in the implementation order, after C(pfc_logs) and the
    C(pfc_users)/C(pfc_access) pair. Higher blast radius than either - these
    are the definitions of the audio devices the router controls.
options:
  host:
    description: Hostname or address of the PathfinderCore device.
    type: str
    required: true
author:
  - Adam Butler (@at-blacknight)
notes:
  - "C(Devices#0) is classified MIXED in the boundary registry, not
    declarative. A device's definition is configuration; its liveness and audio
    state are not. C(Connected), C(Online), C(State), C(GAIN), C(PinState),
    C(LastSent), C(LastReceived), C(ResponseData), C(ResponseCode) and
    C(ResponseSuccess) are refused by the guard."
  - "That runtime-property list was derived from the log subscription
    catalogue - those are precisely the properties the device emits events for
    - so it is inference, not measurement. Run C(pfc_survey) against
    C(Devices#0) and reconcile the list against the real schema before
    implementing."
  - "Purge is not safe here and the registry says so. Devices may be created by
    discovery or by other integrations, so deleting definitions this module did
    not create would remove real equipment from the router's control."
  - "Verification must be property-scoped, not whole-object. Something else is
    writing liveness continuously, so re-reading the whole object after a write
    and comparing everything would fail on properties this module never
    touched."
  - "Deleting or re-addressing a device changes what the router can route.
    Treat any destructive action here as a change to broadcast capability and
    default to refusing it, the way pfc_logs defaults to refusing a writer
    replacement."
"""

EXAMPLES = r"""
# Not implemented. See pfc_logs for the reconciler pattern this will follow.
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
        msg="pfc_devices is not implemented. Devices#0 is a MIXED subtree: "
            "definitions are configuration but liveness and audio state are "
            "not, and the runtime-property list in the boundary registry is "
            "inferred from the log subscription catalogue rather than "
            "measured. Run pfc_survey against Devices#0 first, and note that "
            "verification here must be property-scoped because another writer "
            "is updating the same objects continuously.")


if __name__ == "__main__":
    main()
