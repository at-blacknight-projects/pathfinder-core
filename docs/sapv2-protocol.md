# SapV2 protocol notes

Measured against a Telos PathfinderCore PRO (firmware reporting `SubVersion`
2026-03-14) over raw TCP port 9600. Where this disagrees with the vendor
documentation, the device won.

Vendor reference: <https://docs.telosalliance.com/docs/v110-appendix-a-sapv2.md>

## Framing

```
connect
send  "\r\n"                     flushes any partial line in the device parser
send  "Login <user> <pass>\r\n"  capitalised, CRLF
send  "<command>\r\n"            per command
```

Replies are `indi <path> Prop="value", Prop2="value"`. Appending `.` to a path
lists children. An unknown path replies `indi NONE`.

The port sends **no banner** and ignores commands issued before login, so a
naive probe looks like a dead port. There is no reply terminator either, so an
idle gap is the only frame boundary — which is why every command costs at least
the client's idle timeout.

A children listing returns paths with **empty property dicts**, so the
properties of each child must be fetched individually.

Operators: `SET GET INDI SUB UNSUB RFS SFR INIT NEW DEL LED NOP SYNC LOGIN`,
plus untargeted `LOGOUT EXIT QUIT`. Note `constructor` is **not** an operator.

## Writes are not acknowledged, and `$ACK` does not fix that

A successful `set` and a rejected one both return nothing.

The protocol offers a `$ACK` modifier. It is not a solution. Measured:

| Command | Reply | Took effect? |
|---|---|---|
| `set <mls> Lwrp=Incoming $ACK=True` | `ack <mls> Lwrp=Incoming` | yes |
| `set <mls> Lwrp=In $ACK=True` | `ack <mls> Lwrp=In` | **no** |
| `set <mls> NotAProperty=True $ACK=True` | `ack <mls> NotAProperty=True` | **no** |
| `set <writer> Name=nope $ACK=True` (RO property) | `ack <writer> Name=nope` | **no** |
| `set Logs#0.Nonexistent#9 Foo=1 $ACK=True` | *(nothing)* | no |

An ack means "I parsed this", never "I applied this". It only distinguishes a
valid path from an invalid one. **Read-back verification is the only reliable
failure detection.**

`MessageLogSettings` enum values are `None | Incoming | Outgoing | Both`.
`In` and `Out` are the trap above.

## Error replies

There *is* an explicit error reply for a rejected operation:

```
error Logs#0 $OP=constructor $STATUS="Unsupported Operation."
```

Note the detail fields are **space-separated**, unlike the comma-separated
property lists in an `indi` reply. This covers refused verbs and bad paths, but
not silently-ignored values, so it supplements read-back rather than replacing
it.

## `rfs` — runtime schema introspection

`rfs <path> <Prop>` reports one property; **`rfs <path>` with no property name
dumps the whole object's schema**, as
`sfr <path> Prop=[ReadWrite=RW,SyntaxType=TXT,UiDescription="...",IsStable=True]`.

Access types are `RO`, `RW` and **`WO`** (write-only). This is the only runtime
introspection the device offers.

`rfs` on a *type* with no instance returns `sfr NONE`, so it cannot tell you
what is creatable.

### Write-only properties are actions, not state

All 27 WO properties found across 65 object types are imperative operations:
`ForceServiceRestart`, `SubmitSapMessage`, `Reconnect`, `SendLwrpCommand`,
`SendLwcpCommand`, `ClearLogFile`, `DeleteLogFile`, `CopyTo`, `CloneTo`,
`CopyValue`, `ChangeAllByValue`, `Append`, `Trigger`, `WriteSlot`, `Pulse`,
`PulseValue`, `RotateSource`, `RemoveDeviceIp`, `ActivateScene`,
`SendCriticalMessage`, `ClearElapsed`, `WriteTimer`.

The device models RPC calls as write-only properties. They cannot be read back
and must never be reconciled.

## `Constructor` — the hidden property

