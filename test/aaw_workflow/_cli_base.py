"""Shared helpers for CLI-level tests of skills/aaw-workflow/scripts/cli.

Each test_cli_*.py file covers one CLI command; this module provides the
subprocess runner and workflow-advancing helpers they share.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AAW_SCRIPT = ROOT / "skills" / "aaw-workflow" / "scripts" / "aaw.py"
SCRIPTS_DIR = AAW_SCRIPT.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class _NoReleaseHandler(BaseHTTPRequestHandler):
    """Hermetic endpoint: release queries see "no release published"; every
    other request (telemetry uploads) fails with 404 like before."""

    def do_GET(self):  # noqa: N802
        if self.path == "/api/v1/client/release":
            payload = b'{"latest_version": null}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):  # noqa: N802
        self.send_response(404)
        self.end_headers()

    do_PUT = do_POST

    def log_message(self, *args):  # silence
        pass


_fixture_server = ThreadingHTTPServer(("127.0.0.1", 0), _NoReleaseHandler)
threading.Thread(target=_fixture_server.serve_forever, daemon=True).start()
FIXTURE_ENDPOINT = f"http://127.0.0.1:{_fixture_server.server_address[1]}"


class CliTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(
        self,
        *args: str,
        expect: int = 0,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            # Hermetic: telemetry + release queries hit a local fixture server
            # that publishes no release and rejects uploads.
            "AAW_TELEMETRY_ENDPOINT": FIXTURE_ENDPOINT,
            **(extra_env or {}),
        }
        result = subprocess.run(
            [sys.executable, str(AAW_SCRIPT), *args],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.assertEqual(
            expect,
            result.returncode,
            msg=f"argv={args!r}\nstdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        return result

    DEFAULT_REQUIREMENT = "原始需求：示例系统需要支持用户管理。\n"

    def start_sr(self, sr: str, requirement: str | None = None) -> dict:
        req_file = self.cwd / f".aaw-test-requirement-{sr}.md"
        req_file.write_text(requirement or self.DEFAULT_REQUIREMENT, "utf-8")
        result = self.run_cli(
            "start", "--entry", "sr", "--sr", sr,
            "--requirement-file", str(req_file), "--json",
        )
        return json.loads(result.stdout)

    def user_confirm(self, sr: str) -> dict:
        return json.loads(self.run_cli("user-confirm", "--sr", sr, "--json").stdout)

    def start_ar(self, sr: str, ar: str = "AR-001", title: str = "用户管理") -> dict:
        (self.cwd / ".sdd").mkdir(parents=True, exist_ok=True)
        (self.cwd / ".sdd" / "software_architecture.md").write_text("architecture", "utf-8")
        result = self.run_cli(
            "start", "--entry", "ar", "--sr", sr,
            "--var", f"AR={ar}", "--var", f"TITLE={title}", "--json",
        )
        return json.loads(result.stdout)

    def advance_to_ar_clarify_done(self, sr: str, ar: str = "AR-001") -> dict:
        """Finish steps 1-2 of the ar entry.

        The ar-clarify -> module-boundary-design edge is `user_confirm: must`,
        so `done` on step 2 leaves the workflow awaiting confirmation.
        """
        self.start_ar(sr, ar)
        self.run_cli("next", "--sr", sr, "--json")
        self.run_cli("done", "--sr", sr, "1", "--json")
        clarify = self.cwd / ".sdd" / sr / ar / "AR-clarify.md"
        clarify.parent.mkdir(parents=True, exist_ok=True)
        clarify.write_text("ar clarify", "utf-8")
        self.run_cli("next", "--sr", sr, "--json")
        return json.loads(self.run_cli("done", "--sr", sr, "2", "--json").stdout)

    def complete_step_1(self, sr: str) -> dict:
        """step 1 (sr-init) is a skill step: needs `next` first plus its output file."""
        self.run_cli("next", "--sr", sr, "--json")
        (self.cwd / ".sdd" / "software_architecture.md").write_text("architecture", "utf-8")
        return json.loads(self.run_cli("done", "--sr", sr, "1", "--json").stdout)

    def advance_to_ar_split(self, sr: str) -> None:
        """Finish steps 1-3 so step 4 (ar-split, requires --data) is ready.

        The final `next` marks step 4 started — prompt steps also require an
        actual start timestamp before `done` now.
        """
        self.start_sr(sr)
        self.complete_step_1(sr)
        (self.cwd / ".sdd" / sr / "SR-design.md").write_text("sr design", "utf-8")
        self.run_cli("next", "--sr", sr, "--json")
        self.run_cli("done", "--sr", sr, "2", "--json")
        self.run_cli("next", "--sr", sr, "--json")
        gate_report = f".sdd/{sr}/SR-design-gate.md"
        (self.cwd / gate_report).write_text("gate report", "utf-8")
        self.run_cli(
            "done",
            "--sr",
            sr,
            "3",
            "--data",
            json.dumps(
                {
                    "gate_result": "pass",
                    "recommendation": "可进入 AR 拆分",
                    "report": gate_report,
                    "summary": {
                        "unqualified_dimensions": 0,
                        "p0_conflicts": 0,
                        "p1_conflicts": 0,
                        "p2_findings": 0,
                        "pending_questions": 0,
                        "blocking_issues": 0,
                    },
                },
                ensure_ascii=False,
            ),
            "--json",
        )
        self.run_cli("next", "--sr", sr, "--json")

    def status_json(self, sr: str) -> dict:
        return json.loads(self.run_cli("status", "--sr", sr, "--json").stdout)
