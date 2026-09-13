# Recorded SapV2 replies

Real wire data, captured from two PathfinderCore PRO units and replayed by
`test_wire_fixtures.py`. **Not a simulator.**

That distinction is the whole point. A hand-written fake encodes what the
author believes the device says, which is precisely the thing that keeps
turning out to be wrong here — `$ACK` was documented and misleading,
`TcpClientWriter` was "not creatable" and wasn't, script lines were "dead" and
weren't. Both parser bugs this collection has shipped were invisible to the
hand-written fakes in the other test modules and obvious in a real reply:

| Bug | The shape that caused it |
|---|---|
| Invented property names, dropped access types | `Ready="[ReadWrite=RO,…,UiDescription=\"Becomes True when System starts.\"]"` — an escaped quote inside an already-quoted block, with commas after it |
| `$DONE` folded into the last property's value | `FriendlyName="MessageLogSettings#0" $DONE` — the terminator appended space-separated, not comma-separated |

Neither needed a device to catch. Both needed a *real reply*.

## The two captures

| Directory | Firmware | What it is |
|---|---|---|
| `clean/` | see `FIRMWARE` | A near-factory unit. `Logs#0` holds only `LogRotator#0` — no writers at all, and `RotateRule#0` at the factory `MaxFileSize=1`/`MaxCount=3` |
| `populated/` | see `FIRMWARE` | A working unit: 15 writers including 13 of the device's own log files, a TCP writer, and a UDP writer carrying 27 subscriptions |

Two firmware versions on purpose. Their *schema* replies are byte-identical —
only values differ — which is itself worth knowing, and worth re-checking when
a third firmware appears.

`done_*.txt` are the same reads issued with the `$DONE` terminator, because
that is the path the client actually takes now.

## Sanitisation

These files are in a public repository. Addresses are RFC 5737 documentation
space, hostnames are `example.net`, and writer names are site-neutral. Anything
left is vendor-default — the log file names (`SAPv2Log.log`, `Scenes.log`, …)
ship with the product.

`test_wire_fixtures.py` enforces this, and enforces it with an **allow-list of
shapes** rather than a list of forbidden names. A deny-list would have to spell
out the hostnames and account names being excluded, which puts every one of
them in the public repo — in the file whose whole purpose is to say they are
sensitive. The first draft of that test made exactly that mistake.

## Never capture the Users subtree

🔴 `get Users#0.SapUser#<name>` — and its `Constructor` — return the account's
password hash in the clear. It is Apache MD5 (`$apr1$`), which is weak and
offline-crackable.

A capture of that reply got as far as this directory before being caught. The
address and hostname rules do not help: a hash looks like neither. There is now
an explicit guard for credential *shapes*, and a test asserting the string
`SapUser` appears in no fixture at all — because the safe rule is not to
capture the subtree rather than to capture and scrub it.

The `Constructor` fixture here is from a memory slot for that reason.

## Line endings

The device frames with CRLF and these files keep it, so
`tests/sanity/ignore-2.16.txt` skips the `line-endings` check for this
directory. Rewriting them to LF would make them edited transcripts rather than
captures. No parser depends on CRLF — `splitlines()` handles both — but the
corpus is only worth having if nothing in it has been tidied to match what we
expect.

That ignore file is versioned per ansible-core. Rename it if CI moves off 2.16.

## Adding one

Capture with the raw client (`client.execute(command)`), sanitise, and save the
reply verbatim. Do not tidy a reply up: a capture that has been cleaned is a
fake again — and a fake is what this directory exists to avoid.
