"""Tests for the `aaw rollback` command."""

from __future__ import annotations

import json
import unittest

from _cli_base import CliTestBase


class RollbackCliTests(CliTestBase):
    def test_missing_sr_exits_with_error(self) -> None:
        result = self.run_cli("rollback", "--sr", "SR-NOPE", "1", expect=1)

        self.assertIn("SR SR-NOPE 不存在", result.stderr)

    def test_nonexistent_step_exits_with_error(self) -> None:
        self.start_sr("SR-RBERR")

        result = self.run_cli("rollback", "--sr", "SR-RBERR", "99", expect=1)

        self.assertIn("step 99 不存在", result.stderr)

    def test_rollback_without_artifact_policy_returns_preview_without_mutation(self) -> None:
        self.start_sr("SR-RB")
        self.complete_step_1("SR-RB")

        payload = json.loads(self.run_cli("rollback", "--sr", "SR-RB", "1", "--json").stdout)

        self.assertTrue(payload["ok"])
        self.assertEqual("confirmation_required", payload["status"])
        self.assertEqual(["preserve", "discard"], [item["id"] for item in payload["choices"]])
        self.assertEqual(
            ["preserve", "--json"],
            payload["choices"][0]["command_argv"][-2:],
        )
        self.assertTrue(payload["managed_artifacts"][0]["exists"])
        data = self.status_json("SR-RB")
        self.assertEqual([1, 2], [s["id"] for s in data["steps"]])
        self.assertTrue(data["steps"][0]["finished"])

    def test_preserve_reopens_step_and_keeps_managed_artifacts(self) -> None:
        self.start_sr("SR-RBPRESERVE")
        self.complete_step_1("SR-RBPRESERVE")
        architecture = self.cwd / ".sdd" / "software_architecture.md"

        payload = json.loads(
            self.run_cli(
                "rollback",
                "--sr",
                "SR-RBPRESERVE",
                "1",
                "--artifacts",
                "preserve",
                "--json",
            ).stdout
        )

        self.assertEqual("rolled_back", payload["status"])
        self.assertEqual("preserve", payload["artifact_policy"])
        self.assertTrue(architecture.exists())
        data = self.status_json("SR-RBPRESERVE")
        self.assertEqual([1], [s["id"] for s in data["steps"]])
        self.assertFalse(data["steps"][0]["finished"])

    def test_discard_deletes_target_managed_artifact(self) -> None:
        self.start_sr("SR-RBDISCARD")
        self.complete_step_1("SR-RBDISCARD")
        architecture = self.cwd / ".sdd" / "software_architecture.md"

        payload = json.loads(
            self.run_cli(
                "rollback",
                "--sr",
                "SR-RBDISCARD",
                "1",
                "--artifacts",
                "discard",
                "--json",
            ).stdout
        )

        self.assertEqual("discard", payload["artifact_policy"])
        self.assertFalse(architecture.exists())
        self.assertTrue(payload["deleted_files"])
        self.assertFalse(payload["managed_artifacts"][0]["exists_after"])

    def test_human_output_requests_artifact_policy(self) -> None:
        self.start_sr("SR-RBOUT")
        self.complete_step_1("SR-RBOUT")

        result = self.run_cli("rollback", "--sr", "SR-RBOUT", "1")

        self.assertIn("必须选择成果物处理方式", result.stdout)
        self.assertIn("preserve", result.stdout)
        self.assertIn("discard", result.stdout)

    def test_invalid_artifact_policy_exits_with_error(self) -> None:
        self.start_sr("SR-RBBAD")

        result = self.run_cli(
            "rollback",
            "--sr",
            "SR-RBBAD",
            "1",
            "--artifacts",
            "unknown",
            expect=1,
        )

        self.assertIn("--artifacts 必须是 preserve 或 discard", result.stderr)


if __name__ == "__main__":
    unittest.main()
