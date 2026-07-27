"""Best-effort, repository-local runtime logging for the AAW CLI.

The console streams remain authoritative.  This module tees their text into
human-readable, Log4j-style files under ``<repo>/.aaw/logs`` without ever
turning a logging failure into a CLI failure.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO


_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_SENSITIVE_OPTIONS = re.compile(
    r"^--(?:token|password|passwd|secret|api[-_]?key|access[-_]?key|private[-_]?key)(?:=|$)",
    re.IGNORECASE,
)
_DEFAULT_MAX_BYTES = 100 * 1024 * 1024
_RETENTION_DAYS = 30
_LOCK_TIMEOUT = 2.0

_manager: RuntimeLogManager | None = None


def _env_enabled() -> bool:
    return os.environ.get("AAW_LOGGING", "on").strip().lower() not in {
        "0", "false", "no", "off", "disabled",
    }


def _repo_root(cwd: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return cwd.resolve()


def _masked_argv(argv: list[str]) -> str:
    masked: list[str] = []
    hide_next = False
    hide_var = False
    for item in argv:
        if hide_next:
            masked.append("***")
            hide_next = False
            continue
        if hide_var:
            key, separator, _value = item.partition("=")
            masked.append(
                f"{key}=***"
                if separator and re.search(
                    r"(?:token|password|passwd|secret|api[-_]?key|private[-_]?key)",
                    key,
                    re.IGNORECASE,
                )
                else item
            )
            hide_var = False
            continue
        if item == "--var":
            masked.append(item)
            hide_var = True
            continue
        if _SENSITIVE_OPTIONS.match(item):
            if "=" in item:
                masked.append(item.split("=", 1)[0] + "=***")
            else:
                masked.append(item)
                hide_next = True
            continue
        masked.append(item)
    return " ".join(masked)


def _clean_text(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    cleaned: list[str] = []
    for char in text:
        code = ord(char)
        if char == "\t":
            cleaned.append(r"\t")
        elif char == "\r":
            cleaned.append(r"\r")
        elif code < 32 or code == 127:
            cleaned.append(f"\\x{code:02x}")
        else:
            cleaned.append(char)
    return "".join(cleaned)


def _timestamp() -> str:
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + f" {offset}"


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(self.path, "a+b")
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.stream.seek(0, os.SEEK_END)
                    if self.stream.tell() == 0:
                        self.stream.write(b"\0")
                        self.stream.flush()
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise TimeoutError(f"logging lock timeout: {self.path}")
                time.sleep(0.02)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


class _TeeStream:
    def __init__(self, original: TextIO, manager: "RuntimeLogManager", stream: str, level: str):
        self._original = original
        self._manager = manager
        self._stream = stream
        self._level = level
        self._buffer = ""
        self._lock = threading.RLock()

    def write(self, text: str) -> int:
        written = self._original.write(text)
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.endswith("\r"):
                    line = line[:-1]
                self._manager.record(self._level, self._stream, line)
        return written

    def flush(self) -> None:
        self._original.flush()
        with self._lock:
            if self._buffer:
                line, self._buffer = self._buffer, ""
                self._manager.record(self._level, self._stream, line)

    def __getattr__(self, name):
        return getattr(self._original, name)


class RuntimeLogManager:
    def __init__(self, argv: list[str]) -> None:
        self.root = _repo_root(Path.cwd())
        self.logs_dir = self.root / ".aaw" / "logs"
        self.workflow_dir = self.logs_dir / "workflows"
        inherited_id = os.environ.get("AAW_INVOCATION_ID")
        try:
            self.invocation_id = str(uuid.UUID(inherited_id)) if inherited_id else str(uuid.uuid4())
        except ValueError:
            self.invocation_id = str(uuid.uuid4())
        os.environ["AAW_INVOCATION_ID"] = self.invocation_id
        requested = os.environ.get("AAW_LOG_LEVEL", "INFO").strip().upper()
        self.level = requested if requested in _LEVELS else "INFO"
        try:
            self.max_bytes = max(1024, int(os.environ.get("AAW_LOG_MAX_BYTES", _DEFAULT_MAX_BYTES)))
        except ValueError:
            self.max_bytes = _DEFAULT_MAX_BYTES
        self.workflow_id: str | None = None
        self.sr: str | None = None
        self.ar: str | None = None
        self.target: Path | None = None
        self.pending: list[tuple[str, str, str]] = []
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.stdout = _TeeStream(sys.stdout, self, "stdout", "INFO")
        self.stderr = _TeeStream(sys.stderr, self, "stderr", "ERROR")
        self._warning_emitted = False
        self._finished = False
        self._sequence = 0
        self._state_lock = threading.RLock()
        self._prepare()
        resumed = self._load_handoff()
        self.force_system = self._is_system_command(argv)
        if self.force_system:
            self.bind_system()
        sys.stdout = self.stdout
        sys.stderr = self.stderr
        action = "resume after update" if resumed else "start"
        self.record("INFO", "aaw.launcher", f"invocation {action} argv={_masked_argv(argv)}")

    @staticmethod
    def _is_system_command(argv: list[str]) -> bool:
        if not argv:
            return True
        command = argv[0]
        if command == "update" or command.startswith("-"):
            return True
        return command == "status" and "--sr" not in argv

    def _prepare(self) -> None:
        try:
            self.workflow_dir.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self.logs_dir, 0o700)
                os.chmod(self.workflow_dir, 0o700)
            self._cleanup()
        except OSError as exc:
            self._warn(f"aaw logging warning: cannot prepare {self.logs_dir}: {exc}")

    def _handoff_path(self) -> Path:
        return self.logs_dir / f".handoff-{self.invocation_id}.json"

    def _load_handoff(self) -> bool:
        path = self._handoff_path()
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text("utf-8"))
            rows = data.get("pending") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise ValueError("pending must be a list")
            for row in rows:
                if (
                    isinstance(row, list)
                    and len(row) == 3
                    and all(isinstance(item, str) for item in row)
                ):
                    self.pending.append((row[0], row[1], row[2]))
            return True
        except (OSError, ValueError) as exc:
            self._warn(f"aaw logging warning: cannot consume update handoff: {exc}")
            return False
        finally:
            path.unlink(missing_ok=True)

    def _warn(self, message: str) -> None:
        if self._warning_emitted:
            return
        self._warning_emitted = True
        try:
            self.original_stderr.write(message + "\n")
            self.original_stderr.flush()
        except Exception:
            pass

    def _cleanup(self) -> None:
        stamp = self.logs_dir / ".cleanup.stamp"
        try:
            with _FileLock(self.logs_dir / ".cleanup.lock"):
                if stamp.is_file() and time.time() - stamp.stat().st_mtime < 86400:
                    return
                self._cleanup_expired()
                stamp.touch()
                if os.name != "nt":
                    os.chmod(stamp, 0o600)
        except (OSError, TimeoutError):
            return

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - _RETENTION_DAYS * 86400
        groups: dict[str, list[Path]] = {}
        for path in self.workflow_dir.glob("*.log*"):
            if path.name.endswith(".lock"):
                continue
            base = path.name.split(".log", 1)[0]
            groups.setdefault(base, []).append(path)
        for paths in groups.values():
            if paths and max(p.stat().st_mtime for p in paths) < cutoff:
                lock_path = paths[0].parent / f"{paths[0].name.split('.log', 1)[0]}.log.lock"
                try:
                    with _FileLock(lock_path):
                        if max((p.stat().st_mtime for p in paths if p.exists()), default=cutoff) < cutoff:
                            for path in paths:
                                path.unlink(missing_ok=True)
                except (OSError, TimeoutError):
                    pass
        for path in self.logs_dir.glob("system.log.*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        system = self.logs_dir / "system.log"
        if system.is_file():
            try:
                with _FileLock(system.with_name(system.name + ".lock")):
                    self._prune_system(
                        system,
                        datetime.now().astimezone() - timedelta(days=_RETENTION_DAYS),
                    )
            except (OSError, TimeoutError):
                pass

    @staticmethod
    def _prune_system(path: Path, cutoff: datetime) -> None:
        stamp_re = re.compile(
            r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} [+-]\d\d:\d\d)"
        )
        lines = path.read_text("utf-8", errors="replace").splitlines(keepends=True)
        kept: list[str] = []
        changed = False
        for line in lines:
            match = stamp_re.match(line)
            if not match:
                kept.append(line)
                continue
            try:
                event_time = datetime.fromisoformat(match.group(1))
            except ValueError:
                kept.append(line)
                continue
            if event_time >= cutoff:
                kept.append(line)
            else:
                changed = True
        if changed:
            temporary = path.with_name(path.name + f".prune-{os.getpid()}")
            temporary.write_text("".join(kept), "utf-8")
            os.replace(temporary, path)

    def bind_workflow(self, workflow_id: str, sr: str, ar: str | None = None) -> None:
        if self.force_system:
            return
        try:
            normalized = str(uuid.UUID(workflow_id))
        except (ValueError, AttributeError):
            self._warn(f"aaw logging warning: invalid workflow id {workflow_id!r}")
            return
        with self._state_lock:
            self.workflow_id = normalized
            self.sr = sr
            self.ar = ar
            self.target = self.workflow_dir / f"{normalized}.log"
            pending, self.pending = self.pending, []
        for level, location, message in pending:
            self._write(level, location, message)

    def bind_system(self) -> None:
        with self._state_lock:
            self.target = self.logs_dir / "system.log"
            pending, self.pending = self.pending, []
        for level, location, message in pending:
            self._write(level, location, message)

    def record(self, level: str, location: str, message: str) -> None:
        if _LEVELS.get(level, 20) < _LEVELS[self.level]:
            return
        cleaned = _clean_text(str(message))
        with self._state_lock:
            if self.target is None:
                self.pending.append((level, location, cleaned))
                return
        self._write(level, location, cleaned)

    def _write(self, level: str, location: str, message: str) -> None:
        target = self.target
        if target is None:
            return
        with self._state_lock:
            self._sequence += 1
            sequence = self._sequence
            context = (
                f"pid={os.getpid()} thread={threading.current_thread().name} "
                f"workflow={self.workflow_id or '-'} sr={self.sr or '-'} "
                f"ar={self.ar or '-'} invocation={self.invocation_id} seq={sequence}"
            )
        line = f"{_timestamp()} {level:<5} [{context}] {location} - {message}\n"
        try:
            with _FileLock(target.with_name(target.name + ".lock")):
                self._rotate(target, len(line.encode("utf-8")))
                with open(target, "a", encoding="utf-8", newline="") as stream:
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
                if os.name != "nt":
                    os.chmod(target, 0o600)
        except (OSError, TimeoutError) as exc:
            self._warn(f"aaw logging warning: {exc}")

    def _rotate(self, target: Path, incoming_bytes: int) -> None:
        if not target.exists() or target.stat().st_size + incoming_bytes <= self.max_bytes:
            return
        indices: list[int] = []
        for path in target.parent.glob(target.name + ".*"):
            suffix = path.name[len(target.name) + 1 :]
            if suffix.isdigit():
                indices.append(int(suffix))
        target.rename(target.with_name(target.name + f".{max(indices, default=0) + 1}"))

    def log_exception(self, exc: BaseException) -> None:
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            return
        frames = traceback.extract_tb(exc.__traceback__)
        if frames:
            frame = frames[-1]
            location = f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
        else:
            location = "aaw.unhandled"
        for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
            for part in line.rstrip("\n").splitlines():
                self.record("ERROR", location, part)

    def echo_json(self, pretty: str, compact: str) -> None:
        """Keep pretty console JSON while logging the payload as one record."""
        self.original_stdout.write(pretty + "\n")
        self.original_stdout.flush()
        self.record("INFO", "stdout", compact)

    def prepare_reexec(self) -> None:
        """Persist unresolved startup records across an update exec handoff."""
        if self._finished:
            return
        self.stdout.flush()
        self.stderr.flush()
        try:
            with self._state_lock:
                pending = [list(item) for item in self.pending]
                self.pending = []
            path = self._handoff_path()
            temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
            temporary.write_text(
                json.dumps({"schema": 1, "pending": pending}, ensure_ascii=False),
                "utf-8",
            )
            os.replace(temporary, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError as exc:
            self._warn(f"aaw logging warning: cannot persist update handoff: {exc}")
            with self._state_lock:
                self.pending.extend(tuple(item) for item in pending)
            self.bind_system()
        self._finished = True
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr

    def finish(self, exit_code: object = 0) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self.stdout.flush()
            self.stderr.flush()
            if self.target is None:
                self.bind_system()
            self.record("INFO", "aaw.launcher", f"invocation end exit_code={exit_code}")
        finally:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr


def initialize(argv: list[str] | None = None) -> RuntimeLogManager | None:
    global _manager
    if _manager is not None or not _env_enabled():
        return _manager
    try:
        _manager = RuntimeLogManager(list(argv if argv is not None else sys.argv[1:]))
        atexit.register(finish)
    except Exception as exc:
        try:
            sys.__stderr__.write(f"aaw logging warning: initialization failed: {exc}\n")
        except Exception:
            pass
        _manager = None
    return _manager


def bind_workflow(workflow_id: str, sr: str, ar: str | None = None) -> None:
    if _manager is not None:
        _manager.bind_workflow(workflow_id, sr, ar)


def log(level: str, location: str, message: str) -> None:
    if _manager is not None:
        _manager.record(level, location, message)


def log_exception(exc: BaseException) -> None:
    if _manager is not None:
        _manager.log_exception(exc)


def echo_json(pretty: str, compact: str) -> bool:
    if _manager is None:
        return False
    _manager.echo_json(pretty, compact)
    return True


def finish(exit_code: object = 0) -> None:
    if _manager is not None:
        _manager.finish(exit_code)


def prepare_reexec() -> None:
    if _manager is not None:
        _manager.prepare_reexec()
