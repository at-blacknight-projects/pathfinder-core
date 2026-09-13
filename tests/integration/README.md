# Live smoke test

One playbook, run by hand against real hardware before cutting a release.

```bash
export PFC_USER=... PFC_PASS=...
ansible-playbook tests/integration/smoke.yml -e pfc_host=<address>
```

~60 seconds. It creates four writers, exercises them, and removes them.

## What it is for

The unit tests and the recorded fixtures both test the code against beliefs
about the device. They catch regressions in parsing and planning, which is
worth a lot — but they cannot catch a belief that is simply wrong, because they
were written and captured under those beliefs.

This collection's history is mostly wrong beliefs: `$ACK` was documented and
misleading, `TcpClientWriter` was "not creatable" and was, script lines were
"dead" and were not. Each was found by touching hardware and being contradicted.

So this playbook asserts the things that would silently change if the firmware
did: that each writer type's init form still creates, that a read-back still
catches a value the device accepts and discards, that an endpoint change is
still refused, that a `tcp_listener` without a port is still refused rather
than left as a propertyless object.

**It found a real bug on its first run**, which is the argument for having it:
the device echoes the request's `$MAX_DEPTH` onto every reply line, and only
`$DONE` was being stripped, so the modifier was landing inside the last
property's value.

## Why it is not an `ansible-test integration` target

It needs a physical device, so CI cannot run it under any harness. With one
target and no CI path, the harness adds ceremony and no coverage. A playbook is
what a person actually runs.

## The safety rules, and why each exists

Every one of these is here because something went wrong without it.

**A reserved prefix.** Everything it creates is named `zzitest_`, so its
objects are obvious to anyone else looking at the device.

**It refuses to start if anything with that prefix already exists.** A run that
dies mid-way leaves objects behind; running again on top of them makes the
results meaningless. This check exists because a hand-run script left three
writers on a shared device and the person who noticed was not the one who put
them there.

**Teardown runs on failure, and is verified.** Deletion is confirmed by reading
back, because a read taken straight after a write on this device has been
observed returning empty for an object that exists — so trusting the first read
is how debris gets left behind while the script reports success.

**It restores device-scoped state, not just its own objects.** Rotation belongs
to the device rather than to any writer, so deleting the test writers does not
undo it. The first version of this playbook set rotation and left it set,
permanently altering a device it had only borrowed. It now records the values
in preflight and puts them back.

**It asserts on values, never on `changed`.** A test that demands `changed`
passes once and fails on re-run against a device already holding the desired
state — which also means it only works on a pristine device, which is exactly
the device you will not have.

**It never touches what it did not create.** The module defaults to
`unmanaged_writers: ignore`, and the preflight records the writer list so
teardown can assert the device ends with the same one it started with.

## Choosing a device

A dedicated or near-factory unit. Not a shared sandbox: even with every rule
above, this creates and deletes objects, and someone else's test running at the
same time will confuse both of you.

`tcp_client` creation is deliberately included and takes ~30 seconds on its
own — the device dials out on creation and its replies queue behind that. That
is the device, not a timeout to tune.
