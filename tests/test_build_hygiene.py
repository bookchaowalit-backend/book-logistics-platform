"""Offline supply-chain gates: SHA-pinned workflow actions and a clean-checkout Dockerfile."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CORE_PIN = re.compile(r"platform-backend-core\.git@([0-9a-f]{40})")
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def unpinned_uses(text: str) -> list[str]:
    """Every remote `uses:` reference that is not a full 40-hex commit SHA."""
    return [ref for ref in USES.findall(text) if not ref.startswith("./") and not PINNED.match(ref)]


def workflow_core_commit() -> str:
    commits = set(CORE_PIN.findall((WORKFLOWS / "shared-runtime.yml").read_text(encoding="utf-8")))
    assert len(commits) == 1, commits
    return commits.pop()


def dockerfile_instructions() -> list[tuple[str, str]]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            keyword, _, rest = line.partition(" ")
            rows.append((keyword.upper(), rest.strip()))
    return rows


class WorkflowPinTests(unittest.TestCase):
    def test_checker_flags_tags_and_short_shas(self) -> None:
        sample = (
            "steps:\n"
            "  - uses: actions/checkout@v4\n"
            "  - uses: actions/setup-python@a26af69\n"
            "  - uses: './local-action'\n"
            "  - uses: actions/cache@0123456789abcdef0123456789abcdef01234567 # v4\n"
            "    uses: org/repo/.github/workflows/x.yml@main\n"
        )
        self.assertEqual(
            unpinned_uses(sample),
            ["actions/checkout@v4", "actions/setup-python@a26af69", "org/repo/.github/workflows/x.yml@main"],
        )

    def test_every_workflow_action_is_sha_pinned(self) -> None:
        files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(files)
        found = {path.name: unpinned_uses(path.read_text(encoding="utf-8")) for path in files}
        self.assertEqual({name: refs for name, refs in found.items() if refs}, {})


class DockerfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = dockerfile_instructions()
        self.text = " ".join(rest for _, rest in self.rows)

    def test_builds_from_a_clean_checkout(self) -> None:
        # wheelhouse/ is git-ignored: COPYing it makes `docker build .` fail on a fresh clone.
        self.assertNotIn("wheelhouse", self.text)
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        for keyword, rest in self.rows:
            if keyword == "COPY" and "--from=" not in rest:
                for source in rest.split()[:-1]:
                    self.assertNotIn(source.rstrip("/") + "/", ignored, source)

    def test_is_multi_stage(self) -> None:
        self.assertGreaterEqual(sum(1 for keyword, _ in self.rows if keyword == "FROM"), 2)

    def test_core_comes_from_the_ci_pinned_commit(self) -> None:
        self.assertEqual(set(CORE_PIN.findall(self.text)), {workflow_core_commit()})

    def test_runtime_stage_is_non_root_and_serves(self) -> None:
        last_from = max(i for i, (keyword, _) in enumerate(self.rows) if keyword == "FROM")
        final = self.rows[last_from:]
        users = [rest for keyword, rest in final if keyword == "USER"]
        self.assertTrue(users, "final stage must set USER")
        self.assertNotIn(users[-1].split(":")[0], {"0", "root"})
        self.assertEqual([rest for keyword, rest in final if keyword == "CMD"][-1], '["python", "-m", "platform_logistics", "serve"]')

    def test_ci_builds_the_image_without_pushing(self) -> None:
        workflow = (WORKFLOWS / "shared-runtime.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, re.compile(r"^\s*run:\s*docker build\b.*\s\.\s*$", re.MULTILINE))
        self.assertNotIn("docker push", workflow)
        self.assertNotRegex(workflow, r"--push\b")


if __name__ == "__main__":
    unittest.main()
