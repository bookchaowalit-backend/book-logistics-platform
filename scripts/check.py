#!/usr/bin/env python3
"""Offline contract and repository-shape check for a Book Platform scaffold."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "schema_version",
    "platform_id",
    "repository",
    "status",
    "api_version",
    "data_owner",
    "capabilities",
    "depends_on",
    "current_implementation",
    "migration",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> int:
    contract_path = ROOT / "contract.json"
    readme_path = ROOT / "README.md"
    if not contract_path.is_file():
        fail("contract.json is missing")
    if not readme_path.is_file():
        fail("README.md is missing")
    if not (ROOT / "scripts" / "check.sh").is_file():
        fail("scripts/check.sh is missing")

    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"contract.json is invalid JSON: {exc}")
    if not isinstance(contract, dict):
        fail("contract.json root must be an object")
    missing = sorted(REQUIRED - set(contract))
    if missing:
        fail(f"contract.json missing keys: {missing}")
    if contract["schema_version"] != "book-platform.contract.v1":
        fail("unsupported schema_version")
    if contract["status"] != "scaffolded":
        fail("scaffold status must remain scaffolded until activation evidence exists")
    for key in ("platform_id", "repository", "api_version", "data_owner"):
        if not isinstance(contract[key], str) or not contract[key].strip():
            fail(f"{key} must be a non-empty string")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", contract["platform_id"]):
        fail("platform_id has an invalid slug")
    if not re.fullmatch(r"book-[a-z0-9][a-z0-9-]{1,62}", contract["repository"]):
        fail("repository has an invalid slug")
    for key in ("capabilities", "depends_on"):
        if not isinstance(contract[key], list) or not all(
            isinstance(item, str) and item.strip() for item in contract[key]
        ):
            fail(f"{key} must be a list of non-empty strings")
    implementation = contract["current_implementation"]
    if not isinstance(implementation, dict) or implementation.get("repository") != "solo-empire":
        fail("current_implementation.repository must be solo-empire")
    migration = contract["migration"]
    if not isinstance(migration, dict):
        fail("migration must be an object")
    for key in ("compatibility_adapter", "shadow_parity", "cutover", "rollback"):
        if not isinstance(migration.get(key), str) or not migration[key].strip():
            fail(f"migration.{key} must be a non-empty string")

    readme = readme_path.read_text(encoding="utf-8")
    for heading in ("## Scope", "## Boundary", "## Local verification", "## Migration gate"):
        if heading not in readme:
            fail(f"README.md is missing {heading}")
    if "__" in readme or "__" in contract_path.read_text(encoding="utf-8"):
        fail("scaffold placeholder remains")
    print(f"OK: {contract['repository']} ({contract['platform_id']}) scaffold contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
