"""Tests for the `aaw start` command."""

from __future__ import annotations

import json
import unittest

from _cli_base import CliTestBase


class StartCliTests(CliTestBase):
    def test_malformed_var_exits_with_error(self) -> None:
        result = self.run_cli("start", "--var", "SR-BAD", expect=1)

        self.assertIn("--var 格式错误", result.stderr)

    def test_empty_var_key_exits_with_error(self) -> None:
        result = self.run_cli("start", "--var", "=SR-BAD", expect=1)

        self.assertIn("--var 缺少 key", result.stderr)

    def test_unknown_entry_exits_with_error(self) -> None:
        result = self.run_cli("start", "--entry", "nope", "--sr", "SR-001", expect=1)

        self.assertIn("入口不存在", result.stderr)

    def test_missing_required_vars_exits_with_error(self) -> None:
        result = self.run_cli("start", "--entry", "ar", "--sr", "SR-001", expect=1)

        self.assertIn("缺少变量", result.stderr)

    def test_duplicate_sr_exits_with_error(self) -> None:
        self.start_sr("SR-DUP")

        req_file = self.cwd / ".aaw-test-requirement-SR-DUP.md"
        result = self.run_cli(
            "start", "--entry", "sr", "--sr", "SR-DUP",
            "--requirement-file", str(req_file), expect=1,
        )

        self.assertIn("已存在", result.stderr)

    def test_desc_alias_satisfies_ar_entry(self) -> None:
        payload = json.loads(
            self.run_cli(
                "start", "--entry", "ar",
                "--var", "SR=SR-DESC", "--var", "AR=AR-001", "--var", "DESC=user-mgmt",
                "--json",
            ).stdout
        )

        self.assertTrue(payload["ok"])
        self.assertEqual("SR-DESC", payload["sr"])

    def test_sr_option_overrides_var(self) -> None:
        req_file = self.cwd / ".req.md"
        req_file.write_text(self.DEFAULT_REQUIREMENT, "utf-8")
        payload = json.loads(
            self.run_cli(
                "start", "--var", "SR=SR-A", "--sr", "SR-B",
                "--requirement-file", str(req_file), "--json",
            ).stdout
        )

        self.assertEqual("SR-B", payload["sr"])
        self.assertTrue((self.cwd / ".sdd" / "SR-B" / "workflow.yaml").exists())

    def test_human_output_mentions_sr_and_next_hint(self) -> None:
        req_file = self.cwd / ".req.md"
        req_file.write_text(self.DEFAULT_REQUIREMENT, "utf-8")
        result = self.run_cli(
            "start", "--sr", "SR-HUMAN", "--requirement-file", str(req_file),
        )

        self.assertIn("SR SR-HUMAN 已启动", result.stdout)
        self.assertIn("aaw next", result.stdout)
        self.assertIn("原始需求已保存", result.stdout)

    # -- original requirement handling ------------------------------------

    def test_sr_entry_without_requirement_file_fails(self) -> None:
        result = self.run_cli("start", "--entry", "sr", "--sr", "SR-NOREQ", expect=1)

        self.assertIn("--requirement-file", result.stderr)

    def test_requirement_file_not_found_fails(self) -> None:
        missing = self.cwd / "does-not-exist.md"
        result = self.run_cli(
            "start", "--entry", "sr", "--sr", "SR-MISS",
            "--requirement-file", str(missing), expect=1,
        )

        self.assertEqual(1, result.returncode)

    def test_empty_requirement_file_fails(self) -> None:
        empty = self.cwd / "empty.md"
        empty.write_text("   \n\t\n", "utf-8")
        result = self.run_cli(
            "start", "--entry", "sr", "--sr", "SR-EMPTY",
            "--requirement-file", str(empty), expect=1,
        )

        self.assertIn("为空", result.stderr)

    def test_requirement_saved_verbatim(self) -> None:
        self.start_sr("SR-SAVE")

        saved = self.cwd / ".sdd" / "SR-SAVE" / "original-requirement.md"
        self.assertEqual(self.DEFAULT_REQUIREMENT, saved.read_text("utf-8"))

    def test_start_payload_reports_original_requirement(self) -> None:
        payload = self.start_sr("SR-PAYLOAD")

        original = payload["original_requirement"]
        self.assertIsNotNone(original)
        self.assertEqual(len(self.DEFAULT_REQUIREMENT), original["char_count"])

    def test_rerun_with_identical_content_is_idempotent(self) -> None:
        self.start_sr("SR-IDEM")
        (self.cwd / ".sdd" / "SR-IDEM" / "workflow.yaml").unlink()

        req_file = self.cwd / ".aaw-test-requirement-SR-IDEM.md"
        self.run_cli(
            "start", "--entry", "sr", "--sr", "SR-IDEM",
            "--requirement-file", str(req_file), "--json",
        )

    def test_existing_requirement_with_different_content_fails(self) -> None:
        self.start_sr("SR-DIFF")
        saved = self.cwd / ".sdd" / "SR-DIFF" / "original-requirement.md"
        original_content = saved.read_text("utf-8")
        (self.cwd / ".sdd" / "SR-DIFF" / "workflow.yaml").unlink()

        req_file = self.cwd / ".aaw-test-requirement-SR-DIFF.md"
        req_file.write_text("完全不同的需求内容\n", "utf-8")
        result = self.run_cli(
            "start", "--entry", "sr", "--sr", "SR-DIFF",
            "--requirement-file", str(req_file), expect=1,
        )

        self.assertIn("不一致", result.stderr)
        self.assertEqual(original_content, saved.read_text("utf-8"))

    def test_ar_entry_does_not_require_requirement_file(self) -> None:
        payload = json.loads(
            self.run_cli(
                "start", "--entry", "ar",
                "--sr", "SR-AR", "--ar", "AR-001", "--title", "用户管理",
                "--json",
            ).stdout
        )

        self.assertTrue(payload["ok"])
        self.assertIsNone(payload.get("original_requirement"))
        self.assertFalse(
            (self.cwd / ".sdd" / "SR-AR" / "original-requirement.md").exists()
        )

    def test_start_does_not_write_session_marker(self) -> None:
        """question-tracker 2.0 起不再消费 .current_session；start 不得生成该文件。"""
        self.start_sr("SR-NOMARK")

        self.assertFalse((self.cwd / ".sdd" / ".current_session").exists())


if __name__ == "__main__":
    unittest.main()
