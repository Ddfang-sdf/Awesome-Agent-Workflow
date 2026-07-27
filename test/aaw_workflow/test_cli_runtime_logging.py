from __future__ import annotations

import uuid
from pathlib import Path

import yaml

from _cli_base import CliTestBase


class RuntimeLoggingCliTests(CliTestBase):
    def _workflow_log(self, workflow_id: str) -> Path:
        return self.cwd / ".aaw" / "logs" / "workflows" / f"{workflow_id}.log"

    def test_start_persists_uuid_and_routes_console_output_to_workflow_log(self) -> None:
        result = self.start_sr("SR-LOG")
        workflow = yaml.safe_load(
            (self.cwd / ".sdd" / "SR-LOG" / "workflow.yaml").read_text("utf-8")
        )
        workflow_id = workflow["workflow_id"]
        self.assertEqual(str(uuid.UUID(workflow_id)), workflow_id)
        log = self._workflow_log(workflow_id)
        self.assertTrue(log.is_file())
        content = log.read_text("utf-8")
        self.assertIn(" INFO ", content)
        self.assertIn(" stdout - {", content)
        stdout_rows = [line for line in content.splitlines() if "] stdout - " in line]
        self.assertEqual(1, len(stdout_rows))
        self.assertIn('"sr":"SR-LOG"', stdout_rows[0])
        self.assertIn(f"workflow={workflow_id}", content)
        self.assertIn("sr=SR-LOG", content)
        self.assertRegex(content, r"invocation=[0-9a-f-]{36}")
        self.assertEqual("SR-LOG", result["sr"])

    def test_system_command_writes_human_readable_system_log(self) -> None:
        self.run_cli("--version")
        path = self.cwd / ".aaw" / "logs" / "system.log"
        content = path.read_text("utf-8")
        self.assertRegex(
            content,
            r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} [+-]\d\d:\d\d INFO ",
        )
        self.assertIn(" stdout - ", content)

    def test_logging_can_be_disabled(self) -> None:
        self.run_cli("--version", extra_env={"AAW_LOGGING": "off"})
        self.assertFalse((self.cwd / ".aaw").exists())

    def test_stderr_is_error_and_sensitive_var_is_redacted(self) -> None:
        result = self.run_cli(
            "status",
            "--sr",
            "missing",
            "--json",
            "--var",
            "API_TOKEN=super-secret",
            expect=2,
        )
        self.assertNotEqual("", result.stderr)
        content = (self.cwd / ".aaw" / "logs" / "system.log").read_text("utf-8")
        self.assertIn(" ERROR ", content)
        self.assertIn(" stderr - ", content)
        self.assertNotIn("super-secret", content)
        self.assertIn("API_TOKEN=***", content)

    def test_workflow_log_rolls_at_configured_size(self) -> None:
        req_file = self.cwd / "requirement.md"
        req_file.write_text(self.DEFAULT_REQUIREMENT, "utf-8")
        self.run_cli(
            "start",
            "--entry",
            "sr",
            "--sr",
            "SR-ROLL",
            "--requirement-file",
            str(req_file),
            "--json",
            extra_env={"AAW_LOG_MAX_BYTES": "1024"},
        )
        workflow = yaml.safe_load(
            (self.cwd / ".sdd" / "SR-ROLL" / "workflow.yaml").read_text("utf-8")
        )
        base = self._workflow_log(workflow["workflow_id"])
        self.assertTrue(base.is_file())
        self.assertTrue(base.with_name(base.name + ".1").is_file())

    def test_legacy_workflow_gets_stable_persisted_id_on_load(self) -> None:
        self.start_sr("SR-LEGACY")
        path = self.cwd / ".sdd" / "SR-LEGACY" / "workflow.yaml"
        data = yaml.safe_load(path.read_text("utf-8"))
        data.pop("workflow_id")
        path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            "utf-8",
        )

        self.status_json("SR-LEGACY")
        first = yaml.safe_load(path.read_text("utf-8"))["workflow_id"]
        self.status_json("SR-LEGACY")
        second = yaml.safe_load(path.read_text("utf-8"))["workflow_id"]

        self.assertEqual(first, second)
        self.assertTrue(self._workflow_log(first).is_file())
