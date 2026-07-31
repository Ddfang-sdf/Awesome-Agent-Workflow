"""Minimal HTTP server that mimics the telemetry release API for local testing."""
from __future__ import annotations

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "dist"
ZIP_NAME = "aaw-skills-2.3.2.zip"
VERSION = "2.3.2"
ZIP_PATH = DIST / ZIP_NAME


class ReleaseHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST))

    def do_GET(self):
        if self.path == "/api/v1/client/release":
            size = ZIP_PATH.stat().st_size if ZIP_PATH.is_file() else 0
            body = json.dumps({
                "latest_version": VERSION,
                "file_name": ZIP_NAME,
                "size_bytes": size,
                "released_at": "2026-07-24T00:00:00Z",
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())
            return

        if self.path.startswith(f"/api/v1/client/releases/{VERSION}/download/"):
            if ZIP_PATH.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename={ZIP_NAME}")
                self.send_header("Content-Length", str(ZIP_PATH.stat().st_size))
                self.end_headers()
                with open(ZIP_PATH, "rb") as f:
                    self.wfile.write(f.read())
                return

        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        print(f"[server] {args[0]}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9876"))
    server = HTTPServer(("127.0.0.1", port), ReleaseHandler)
    print(f"Test release server on http://127.0.0.1:{port}")
    server.serve_forever()
