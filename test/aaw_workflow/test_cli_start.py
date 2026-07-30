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

        result = self.run_cli("start", "--entry", "sr", "--sr", "SR-DUP", expect=1)

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
        payload = json.loads(
            self.run_cli("start", "--var", "SR=SR-A", "--sr", "SR-B", "--json").stdout
        )

        self.assertEqual("SR-B", payload["sr"])
        self.assertTrue((self.cwd / ".sdd" / "SR-B" / "workflow.yaml").exists())

    def test_human_output_mentions_sr_and_next_hint(self) -> None:
        result = self.run_cli("start", "--sr", "SR-HUMAN")

        self.assertIn("SR SR-HUMAN 已启动", result.stdout)
        self.assertIn("aaw next", result.stdout)

    def test_start_does_not_write_session_marker(self) -> None:
        """question-tracker 2.0 起不再消费 .current_session；start 不得生成该文件。"""
        self.run_cli("start", "--sr", "SR-NOMARK")

        self.assertFalse((self.cwd / ".sdd" / ".current_session").exists())


if __name__ == "__main__":
    unittest.main()
