"""Integration tests for update.py _sanity_check with auxiliary directories.

Covers the bug where a zip containing an auxiliary dir (question-tracker-mcp)
was rejected as "顶层目录与清单不一致" because top_dirs was compared against
skills only, not skills + auxiliary.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "skills" / "aaw-workflow" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.update import UpdateError, _load_release_manifest, _sanity_check  # noqa: E402


class SanityCheckAuxiliaryTests(unittest.TestCase):
    """IT-M12-03 / IT-M12-04: auxiliary dir acceptance and rejection."""

    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.stage = self.tmp_path / "stage"
        self.payload = self.stage / "payload"
        self.payload.mkdir(parents=True)
        self.skills_root = self.tmp_path / "skills_root"
        self.skills_root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _mk_skill(self, name: str) -> None:
        d = self.payload / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n", "utf-8")

    def _mk_workflow_skill(self, version: str = "2.3.2") -> None:
        d = self.payload / "aaw-workflow"
        (d / "scripts" / "cli" / "definitions").mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: aaw-workflow\n---\n", "utf-8")
        (d / "scripts" / "aaw.py").write_text("# entry\n", "utf-8")
        (d / "scripts" / "cli" / "VERSION").write_text(version + "\n", "utf-8")

    def _mk_auxiliary(self, name: str = "question-tracker-mcp") -> None:
        bin_dir = self.payload / name / "bin" / "linux"
        bin_dir.mkdir(parents=True)
        (bin_dir / "mcp_server").write_bytes(b"\x7fELF fake")

    def _manifest(
        self,
        skills: list[str],
        auxiliary: list[str] | None = None,
        version: str = "2.3.2",
    ) -> dict:
        m: dict = {
            "version": version,
            "skills": skills,
            "external_skills": [],
            "removed_skills": [],
        }
        if auxiliary is not None:
            m["auxiliary"] = auxiliary
        return m

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------

    def test_it_m12_03_zip_with_auxiliary_passes(self) -> None:
        """Zip containing skills + auxiliary with manifest declaring both → pass."""
        self._mk_workflow_skill()
        self._mk_skill("sr-design")
        self._mk_auxiliary()
        manifest = self._manifest(
            skills=["aaw-workflow", "sr-design"],
            auxiliary=["question-tracker-mcp"],
        )
        # Must not raise — this was the production bug: top_dirs included the
        # auxiliary dir but was compared against skills only.
        _sanity_check(self.stage, manifest, "2.3.2", self.skills_root)

    def test_it_m12_04_missing_auxiliary_rejected(self) -> None:
        """Manifest declares auxiliary but zip lacks it → UpdateError."""
        self._mk_workflow_skill()
        self._mk_skill("sr-design")
        # No auxiliary directory on disk
        manifest = self._manifest(
            skills=["aaw-workflow", "sr-design"],
            auxiliary=["question-tracker-mcp"],
        )
        with self.assertRaises(UpdateError):
            _sanity_check(self.stage, manifest, "2.3.2", self.skills_root)

    def test_it_m12_06_auxiliary_without_bin_dir_rejected(self) -> None:
        """Auxiliary dir present but bin/ missing or empty → UpdateError."""
        self._mk_workflow_skill()
        self._mk_skill("sr-design")
        # auxiliary dir exists but has no bin/ at all
        (self.payload / "question-tracker-mcp").mkdir(parents=True)
        manifest = self._manifest(
            skills=["aaw-workflow", "sr-design"],
            auxiliary=["question-tracker-mcp"],
        )
        with self.assertRaises(UpdateError):
            _sanity_check(self.stage, manifest, "2.3.2", self.skills_root)

    def test_it_m12_07_extra_top_dir_rejected(self) -> None:
        """Zip has a top-level dir not declared in skills or auxiliary → reject."""
        self._mk_workflow_skill()
        self._mk_skill("sr-design")
        self._mk_auxiliary()
        # Undeclared extra directory
        (self.payload / "rogue-dir").mkdir(parents=True)
        manifest = self._manifest(
            skills=["aaw-workflow", "sr-design"],
            auxiliary=["question-tracker-mcp"],
        )
        with self.assertRaises(UpdateError):
            _sanity_check(self.stage, manifest, "2.3.2", self.skills_root)

    def test_it_m12_08_no_auxiliary_in_manifest_old_zip_still_passes(self) -> None:
        """Backward compat: manifest without auxiliary key, zip without aux dir."""
        self._mk_workflow_skill()
        self._mk_skill("sr-design")
        manifest = self._manifest(skills=["aaw-workflow", "sr-design"])
        _sanity_check(self.stage, manifest, "2.3.2", self.skills_root)


class LoadReleaseManifestTests(unittest.TestCase):
    """_load_release_manifest must preserve the auxiliary key.

    Root cause of the production bug: the loader only returned
    skills/external_skills/removed_skills, silently dropping auxiliary,
    which then made _sanity_check compare top_dirs against skills only.
    """

    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.stage = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_manifest(self, data: dict) -> None:
        import json

        (self.stage / "release-manifest.json").write_text(
            json.dumps(data), "utf-8"
        )

    def test_auxiliary_preserved(self) -> None:
        """Manifest with auxiliary key → returned dict includes it."""
        self._write_manifest(
            {
                "schema": 1,
                "version": "2.3.2",
                "skills": ["aaw-workflow"],
                "external_skills": [],
                "removed_skills": [],
                "auxiliary": ["question-tracker-mcp"],
            }
        )
        result = _load_release_manifest(self.stage)
        assert result["auxiliary"] == ["question-tracker-mcp"]
        assert result["skills"] == ["aaw-workflow"]

    def test_no_auxiliary_defaults_to_absent_or_empty(self) -> None:
        """Old manifest without auxiliary key → loader tolerates it."""
        self._write_manifest(
            {
                "schema": 1,
                "version": "2.3.2",
                "skills": ["aaw-workflow"],
                "external_skills": [],
                "removed_skills": [],
            }
        )
        result = _load_release_manifest(self.stage)
        assert result.get("auxiliary", []) == []

    def test_auxiliary_must_be_list_of_valid_names(self) -> None:
        """auxiliary containing invalid name → rejected."""
        self._write_manifest(
            {
                "schema": 1,
                "version": "2.3.2",
                "skills": ["aaw-workflow"],
                "external_skills": [],
                "removed_skills": [],
                "auxiliary": ["../escape"],
            }
        )
        with self.assertRaises(UpdateError):
            _load_release_manifest(self.stage)

    def test_auxiliary_skills_overlap_rejected(self) -> None:
        """auxiliary overlapping skills → rejected."""
        self._write_manifest(
            {
                "schema": 1,
                "version": "2.3.2",
                "skills": ["question-tracker-mcp"],
                "external_skills": [],
                "removed_skills": [],
                "auxiliary": ["question-tracker-mcp"],
            }
        )
        with self.assertRaises(UpdateError):
            _load_release_manifest(self.stage)


if __name__ == "__main__":
    unittest.main()
