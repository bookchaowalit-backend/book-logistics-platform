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
- Target remote: `https://github.com/bookchaowalit-backend/book-logistics-platform.git` (published; runtime not activated)
- Contract: `book-platform.contract.v1`
- Status: `scaffolded`
- Data owner: the platform boundary identified in `contract.json`

The platform communicates through versioned API or event contracts. Consumers
must not import another platform's database, migration, or private runtime
module. `solo-empire` remains the control plane and compatibility adapter until
parity and rollback evidence permit a cutover.

## Local verification

Run from this repository (Python 3.11+, no third-party packages):

```bash
bash scripts/check.sh
```

The check validates `contract.json` against
[`schema/book-platform.contract.v1.schema.json`](schema/book-platform.contract.v1.schema.json),
confirms that the README and [`docs/CONTRACT-SURFACE.md`](docs/CONTRACT-SURFACE.md)
agree with the contract, and runs the unit tests in `tests/`. GitHub Actions
runs the same command on every push and pull request
(`.github/workflows/check.yml`).

The check validates repository shape and contract metadata only. It does not
claim deployment, provider connectivity, data migration, or production
readiness.

## Contract surface

Dependencies: `identity`, `observability`. The interfaces this boundary owns or consumes, with
their auth, idempotency and error behaviour, are listed in
[`docs/CONTRACT-SURFACE.md`](docs/CONTRACT-SURFACE.md). Planned work is tracked
in [`docs/UPGRADE-PLAN.md`](docs/UPGRADE-PLAN.md).

## Migration gate

Before activating a remote or changing a consumer, add sanitized fixtures for
success, duplicate delivery, timeout and provider failure; prove tenant/privacy
isolation; compare the old and new contract; and rehearse rollback on a
disposable state store. Record the evidence in the parent platform registry.
