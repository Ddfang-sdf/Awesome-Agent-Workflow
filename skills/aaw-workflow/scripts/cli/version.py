"""Unified AAW version source and update-check state.

The VERSION file lives next to this module so that every distribution form
(copy, symlink, zip update) carries it along; the moment a new skill tree is
swapped in, the reported version is the new one.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

FALLBACK_VERSION = "0.0.0"

# Strict three-part version, no leading zeros (see docs/auto-update-design.md §3.2).
_STRICT_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def aaw_version() -> str:
    version_file = Path(os.path.abspath(__file__)).with_name("VERSION")
    try:
        text = version_file.read_text("utf-8").strip()
    except OSError:
        return FALLBACK_VERSION
    return text or FALLBACK_VERSION


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Return the (major, minor, patch) tuple, or None if not a strict version."""
    match = _STRICT_VERSION.match(value.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_newer(candidate: str, current: str) -> bool:
    """True when candidate is a valid version strictly newer than current.

    An invalid candidate never wins; an invalid current is treated as the
    lowest version so a corrupted local install can still be upgraded.
    """
    candidate_parts = parse_version(candidate)
    if candidate_parts is None:
        return False
    return candidate_parts > (parse_version(current) or (0, 0, 0))


def update_state_path() -> Path:
    override = os.getenv("AAW_UPDATE_STATE")
    return Path(override) if override else Path.home() / ".aaw" / "update-check.json"


def load_update_state() -> dict:
    try:
        data = json.loads(update_state_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_update_state(state: dict) -> None:
    """Best-effort atomic write; never raises."""
    try:
        path = update_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
        tmp.replace(path)
    except OSError:
        pass
