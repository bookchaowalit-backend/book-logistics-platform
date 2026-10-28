# Logistics runtime pilot

Status: implemented and locally verified; deployment artifacts staged only.
No production activation, real carrier integration or carrier credential exists.
The contract's `scaffolded` status and migration markers stay unchanged because
the registry guard requires activation evidence. Built like
`platform-communication` (main `b78809e`) on `platform-backend-core` at
`d00e0119c2e47da3942d9c7b4b88828953f8bc5a`.

The three migration gates in the registry map to code and tests:

| Gate | Where | Tests |
|---|---|---|
| carrier sandbox | `carriers.SandboxCarrier`, the only adapter | `test_rates_and_transitions.py`, `test_labels.py` |
| label replay | `store.begin_label` / `finish_label` / `reconcile_label` | `test_labels.py`, `test_http.py` |
| tracking deduplication | `store.record_tracking_event` | `test_tracking.py`, `test_http.py` |

## Installation and lifecycle

Python >=3.12, PostgreSQL >=16. Build and review wheels for this repository and
`platform-backend-core==0.1.0`; a release bundles all transitive wheels in
`wheelhouse/` for the `Dockerfile`. Nothing is published to PyPI. `Dockerfile`
and `railway.toml` are reference release assets; no image was built and nothing
was deployed.

- `python -m platform_logistics migrate`: explicit migration with the DDL-role
  `DATABASE_URL` (namespace `logistics`). The API never migrates at startup.
- `python -m platform_logistics serve`: a separate NOSUPERUSER NOBYPASSRLS runtime
  `DATABASE_URL` (the shared core refuses anything else); `PORT` defaults to 8080,
  at most 100 concurrent requests, pool 8, SQL timeout 10 s, HTTP boundary 30 s,
  request bodies up to 16 KiB.
- `JWT_ISSUER`, `JWT_AUDIENCE` and exactly one of `JWT_PUBLIC_KEY` (PEM) or
  `JWT_JWKS_URL` (HTTPS). Workspace authority comes only from the JWT `ws` claim;
  nothing in a body or header selects a tenant.
- `LOGISTICS_CARRIERS=sandbox` enables the sandbox carrier. Unknown names are
  ignored; with no carrier `serve` refuses to start and `/readyz` is 503.
- Startup failures print one generic line, never configuration values.
- `GET /healthz`, `/readyz`, `/metrics` come from the shared core. Readiness checks
  the exact applied migration versions and checksums, forced RLS on all three
  tables, schema USAGE, history SELECT and each required table privilege
  separately (a comma privilege list in PostgreSQL means "any").

Runtime grants: SELECT, INSERT, UPDATE on `shipment` and `shipment_label`;
SELECT, INSERT on `tracking_event`; SELECT on `platform_schema_migrations`;
USAGE on the schema. No DELETE, TRUNCATE, ownership or CREATE. Use a dedicated
database; no other service's tables are read.

Rollback stops this optional runtime; no consumer uses it yet. Never downgrade
applied SQL. Restore only into a new isolated database.

## Data held

