"""Tests for scripts/check.py: the offline Book Platform contract gate."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("platform_contract_check", ROOT / "scripts" / "check.py")
assert _SPEC is not None and _SPEC.loader is not None
check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check)


class ContractCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / ROOT.name
        shutil.copytree(
            ROOT,
            self.root,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".runtime", ".pytest_cache"),
        )
        self.contract = json.loads((self.root / "contract.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def write_contract(self) -> None:
        (self.root / "contract.json").write_text(json.dumps(self.contract, indent=2), encoding="utf-8")

    def assert_error(self, fragment: str) -> None:
        errors = check.validate(self.root)
        self.assertTrue(any(fragment in error for error in errors), f"{fragment!r} not in {errors}")

    def test_repository_contract_is_valid(self) -> None:
        self.assertEqual(check.validate(ROOT), [])

    def test_contract_declares_precise_boundary_metadata(self) -> None:
        implementation = self.contract["current_implementation"]
        self.assertIn("source_paths", implementation)
        self.assertIn("source_status", implementation)
        self.assertTrue(self.contract.get("migration_gates"))
        self.assertIn("surface_doc", self.contract)

    def test_missing_required_key_fails(self) -> None:
        del self.contract["data_owner"]
        self.write_contract()
        self.assert_error("$.data_owner is required")

    def test_unknown_top_level_key_fails(self) -> None:
        self.contract["database_url"] = "not-allowed"
        self.write_contract()
        self.assert_error("$.database_url is not allowed")

    def test_unknown_dependency_fails(self) -> None:
        self.contract["depends_on"] = ["not-a-platform"]
        self.write_contract()
        self.assert_error("$.depends_on[0] must be one of")

    def test_self_dependency_fails(self) -> None:
        self.contract["depends_on"] = [self.contract["platform_id"]]
        self.write_contract()
        self.assert_error("must not depend on itself")

    def test_duplicate_capability_fails(self) -> None:
        self.contract["capabilities"] = self.contract["capabilities"] + self.contract["capabilities"][:1]
        self.write_contract()
        self.assert_error("contains duplicate item")

    def test_readme_status_must_match_contract(self) -> None:
        readme = self.root / "README.md"
        status = self.contract["status"]
        readme.write_text(
            readme.read_text(encoding="utf-8").replace(f"Status: `{status}`", "Status: `unknown`"),
            encoding="utf-8",
        )
        self.assert_error("README.md status must match")

    def test_scaffold_placeholder_fails(self) -> None:
        readme = self.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\n__PLATFORM_ID__\n", encoding="utf-8")
        self.assert_error("scaffold placeholder __PLATFORM_ID__")

    def test_dunder_identifiers_are_not_placeholders(self) -> None:
        readme = self.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nSee `__init__.py`.\n", encoding="utf-8")
        self.assertEqual(check.validate(self.root), [])

    def test_source_path_must_match_first_source_path(self) -> None:
        implementation = self.contract["current_implementation"]
        implementation["source_paths"] = ["docs", *implementation["source_paths"]]
        self.write_contract()
        self.assert_error("must equal the first source_paths entry")

    def test_parent_traversal_in_source_paths_fails(self) -> None:
        self.contract["current_implementation"]["source_paths"] = ["../secrets"]
        self.contract["current_implementation"]["source_path"] = "../secrets"
        self.write_contract()
        self.assert_error("does not match pattern")

    def test_undocumented_interface_fails(self) -> None:
        self.contract.setdefault("interfaces", []).append(
            {
                "id": "undocumented.probe",
                "kind": "http",
                "direction": "provides",
                "name": "GET /probe",
                "status": "planned",
                "source": "none",
            }
        )
        self.write_contract()
        self.assert_error("does not document interface `undocumented.probe`")

    def test_duplicate_interface_id_fails(self) -> None:
        interface = {
            "id": "duplicate.probe",
            "kind": "event",
            "direction": "provides",
            "name": "probe",
            "status": "planned",
            "source": "none",
        }
        self.contract["interfaces"] = [*self.contract.get("interfaces", []), interface, dict(interface)]
        self.write_contract()
        self.assert_error("declared more than once")

    def test_invalid_json_is_reported(self) -> None:
        (self.root / "contract.json").write_text("{", encoding="utf-8")
        self.assert_error("contract.json is not valid JSON")

    def test_missing_schema_is_reported(self) -> None:
        (self.root / check.SCHEMA_RELATIVE).unlink()
        self.assert_error(f"{check.SCHEMA_RELATIVE} is missing")

    def test_boolean_is_not_an_integer(self) -> None:
        schema = {"type": "integer"}
        self.assertEqual(check.validate_schema(True, schema, schema), ["$ must be of type integer"])
        self.assertEqual(check.validate_schema(3, schema, schema), [])

    def test_main_reports_failures_with_exit_code(self) -> None:
        self.contract["status"] = "production"
        self.write_contract()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = check.main([str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL: contract.json: $.status must be one of", output.getvalue())

    def test_main_succeeds_for_repository(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = check.main([str(self.root)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn(f"OK: {self.contract['repository']}", output.getvalue())


if __name__ == "__main__":
    unittest.main()
