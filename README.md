# platform-logistics

`Logistics` platform boundary for the Book Platform portfolio.

## Scope

This repository owns the `logistics` capability: carrier adapters, shipment, shipping rates, labels, tracking.
The current source implementation is recorded as `none` in the
parent registry. This checkout includes an optional, locally implemented runtime pilot with a
sandbox carrier only. Its contract remains scaffolded pending activation evidence; it holds no
real carrier integration, credential or customer payload, and no production activation is claimed.

## Boundary

- Owner: `bookchaowalit-backend`
- Repository: `platform-logistics`
- Target remote: `https://github.com/bookchaowalit-backend/platform-logistics.git` (published; runtime not activated)
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

`scripts/check_schema_pin.py` fails when the vendored schema no longer
matches the sha256 pinned in `schema/book-platform.contract.v1.schema.json.sha256`
(the canonical copy lives in `bookchaowalit-backend-core/contracts/`; change it
there first, then copy the schema and its pin here). Pass
`--canonical ../bookchaowalit-backend-core` to also compare with a local
checkout. `python3 scripts/check_registry_alignment.py --solo-empire ../solo-empire`
compares this contract with the parent platform registry (local only; read-only).

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

## Runtime pilot — implemented locally, staged only

A Python 3.12+ / PostgreSQL 16+ runtime on `platform-backend-core`: workspace JWT scopes,
forced row-level security, idempotent shipments, deterministic sandbox rates, replay-safe
label purchase with an uncertain quarantine, and tracking events deduplicated by carrier
event id. See the [API and operating guide](docs/runtime.md). The original contract,
schema pin and offline checks are unchanged. Nothing has been deployed.
