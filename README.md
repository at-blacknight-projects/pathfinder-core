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
| `logs` | `Logs#0` | **Implemented.** Writers, subscriptions, MessageLogSettings |
| `survey` | any | **Implemented.** Read-only schema introspection |
| `users` | `Users#0` | Stub — schema measured, design unblocked |
| `access` | `System#0.Access#0` | Stub — and it will be a *reporter*, see below |
| `devices` | `Devices#0` | Stub |

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
      at_blacknight.pathfinder_core.logs:
        host: "{{ inventory_hostname }}"
        username: "{{ lookup('env', 'PFC_USER') }}"
        password: "{{ lookup('env', 'PFC_PASS') }}"
        writers:
          - name: alloy_site1
            ip: 192.0.2.10
        subscriptions: "{{ pfc_subscriptions }}"
      check_mode: true
```

Credentials fall back to `PFC_USER` / `PFC_PASS`, which is how AWX custom
credential types, Semaphore secret survey vars and CI variables should inject
them.

## The device's writers are reconciled together

`logs` takes the whole `writers` list rather than one writer per task. Two
of the reasons are ordinary — one login instead of one per writer, and one plan
and one diff for the device — but the third is the one that matters:

```yaml
    - name: Cut over to the new receiver
      at_blacknight.pathfinder_core.logs:
        host: "{{ inventory_hostname }}"
        writers:
          - name: alloy_site1          # the replacement
            ip: 192.0.2.10
          - name: legacy_collector     # the writer it replaces
            type: tcp_client
            state: absent
        subscriptions: "{{ pfc_subscriptions }}"
```

That runs in three phases: every `present` writer is created and configured,
**all of it is read back**, and only then are the `absent` writers deleted. If
the new writer did not come up, the old one is still there and the task fails
saying so.

Separate tasks cannot express that. By the time a delete task ran, the create
task would already have reported success — and on this protocol a create that
the device silently ignored looks exactly like one that worked.

`state`, `subscriptions`, `subscriptions_purge` and `message_log_settings` may
be set per writer or once at task level. Omitting one inherits the task-level
value; setting it to an empty list or dict does not, so a writer can opt out of
a shared catalogue with `subscriptions: []`.

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

191 tests, no device and no Ansible required. `tests/unit/loader.py` handles
importing the collection outside an `ansible_collections/` tree.

That coverage is only possible because the reconcilers live in `module_utils`
and take a client rather than an `AnsibleModule` — including the phase ordering
above, which is exercised against a fake device that accepts writes and ignores
them, the way real hardware fails.

For `ansible-test`, the repo must be checked out at a path ending
`ansible_collections/at_blacknight/pathfinder_core/`.

## Installing a local build

`ansible-galaxy collection install` takes a path, and a glob over the build
directory will happily match an OLD artifact without saying so - you get a
stale collection and a confusing "Unsupported parameters" error from a module
that does support them. Always clear the directory first:

```bash
rm -f *.tar.gz
ansible-galaxy collection build --force
ansible-galaxy collection install ./at_blacknight-pathfinder_core-*.tar.gz   -p /path/to/consuming-repo/collections --force
```

## Versioning

Pre-1.0, on the `alpha` prerelease channel. Every `alpha.N` is a prerelease of
the same unreleased `0.1.0`, so a `feat` advances the prerelease counter rather
than the base version.

Do NOT use `feat!:` or a `BREAKING CHANGE:` footer while pre-1.0. semantic-
release treats either as a major bump unconditionally, which lands on `1.0.0`
and claims an API stability this collection does not have. Describe the
incompatibility in the body instead - and note the footer is matched
case-insensitively at the start of any line, so a wrapped sentence beginning
"breaking change" is enough to trigger it.

## Status

Version 0.1.0. `logs` has been exercised end to end against a Core PRO:
check-mode planning, create, read-back verification, idempotent re-run,
refusal of an immutable endpoint change, detection of a silently-ignored
value, read-only-field replacement, and delete.

## Licence

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
