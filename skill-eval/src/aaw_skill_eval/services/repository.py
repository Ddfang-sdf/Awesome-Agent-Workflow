from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import EvalError, InfrastructureError


@dataclass(frozen=True)
class ProjectSnapshot:
    path: Path
    name: str
    commit: str
    tree: str
    remote: str | None


def _git(path: Path, *args: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InfrastructureError("GIT_UNAVAILABLE", f"Git command failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise EvalError("GIT_ERROR", detail or "Git command failed")
    return result.stdout.strip()


def inspect_clean_project(raw_path: str | Path) -> ProjectSnapshot:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_dir():
        raise EvalError("PROJECT_NOT_FOUND", f"Project directory does not exist: {path}")
    try:
        root = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    except EvalError as exc:
        raise EvalError("NOT_GIT_REPOSITORY", f"Not a Git repository: {path}") from exc

    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        lines = status.splitlines()
        preview = "\n".join(lines[:20])
        suffix = f"\n... and {len(lines) - 20} more" if len(lines) > 20 else ""
        raise EvalError(
            "PROJECT_DIRTY",
            "Project must have a clean working tree before evaluation:\n" + preview + suffix,
        )
    commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    remote_result = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    remote = remote_result.stdout.strip() if remote_result.returncode == 0 else None
    return ProjectSnapshot(path=root, name=root.name, commit=commit, tree=tree, remote=remote)


def clone_at_commit(snapshot: ProjectSnapshot, destination: Path) -> None:
    if destination.exists():
        raise InfrastructureError("WORKSPACE_EXISTS", f"Workspace already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--no-hardlinks",
                "--no-checkout",
                "--",
                str(snapshot.path),
                str(destination),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
            check=False,
        )
        if clone.returncode != 0:
            raise InfrastructureError(
                "CLONE_FAILED", (clone.stderr or clone.stdout).strip()[-3000:]
            )
        checkout = subprocess.run(
            ["git", "-C", str(destination), "checkout", "--detach", snapshot.commit],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
            check=False,
        )
        if checkout.returncode != 0:
            raise InfrastructureError(
                "CHECKOUT_FAILED", (checkout.stderr or checkout.stdout).strip()[-3000:]
            )
    except subprocess.TimeoutExpired as exc:
        raise InfrastructureError("CLONE_TIMEOUT", f"Project clone timed out: {exc}") from exc


def run_trusted_command(command: str, cwd: Path, timeout_seconds: int) -> dict:
    started = __import__("time").monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout[-100_000:],
            "stderr": result.stderr[-100_000:],
            "duration_ms": int((__import__("time").monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": None,
            "stdout": (exc.stdout or "")[-100_000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-100_000:] if isinstance(exc.stderr, str) else "",
            "duration_ms": int((__import__("time").monotonic() - started) * 1000),
            "timed_out": True,
        }


def capture_changes(workspace: Path) -> dict:
    patch = _git(workspace, "diff", "--binary", "HEAD", timeout=120)
    raw_status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if raw_status.returncode != 0:
        raise InfrastructureError("GIT_STATUS_FAILED", "Unable to capture final Git status")
    entries = [item for item in raw_status.stdout.decode("utf-8", "replace").split("\0") if item]
    changed_files: list[str] = []
    untracked_files: list[str] = []
    for entry in entries:
        if len(entry) < 4:
            continue
        code = entry[:2]
        path = entry[3:].replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith((".aaw-eval/", ".agents/skills/aaw-eval-")):
            continue
        changed_files.append(path)
        if code == "??":
            untracked_files.append(path)
    return {
        "patch": patch,
        "changed_files": sorted(set(changed_files)),
        "untracked_files": sorted(set(untracked_files)),
    }


def file_tree_manifest(workspace: Path) -> list[dict]:
    manifest: list[dict] = []
    for root, directories, files in os.walk(workspace, topdown=True):
        current = Path(root)
        directories[:] = sorted(
            directory
            for directory in directories
            if directory != ".git"
            and not (current == workspace and directory == ".aaw-eval")
            and not (
                current.relative_to(workspace).as_posix() == ".agents/skills"
                and directory.startswith("aaw-eval-")
            )
        )
        for name in sorted(files):
            path = current / name
            relative = path.relative_to(workspace).as_posix()
            try:
                stat_result = path.stat()
            except OSError:
                continue
            manifest.append({"path": relative, "size": stat_result.st_size})
    return manifest