`Constructor` is a **property, not an operator**. It does not appear in an
object's property list (absent from all 225 distinct properties across 65
surveyed types) and must be requested by name:

```
get MemorySlots#0.MemorySlot#Whatever Constructor
```

Where supported it returns the literal `init` command that would recreate the
object, wrapped in `%BeginEncap%`:

```
init Users#0.SapUser Username=Admin
init Users#0.SapUser#Admin.UserSecurity Name=Admin
init Routers#0.AxiaAudioRouter id=1
init Routers#0.SapPropertyRouter id=10
init MemorySlots#0.LatchingMemorySlot slotName=Cyclone_Mode_Latch
init LogicFlows#0.LogicFlowFolder FolderName=COPIES
```

Issuing `constructor <path>` as a verb instead returns
`error <path> $OP=constructor $STATUS="Unsupported Operation."`.

Supported on 8 of 15 probed objects. **Not** supported on the `Logs#0` writer
and subscription family, which is why those init forms had to be established
empirically.

## `init` parameter names are per-type and are not the property names

This is the single most expensive thing to rediscover. The vendor docs define
`INIT {Object} {ConstructorProperty}={Value}` — so the init parameters are the
*Constructor* property's names, and those vary wildly by type:

| Type | Init parameters |
|---|---|
| `Logs#0.UdpSysLogWriter` | `name=`, `ip=` (comma-separated) |
| `Logs#0...LogSubscription` | `subscription=`, `typeid=`, `severity=`, `customname=` |
| `Users#0.SapUser` | `Username=` |
| `Routers#0.AxiaAudioRouter` | `id=` |
| `MemorySlots#0.LatchingMemorySlot` | `slotName=` |
| `LogicFlows#0.LogicFlowFolder` | `FolderName=` |

Using the object's readable property names where they differ is a **silent
no-op** that looks exactly like "this object type cannot be created". Use
`Constructor` to discover them; fall back to empirical probing where it is
unsupported.

Quoting follows the proven forms rather than a general rule: this collection
emits `name=`, `ip=`, `typeid=`, `severity=` and `port=` bare and quotes
everything else, because a value like `device-gain` needs no escaping yet the
working command quotes it, and there is no safe way to test which the device
actually requires.

## Path syntax

`.` is the separator, so an object whose name contains a dot must be
bracket-quoted. The device emits this form itself:

```
Logs#0.LogFileWriter#[Connected_Msg.log]
Devices#0.Fusion#[tcp://192.168.4.11:93]
```

Estate convention is to avoid the problem: use underscores in names you create.

## `%BeginEncap%`

A general payload wrapper, not specific to any subtree. Seen on `Constructor`
and on `System#0.Access#0.SecurityJson`. Strip before parsing.

## Subtree findings

- **`Logs#0`** — fully provisionable. Writer `Name` and `RemoteEndpointUri`,
  and subscription `CustomName`/`Severity`/`SubscriptionTypeId` are RO after
  creation; subscription `Subscription` and all ten `MessageLogSettings`
  properties are RW. A writer with no subscriptions and default (all-off)
  MessageLogSettings is **inert**, so creating one is safe on production.
  `Connected=True` on a UDP writer means nothing — there is no connection.
- **`Users#0.SapUser`** — `Username` RO, `Password` **RW**. A read returns the
  `$apr1$` Apache MD5 hash in the clear, so any credential with API read access
  can harvest every admin hash. Verifying a password write means recomputing
  the hash with its embedded salt, not comparing strings.
- **`Users#0.SapUser#<n>.UserSecurity`** — `IsAdmin`, `SecurityPaths`,
  `MenuItems`, `CanChangeLocks`, `LocksDoNotApply` all RW. This is the real
  writable access-control surface.
- **`System#0.Access#0.SecurityJson`** — **`ReadWrite=RO`**. It cannot be
  written over SapV2 at all, so this subtree is a reporter, not a reconciler.
  Its `SyntaxType` is reported as `NUM` despite holding JSON text.
