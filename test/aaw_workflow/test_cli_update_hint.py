"""Tests for the update hint (stderr) and the throttled background check."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _cli_base import CliTestBase

from cli import update as cli_update


class UpdateHintCliTests(CliTestBase):
    """Hint behaviour through the real CLI subprocess."""

    def _hint_env(self, state: dict | str | None) -> dict[str, str]:
        state_path = self.cwd / "hint-state.json"
        if isinstance(state, dict):
            state_path.write_text(json.dumps(state), "utf-8")
        elif isinstance(state, str):
            state_path.write_text(state, "utf-8")
        return {"AAW_UPDATE_CHECK": "1", "AAW_UPDATE_STATE": str(state_path)}

    def test_hint_on_stderr_and_stdout_stays_pure_json(self) -> None:
        result = self.run_cli(
            "status", "--json",
            extra_env=self._hint_env({"latest_version": "99.0.0"}),
        )

        json.loads(result.stdout)  # stdout must remain parseable JSON
        self.assertIn("aaw update", result.stderr)
        self.assertIn("99.0.0", result.stderr)

    def test_no_hint_when_latest_equals_current(self) -> None:
        current = (
            Path(__file__).resolve().parents[2]
            / "skills" / "aaw-workflow" / "scripts" / "cli" / "VERSION"
        ).read_text("utf-8").strip()

        result = self.run_cli(
            "status", "--json",
            extra_env=self._hint_env({"latest_version": current}),
        )

        self.assertNotIn("aaw update", result.stderr)

    def test_no_hint_on_corrupted_state_file(self) -> None:
        result = self.run_cli("status", "--json", extra_env=self._hint_env("not json{"))

        self.assertNotIn("aaw update", result.stderr)

    def test_no_hint_when_state_file_missing(self) -> None:
        result = self.run_cli("status", "--json", extra_env=self._hint_env(None))

        self.assertNotIn("aaw update", result.stderr)

    def test_hint_disabled_by_default_test_env(self) -> None:
        # CliTestBase sets AAW_UPDATE_CHECK=0; even a newer cached version stays silent
        state_path = self.cwd / "hint-state.json"
        state_path.write_text('{"latest_version": "99.0.0"}', "utf-8")

        result = self.run_cli("status", "--json", extra_env={"AAW_UPDATE_STATE": str(state_path)})

        self.assertNotIn("aaw update", result.stderr)


class BackgroundCheckTests(unittest.TestCase):
    """check_for_update throttling, in-process."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "update-check.json"
        self.env = patch.dict(os.environ, {
            "AAW_UPDATE_STATE": str(self.state_path),
            "AAW_UPDATE_CHECK": "1",
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_failed_probe_still_refreshes_checked_at(self) -> None:
        cli_update.check_for_update(endpoint="http://127.0.0.1:1")

        state = json.loads(self.state_path.read_text("utf-8"))
        self.assertIsInstance(state.get("checked_at"), int)
        self.assertNotIn("latest_version", state)

    def test_recent_check_skips_network_entirely(self) -> None:
        cli_update.check_for_update(endpoint="http://127.0.0.1:1")

        with patch.object(cli_update, "query_latest") as probe:
            cli_update.check_for_update(endpoint="http://127.0.0.1:1")
        probe.assert_not_called()

    def test_disabled_by_env_never_touches_network_or_state(self) -> None:
        with patch.dict(os.environ, {"AAW_UPDATE_CHECK": "0"}):
            with patch.object(cli_update, "query_latest") as probe:
                cli_update.check_for_update(endpoint="http://127.0.0.1:1")

        probe.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_successful_probe_records_strict_version_only(self) -> None:
        with patch.object(cli_update, "query_latest", return_value={"latest_version": "v-bad"}):
            cli_update.check_for_update(endpoint="http://127.0.0.1:9")
        self.assertNotIn("latest_version", json.loads(self.state_path.read_text("utf-8")))

        self.state_path.unlink()
        with patch.object(cli_update, "query_latest", return_value={"latest_version": "2.0.0"}):
            cli_update.check_for_update(endpoint="http://127.0.0.1:9")
        self.assertEqual("2.0.0", json.loads(self.state_path.read_text("utf-8"))["latest_version"])


if __name__ == "__main__":
    unittest.main()