A shipment stores an opaque `reference` (the caller's order id), an opaque
`recipient_ref` (a pointer to a contact another platform owns, `[A-Za-z0-9_.:-]`,
so a name or street address is refused), origin and destination country and
postal code, parcel weight and dimensions, the quoted rate, and status. No names,
street addresses, phone numbers or emails. The service logs only the route
template, status and correlation id (shared core); a test asserts that postal
codes, references and tracking numbers do not reach the log.

## API

All errors are generic: `Invalid request` 422, `Idempotency conflict` 409,
`Invalid status transition` 409, `Label purchase in progress` 409,
`Label outcome uncertain` 409, `Label rejected` 422, `Carrier unavailable` 503,
`Not found` 404, plus the shared core's 401/403. No request value or carrier
response is echoed.

| Method and path | Scope | Result |
|---|---|---|
| `POST /v1/rates` | `rates:read` | 200 `{rates: [...]}` from every configured carrier |
| `POST /v1/shipments` + `Idempotency-Key` | `shipments:write` | 201 new, 200 replay, 409 changed payload |
| `GET /v1/shipments?limit=&after=` | `shipments:read` | page by id, 1..100 |
| `GET /v1/shipments/{id}` | `shipments:read` | shipment with its label, or 404 |
| `POST /v1/shipments/{id}/cancel` | `shipments:write` | only from `created`, else 409 |
| `POST /v1/shipments/{id}/label` | `labels:write` | 201 bought, 200 replay of the bought label |
| `POST /v1/shipments/{id}/label/reconcile` | `labels:write` | settles an `uncertain` label by asking the carrier |
| `POST /v1/tracking-events` | `tracking:write` | 201 `applied` or `recorded`, 200 `duplicate`, 404 unknown tracking number |
| `GET /v1/shipments/{id}/events` | `shipments:read` | events by `occurred_at`, at most 500 |

### Rates

Rate and shipment bodies use `origin` and `destination` (`country` ISO 3166 alpha-2
upper case, `postal_code` 2..12 of `A-Z a-z 0-9 space -`) and `parcel`
(`weight_grams` 1..30000, `length_mm`, `width_mm`, `height_mm` 1..1500, integers).
Billable weight is the larger of actual and volumetric weight (L x W x H / 5000,
in mm and g), rounded up to 500 g. Sandbox tariff in THB minor units:

| Service | Domestic first 500 g / each further 500 g | International | Days |
|---|---|---|---|
| standard | 4000 / 1500 | 45000 / 12000 | 3 / 10 |
| express | 8000 / 2500 | 90000 / 20000 | 1 / 4 |

The same input always gives the same rates. A shipment stores the rate for its
`carrier` and `service` at creation and never re-prices.

### Shipments and idempotency

`POST /v1/shipments` takes `reference`, `recipient_ref`, `carrier`, `service`,
`origin`, `destination`, `parcel` (no other fields) and a required
`Idempotency-Key` (`[A-Za-z0-9_.:/-]{1,128}`). The fingerprint is SHA-256 of the
canonical JSON of the body (sorted keys), so key order does not matter. Creation
is `INSERT ... ON CONFLICT DO NOTHING` on `(workspace_id, idempotency_key)`;
concurrent requests with one key make one shipment. A replay returns the first
shipment with 200; a different body under the same key is 409. Keys are per
workspace.

### Fulfillment status

```
created -> label_pending -> labelled -> in_transit -> out_for_delivery -> delivered
   |            |    \                     |  ^             |   \
cancelled  label_uncertain -> labelled     v  |             v    -> exception
                 \-> created          exception -> returned
```

The exact graph is `rules.TRANSITIONS`: `labelled` may also go straight to
`out_for_delivery`, `delivered` or `exception`; `out_for_delivery` may fall back to
`in_transit`; `exception` may go to `in_transit`, `out_for_delivery`, `delivered`
or `returned`; `label_pending` and `label_uncertain` return to `created` when no
label was bought. `delivered`, `returned` and `cancelled` are terminal. Every
status change in the store goes through this graph.

### Label purchase (label replay gate)

One `shipment_label` row per shipment (primary key `(workspace_id, shipment_id)`),
so a shipment can never hold two labels.

1. `begin_label` locks the shipment row, refuses unless the shipment is `created`
   (or was rejected before), and commits a `purchasing` row with a 60-second lease
   token before any carrier call. A second request meanwhile gets 409
   `Label purchase in progress`.
2. The carrier is called outside the transaction with the stable key
   `label/<shipment id>`. The sandbox honours it like an idempotent carrier API.
3. `finish_label` records the outcome only under the same unexpired lease token:
   - bought: `purchased` with tracking number and label reference; shipment `labelled`.
   - `rejected` or `unavailable` (the carrier definitely bought nothing): `failed`,
     shipment back to `created`; a later purchase is a new attempt (`attempts` + 1).
   - `uncertain` (timeout after sending, malformed answer, any unexpected adapter
     error, a lease that expired while the call ran): `uncertain`, shipment
     `label_uncertain`.
4. A `purchasing` lease that expires (the process died mid-call) becomes
   `uncertain` the next time anyone touches the label. That change is committed
   on its own so the refusal that follows does not roll it back.

An `uncertain` label is quarantined: purchase returns 409 and never calls the
carrier again. `POST .../label/reconcile` asks the carrier `find_label(key)`: a
label found becomes `purchased` (shipment `labelled`); a definite "none" becomes
`failed` (shipment `created`, so a retry is safe and still carries the same
carrier key); a failed lookup leaves it `uncertain`. A purchased label replays
with 200 and the same tracking number for ever.

### Tracking events (tracking deduplication gate)

`POST /v1/tracking-events` takes `carrier`, `carrier_event_id`
(`[A-Za-z0-9_.:/-]{1,128}`), `tracking_number`, `status` (`in_transit`,
`out_for_delivery`, `delivered`, `exception`, `returned`) and `occurred_at`
(ISO 8601 with a time zone, at most one day in the future). The caller is a
trusted carrier-ingest bridge holding `tracking:write`; this pilot has no carrier
webhook signature check.

- The tracking number must belong to a purchased label in the caller's workspace,
  else 404.
- Events are keyed by `(workspace_id, carrier, carrier_event_id)`. A repeat with
  the same content is `duplicate` (200) and changes nothing; the same id with other
  content is 409. Concurrent deliveries of one event store one row.
- Events for a shipment are decided one at a time (row lock). An event older than
  the last applied one, or one the status graph does not allow (for example
  `returned` after `delivered`), is stored as `recorded` and leaves the status as
  it is. Otherwise it is `applied` and moves the shipment.

## Local verification record — 2026-10-29

Environment: Python 3.12, PostgreSQL 16.2 via `pgserver` (no Docker),
`platform-backend-core` at the CI pin. Every PostgreSQL case runs in a throwaway
database with a NOSUPERUSER NOBYPASSRLS role, real RS256 tokens and real HTTP
requests through the app. No network carrier was called.

RED before GREEN, in order:

1. `test_rates_and_transitions.py` (12 cases): collection error, module
   `platform_logistics.carriers` missing; then 12 passed.
2. `test_shipments.py`: collection error, `platform_logistics.migrations` missing;
   then 27 passed in total.
3. `test_labels.py`: collection error, `LabelInProgress` missing. After the first
   implementation one assertion failed:
   `test_crash_after_persisting_purchasing_becomes_uncertain` saw `label_pending`
   instead of `label_uncertain`, because the refusal rolled back the lease-expiry
   update in the same transaction. Fixed by committing the expiry separately; 40 passed.
4. `test_tracking.py`: 17 failed (no `record_tracking_event`). After implementing,
   one assertion failed: a 129-character tracking number was accepted; fixed with a
   dedicated tracking-number pattern; 57 passed.
5. `test_http.py`: collection error, `platform_logistics.app` missing. After
   implementing, the readiness test failed (503 instead of 200) because the
   shared app closes its database at shutdown and the test reused it; the test
   now opens one pool per client. 74 passed.
6. `test_lifecycle.py`: 3 failed (no `__main__`); then passed.

Final: `78 passed` in `runtime_tests` (one upstream Starlette/httpx deprecation
warning), 36 legacy unit tests in `tests/`, `bash scripts/check.sh` OK, Ruff 0.17
clean, wheel builds with `pip wheel --no-deps`. Without `PLATFORM_TEST_DSN` the
PostgreSQL cases skip visibly (15 pass, 63 skip) and prove nothing about
durability. CI runs the same suite on PostgreSQL 16 in `shared-runtime.yml`.

## Deliberate omissions

- No real carrier adapter, credential, HTTP client or carrier webhook. The sandbox
  ledger lives in process memory, so in a multi-process deployment reconcile only
  sees labels its own process bought; it is a fixture, not a carrier.
- No label void or refund: a labelled shipment cannot be cancelled.
- No background worker: label purchase is synchronous and expired leases are
  settled lazily. No automatic reconcile; an operator or caller triggers it.
- No full address, recipient name or contact data; resolving `recipient_ref`
  belongs to a later adapter phase with its own privacy review.
- No per-workspace shipment cap, no retention or purge job, no rate cards per
  workspace, no multi-parcel shipments, no currency other than THB.
- `interfaces` in `contract.json` stays empty and the contract stays `scaffolded`.

## Staging plan (not executed)

Blocked until at least 2026-11-01 (Neon `empire-platform` compute quota) and on
the Railway deployment approval step. Each step needs the owner's go.

1. Dedicated database, a DDL role for `migrate` and a NOSUPERUSER NOBYPASSRLS
   runtime role with the grants above. Connection strings to Infisical
   `/platform-logistics`.
2. Build the `wheelhouse/` (this wheel plus `platform-backend-core` at the CI pin
   and dependencies) and deploy one Railway service for `serve` with
   `LOGISTICS_CARRIERS=sandbox` and JWT issuer `empire-platform`. Run `migrate`
   once with the DDL role.
3. Smoke test: `/readyz` 200; a quote; a shipment created twice with one key
   returns one id; a label bought twice returns one tracking number; a tracking
   event posted twice is `applied` then `duplicate`.
4. Only then: Sentry, the uptime monitor, a real carrier sandbox adapter with its
   own review, and a first consumer with a rollback path.

## Review fixes — 2026-10-29

Independent review (changes requested) found one medium and three low issues; the
tests in `runtime_tests/test_review_fixes.py` failed first (5 of 5) and pass now.

- An unconfigured carrier is resolved before the label lease, so `POST /label` is a
  plain 422 and the shipment stays `created`; it is never quarantined as `uncertain`
  for a carrier that was not called. `reconcile` with an unconfigured carrier is 422
  and leaves the label `uncertain`.
- A reconcile whose carrier reports a tracking number another shipment already holds
  stays `uncertain` (409) instead of failing with a 500.
- Event times that overflow on UTC conversion or predate 2000-01-01 are invalid (422).

Requirements on any real carrier adapter before it replaces the sandbox: purchases
must be idempotent on the `label/<shipment id>` key, every call must time out well
inside the 60-second lease, and `find_label` must be authoritative (a `None` answer
allows a new purchase). The sandbox keeps its ledger in memory, so run it as a single
process.

Full suite: 119 passed against PostgreSQL 16.2, Python 3.12.
