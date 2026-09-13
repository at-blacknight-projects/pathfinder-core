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
naive probe looks like a dead port.

A children listing returns paths with **empty property dicts** — measured, the
child entries carry no properties at all. Fetching each child individually is
one way to fill them in; `$MAX_DEPTH` below is the better one.

## Framing: `$DONE` terminates a reply

A reply has no terminator *by default*, so an idle gap is the fallback frame
boundary and an unterminated command costs at least the client's idle timeout.
**Appending `$DONE` to a read removes that**: the device echoes the token on the
last line of the reply and nowhere else, so the read returns as soon as it lands.

Measured on a Core PRO: first byte in ~0.04s, whole reply within ~0.05s, against
a 1.5s idle timeout — so roughly 97% of every read was spent waiting to find out
the reply had already finished.

It also removes a guess. The reply misattribution this client was bitten by —
`get System#0` returning another object's properties — was fundamentally not
knowing where one reply ended and the next began.

Three things to know:

- **It corrupts the last property unless stripped.** The token is appended after
  the last property *space*-separated, not comma-separated, so a naive parse
  folds it into that value: `FriendlyName="MessageLogSettings#0" $DONE`. On a
  subscription that means `Subscription` never matches the desired expression
  again.
- **An empty result is terminated too**: an unknown path answers
  `indi NONE $DONE`, so even a miss returns immediately. Reading a writer that
  does not exist is the hot path of every create.
- **It does nothing for writes.** Measured: a successful write, a write of a
  silently ignored value, a write to an unknown property and a write to an
  unknown path all return *absolutely nothing*, with or without the modifier.

## `$MAX_DEPTH` reads a subtree in one command

`get <path> $MAX_DEPTH=-1` returns every object below `<path>`, with properties.
One writer with 27 subscriptions is 30 commands read child-by-child and one this
way; the whole of `Logs#0` — 118 objects across 16 writers — comes back as a
single 37KB reply.

⚠️ **It means strictly BELOW.** The reply does *not* include the object named in
the request, so a caller looking for its own path finds nothing and concludes the
object does not exist. That failure is silent and it planned a redundant create
before it was spotted. Read the object itself separately, or query the parent.

Valid on `get`, `sub` and `rfs`.

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
init MemorySlots#0.LatchingMemorySlot slotName=Standby_Mode_Latch
init LogicFlows#0.LogicFlowFolder FolderName=ARCHIVE
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
Devices#0.Fusion#[tcp://192.0.2.40:93]
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
