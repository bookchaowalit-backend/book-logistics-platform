# Upgrade plan: book-logistics-platform

## Current state

Score: 5/10 (was 3/10). The boundary now has a precise, schema-validated
contract aligned with the parent registry, a documented contract surface, unit
tests for the check and CI; it still has no runtime implementation, fixtures or
parity evidence.

## Backlog

### P0

- Keep `interfaces` empty until a consumer needs shipping; then define one sandbox carrier-adapter contract with duplicate-label and tracking-dedup fixtures.

### P1

- Keep `schema/book-platform.contract.v1.schema.json` identical to
  `bookchaowalit-backend-core/contracts/`; change the canonical copy first.
- Ask the `solo-empire` owner to adopt the new schema and check in
  `infra/scaffolds/platform-repository` and to extend `platform_repository_guard.py`
  to compare `capabilities`, `depends_on` and `source_paths` with this contract.
- Add sanitized fixtures for success, duplicate delivery, timeout and provider
  failure (repository baseline in `systems/architecture/platform-repository-split.md`).

### P2

- Add a health endpoint and rollback instructions once a runtime exists.

## Done in this pass

- `contract.json`: registry-aligned `capabilities`, `depends_on`,
  `source_paths`, `source_status`, `migration_gates`, and an `interfaces` list
  derived from the current source.
- `schema/book-platform.contract.v1.schema.json` plus a dependency-free
  validator in `scripts/check.py` (schema, README/contract agreement,
  interface documentation, placeholder and path-traversal checks).
- `tests/test_contract_check.py` (19 tests) and `.github/workflows/check.yml`.
- `docs/CONTRACT-SURFACE.md` documenting the owned and consumed interfaces.
