# book-logistics-platform

`Logistics` platform boundary for the Book Platform portfolio.

## Scope

This repository owns the `logistics` capability: carrier adapters, shipment, shipping rates, labels, tracking.
The current source implementation is recorded as `none` in the
parent registry. This checkout is a local scaffold; it contains no production
provider integration, database writer, credential, or customer payload.

## Boundary

- Owner: `bookchaowalit-backend`
- Repository: `book-logistics-platform`
- Target remote: `https://github.com/bookchaowalit-backend/book-logistics-platform.git` (not created by the bootstrap)
- Contract: `book-platform.contract.v1`
- Status: `scaffolded`
- Data owner: the platform boundary identified in `contract.json`

The platform communicates through versioned API or event contracts. Consumers
must not import another platform's database, migration, or private runtime
module. `solo-empire` remains the control plane and compatibility adapter until
parity and rollback evidence permit a cutover.

## Local verification

Run from this repository:

```bash
bash scripts/check.sh
```

The check validates repository shape and contract metadata only. It does not
claim deployment, provider connectivity, data migration, or production
readiness.

## Migration gate

Before activating a remote or changing a consumer, add sanitized fixtures for
success, duplicate delivery, timeout and provider failure; prove tenant/privacy
isolation; compare the old and new contract; and rehearse rollback on a
disposable state store. Record the evidence in the parent platform registry.
