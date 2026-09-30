# Contract surface: book-logistics-platform

This page documents the API, event, CLI, storage and configuration surface that
`book-logistics-platform` owns or consumes. The machine-readable list is `interfaces` in
[`contract.json`](../contract.json); `scripts/check.py` fails when an interface
listed there is missing from this page.

- Platform: `logistics` (contract `book-platform.contract.v1`, API `v1`)
- Status: `scaffolded`; source status: `new-boundary`
- Current implementation: `solo-empire` at none
- Depends on: `identity`, `observability`

Interfaces marked `current-in-solo-empire` are served by the `solo-empire`
control plane today. This repository does not serve them yet; they are the
compatibility surface a migration must preserve. The `source` column is a path
in the implementing repository.

## Interfaces

No interface is declared yet (see notes below).

## Behaviour notes

Derived from the current source; re-check the source before changing a
consumer.

- Logistics is a new boundary (`source_status: new-boundary`, `source_path:
  none`). No current implementation exists in `solo-empire`, so no interface is
  declared.
- Carrier integrations are sandbox or partner adapters only; the platform does
  not own a carrier fleet or map dataset.

## Migration gates

- carrier sandbox
- label replay
- tracking deduplication

## Out of scope

No production provider integration, credential, database writer or customer
payload lives in this repository. Record parity, privacy and rollback evidence
in the parent platform registry before any cutover.
