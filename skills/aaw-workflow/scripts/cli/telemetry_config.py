"""Telemetry configuration: upload switch and snapshot file filters."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BUILTIN_CONFIG = Path(__file__).parent / "telemetry_config.yaml"
PROJECT_CONFIG_RELATIVE = Path(".aaw") / "telemetry.yaml"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class TelemetryConfigError(Exception):
    pass


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool
    max_file_bytes: int
    excluded_dirs: tuple[str, ...]
    sensitive_names: tuple[re.Pattern[str], ...]
    sensitive_contents: tuple[re.Pattern[bytes], ...]
    diff_excluded_suffixes: frozenset[str]

    def excluded_dir(self, name: str) -> str | None:
        """Return the configured directory that excludes `name`, if any."""
        lowered = name.lower()
        for directory in self.excluded_dirs:
            if lowered.startswith(directory + "/"):
                return directory
        return None

    def is_sensitive_name(self, name: str) -> bool:
        return any(pattern.search(name) for pattern in self.sensitive_names)

    def is_sensitive_content(self, content: bytes) -> bool:
        return any(pattern.search(content) for pattern in self.sensitive_contents)

    def is_sensitive(self, name: str, content: bytes) -> bool:
        return self.is_sensitive_name(name) or self.is_sensitive_content(content)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text("utf-8")) or {}
    except OSError as exc:
        raise TelemetryConfigError(f"Unable to read telemetry config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise TelemetryConfigError(f"Unable to parse telemetry config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise TelemetryConfigError(f"Telemetry config {path} must be a mapping")
    return raw


def _string_list(value: Any, path: Path, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TelemetryConfigError(f"Telemetry config {path} key filters.{key} must be a list of strings")
    return value


def _normalize_dirs(values: list[str], path: Path) -> tuple[list[str], list[str]]:
    """Split configured directories into additions and removals (`!` prefix)."""
    additions: list[str] = []
    removals: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            raise TelemetryConfigError(
                f"Telemetry config {path} key filters.excluded_dirs must not contain an empty entry"
            )
        target = removals if value.startswith("!") else additions
        remainder = value[1:].strip() if value.startswith("!") else value
        if not remainder:
            raise TelemetryConfigError(
                f"Telemetry config {path} key filters.excluded_dirs entry {raw!r} has an empty directory after '!'"
            )
        if remainder.startswith(("/", "\\")) or ":" in remainder:
            raise TelemetryConfigError(
                f"Telemetry config {path} key filters.excluded_dirs entry {raw!r} must be a relative path"
            )
        normalized = remainder.replace("\\", "/").strip("/").lower()
        if not normalized:
            raise TelemetryConfigError(
                f"Telemetry config {path} key filters.excluded_dirs entry {raw!r} is not a valid directory"
            )
        if normalized.startswith("!"):
            raise TelemetryConfigError(
                f"Telemetry config {path} key filters.excluded_dirs entry {raw!r} must not start with '!!'"
            )
        if ".." in normalized.split("/"):
            raise TelemetryConfigError(
                f"Telemetry config {path} key filters.excluded_dirs entry {raw!r} must not contain '..'"
            )
        target.append(normalized)
    return additions, removals


def _compile(patterns: list[str], path: Path, key: str, *, binary: bool) -> tuple[Any, ...]:
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern.encode("utf-8") if binary else pattern, re.I))
        except re.error as exc:
            raise TelemetryConfigError(
                f"Telemetry config {path} key filters.{key} has an invalid regex {pattern!r}: {exc}"
            ) from exc
    return tuple(compiled)


def _env_override() -> bool | None:
    raw = os.getenv("AAW_TELEMETRY_ENABLED")
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise TelemetryConfigError(
        f"Environment variable AAW_TELEMETRY_ENABLED has an invalid value {raw!r}; "
        "use one of 1/true/yes/on or 0/false/no/off"
    )


@lru_cache(maxsize=None)
def load_config(root: Path) -> TelemetryConfig:
    if not BUILTIN_CONFIG.is_file():
        raise TelemetryConfigError(f"Built-in telemetry config is missing: {BUILTIN_CONFIG}")
    builtin = _load_yaml(BUILTIN_CONFIG)
    enabled = builtin.get("enabled")
    filters = builtin.get("filters")
    if not isinstance(filters, dict):
        raise TelemetryConfigError(f"Telemetry config {BUILTIN_CONFIG} key filters must be a mapping")
    filters = dict(filters)
    source = {"enabled": BUILTIN_CONFIG, "filters": BUILTIN_CONFIG}

    project_path = root / PROJECT_CONFIG_RELATIVE
    if project_path.is_file():
        project = _load_yaml(project_path)
        if "enabled" in project:
            enabled = project["enabled"]
            source["enabled"] = project_path
        if "filters" in project:
            project_filters = project["filters"]
            if not isinstance(project_filters, dict):
                raise TelemetryConfigError(f"Telemetry config {project_path} key filters must be a mapping")
            filters.update(project_filters)
            source["filters"] = project_path

    environment = _env_override()
    if environment is not None:
        enabled = environment
    elif not isinstance(enabled, bool):
        raise TelemetryConfigError(f"Telemetry config {source['enabled']} key enabled must be a boolean")

    filters_path = source["filters"]
    max_file_bytes = filters.get("max_file_bytes")
    if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or max_file_bytes <= 0:
        raise TelemetryConfigError(
            f"Telemetry config {filters_path} key filters.max_file_bytes must be a positive integer"
        )
    names = _compile(
        _string_list(filters.get("sensitive_names"), filters_path, "sensitive_names"),
        filters_path,
        "sensitive_names",
        binary=False,
    )
    contents = _compile(
        _string_list(filters.get("sensitive_contents"), filters_path, "sensitive_contents"),
        filters_path,
        "sensitive_contents",
        binary=True,
    )
    suffixes = _string_list(filters.get("diff_excluded_suffixes"), filters_path, "diff_excluded_suffixes")
    builtin_dirs_add, builtin_dirs_remove = _normalize_dirs(
        _string_list(builtin["filters"].get("excluded_dirs"), BUILTIN_CONFIG, "excluded_dirs"),
        BUILTIN_CONFIG,
    )
    project_dirs_add, project_dirs_remove = [], []
    if project_path.is_file():
        project_filters = project.get("filters")
        if isinstance(project_filters, dict) and "excluded_dirs" in project_filters:
            project_dirs_add, project_dirs_remove = _normalize_dirs(
                _string_list(project_filters["excluded_dirs"], project_path, "excluded_dirs"),
                project_path,
            )
    excluded_dirs = (
        set(builtin_dirs_add) | set(project_dirs_add)
    ) - (
        set(builtin_dirs_remove) | set(project_dirs_remove)
    )
    return TelemetryConfig(
        enabled=enabled,
        max_file_bytes=max_file_bytes,
        excluded_dirs=tuple(sorted(excluded_dirs)),
        sensitive_names=names,
        sensitive_contents=contents,
        diff_excluded_suffixes=frozenset(suffix.lower() for suffix in suffixes),
    )
