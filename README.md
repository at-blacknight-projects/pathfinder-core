# at_blacknight.pathfinder_core

Ansible collection for managing **Telos/Axia PathfinderCore PRO** broadcast
router-control appliances over their **SapV2 API** (raw TCP, port 9600).

> PathfinderCore is broadcast-critical equipment. This collection is built on
> the assumption that a wrong write is worse than no write, and the code is
> shaped accordingly: default-deny on which parts of the tree may be touched,
> read-back verification on every change, and destructive operations refused
> unless explicitly asked for.

## Why this is not a generic "PFC resource" module

It would be easy to ship one module taking an arbitrary object path and a bag
of properties. That module would have no idempotency guarantees and no safety
rails, on equipment where a bad write changes what is on air. Instead:

**Layer 1 — a thin generic SapV2 client** (`plugins/module_utils/sapv2.py`):
connect, login, `get`/`set`/`init`/`del`, `rfs`, `Constructor`, and reply
parsing. Built once, shared, and dependent on nothing but the standard library
so it can be unit-tested without Ansible.

**Layer 2 — per-subtree reconcilers that opt in**, each with its own identity
key, its own immutability rules and its own verification scope.

| Module | Subtree | Status |
|---|---|---|
| `pfc_logs` | `Logs#0` | **Implemented.** Writers, subscriptions, MessageLogSettings |
| `pfc_survey` | any | **Implemented.** Read-only schema introspection |
| `pfc_users` | `Users#0` | Stub — schema measured, design unblocked |
| `pfc_access` | `System#0.Access#0` | Stub — and it will be a *reporter*, see below |
| `pfc_devices` | `Devices#0` | Stub |

Logs was implemented first deliberately: the worst case for getting it wrong is
losing logs, not losing air.

## The runtime/config boundary

Nothing in the object model marks which subtrees are configuration.
`Logs#0...RemoteEndpointUri` and `MemorySlots#0...SlotValue` are the same kind
of thing to the API — a writable property — but the first is a human's choice
and the second is live state rewritten continuously by another system. A
reconciler enforcing desired state on the second would fight that system
forever, and on `Routers#0...CurrentSourcePath` it would re-route air.

`plugins/module_utils/subtrees.py` states the boundary explicitly and
**enforces it in the client's write path**, so a reconciler cannot forget to
check. It is default-deny: an unlisted path is refused.

Crucially the boundary runs **through** subtrees, not around them. A
MemorySlot's `SlotName` and `Persistent` are configuration; its `SlotValue` is
not. Such subtrees are classified `MIXED` and name their runtime properties.

Two further rails, both derived from measurement rather than guesswork:

- **Write-only properties are refused.** Every WO property on the device is an
  imperative action, not state — `ActivateScene`, `ForceServiceRestart`,
  `SendLwrpCommand`, `ClearLogFile`, `Trigger`. Setting one would invoke it on
  every run, and it cannot be read back to verify.
- **`purge_safe`** is false wherever another system creates objects in the
  subtree, so a reconciler never deletes what it did not create.

## Read-back verification is not optional

**SapV2 does not tell you when a write fails.** A successful `set` and a
rejected one both return nothing. The classic trap is `Lwrp=In`: a plausible
value that the device accepts on the wire, silently discards, and never
mentions again.

The protocol does offer a `$ACK` modifier. **This collection deliberately does
not use it.** Measured on a Core PRO, `set ... $ACK=True` returns
`ack <path> <prop>=<value>` for an invalid enum, for a property that does not
exist, and for a read-only property — it goes silent only for an unknown
*path*. An ack means "I parsed this", never "I applied this", which makes it
more dangerous than silence because it looks like confirmation.

So every module re-reads the device after writing and fails if the desired
state did not take. A reported `changed=true` always means verified.

## Quick start

```yaml
- name: Reconcile PathfinderCore log shipping
  hosts: pathfinder_core
  gather_facts: false
  connection: local          # devices are not Ansible hosts
  tasks:
    - name: Drift report (writes nothing)
      at_blacknight.pathfinder_core.pfc_logs:
        host: "{{ inventory_hostname }}"
        username: "{{ lookup('env', 'PFC_USER') }}"
        password: "{{ lookup('env', 'PFC_PASS') }}"
        writer:
          name: alloy_sca1
          ip: 172.22.215.236
        subscriptions: "{{ pfc_subscriptions }}"
      check_mode: true
```

Credentials fall back to `PFC_USER` / `PFC_PASS`, which is how AWX custom
credential types, Semaphore secret survey vars and CI variables should inject
them.

## Protocol notes

The hard-won details — framing, why `init` parameter names are not property
names, the `Constructor` hidden property, the error reply format, and the
`$ACK` measurement — are in [`docs/sapv2-protocol.md`](docs/sapv2-protocol.md).
Read it before extending anything.

## Testing

The module_utils depend only on the standard library, so:

```bash
python -m unittest discover -s tests/unit -t tests/unit
```

83 tests, no device and no Ansible required. `tests/unit/loader.py` handles
importing the collection outside an `ansible_collections/` tree.

For `ansible-test`, the repo must be checked out at a path ending
`ansible_collections/at_blacknight/pathfinder_core/`.

## Status

Version 0.1.0. `pfc_logs` has been exercised end to end against a Core PRO:
check-mode planning, create, read-back verification, idempotent re-run,
refusal of an immutable endpoint change, detection of a silently-ignored
value, read-only-field replacement, and delete.

## Licence

GPL-3.0-or-later. **The `LICENSE` file still needs the full licence text** —
see `LICENSE` for the one-line command that fetches it.
