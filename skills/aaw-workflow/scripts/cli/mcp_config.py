"""MCP configuration injection for AAW auto-update.

Automatically detects the agent host platform, agent type, and MCP binary
location, then injects or updates question-tracker MCP server registration
into the agent's configuration file.

Called by ``update.py`` during the transaction swap phase.  Also usable
as a standalone module for manual MCP configuration.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MCP_SERVER_NAME = "question-tracker"
MCP_BINARY_DIR = "question-tracker-mcp"
MCP_BIN_SUBDIR = "bin"
CHRYS_BUILTIN_PACKAGE = "chrys.service.profiles.agents.builtins"
CHRYS_PROFILE_NAME = "Code"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MCPConfigError(Exception):
    """MCP configuration injection failure.

    Raised by ``upsert_*_mcp`` functions when the configuration file cannot
    be read or written.  Propagated to ``update.py``'s transaction layer
    which triggers a rollback.
    """

    def __init__(self, message: str, config_file: str = "") -> None:
        super().__init__(message)
        self.config_file = config_file


def _posix(path: Path | str) -> str:
    """Return the path with forward slashes for config files.

    Config files (JSON/TOML/YAML) written for cross-agent consumption use
    POSIX-style separators uniformly: backslashes are escape characters in
    YAML and look broken mixed with slashes on Windows.  Windows APIs accept
    forward slashes everywhere, so this is always safe.
    """
    return str(path).replace("\\", "/")


# ===================================================================
# PG01: Platform detection
# ===================================================================


def detect_platform() -> str:
    """Return the current platform identifier for MCP binary selection.

    Returns one of ``"windows"``, ``"linux"``, ``"macos"``.
    Falls back to ``"windows"`` on unknown platforms (WSL, Git Bash,
    MSYS2) because the statically-linked ``mcp_server.exe`` runs under
    Wine and WSL compatibility layers.
    """
    if os.name == "nt":
        return "windows"
    if sys.platform == "linux":
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    return "windows"


# ===================================================================
# PG02: Agent type detection
# ===================================================================


def detect_target(skills_root: Path) -> str | None:
    """Detect which agent host owns *skills_root*.

    Match priority: chrys → claude → opencode → codex → None.

    Returns ``None`` when the path does not match any known agent pattern,
    signalling callers to skip MCP configuration injection.
    """
    root = str(skills_root.resolve())

    # Chrys: ~/.chrys/skills (Unix) or %APPDATA%/chrys/skills (Windows)
    if root.endswith(os.sep + ".chrys" + os.sep + "skills") or root.endswith(
        os.sep + "chrys" + os.sep + "skills"
    ):
        return "chrys"

    # Claude: ~/.claude/skills or <cwd>/.claude/skills
    if root.endswith(os.sep + ".claude" + os.sep + "skills"):
        return "claude"

    # OpenCode: ~/.config/opencode/skills or <cwd>/.opencode/skills
    if root.endswith(os.sep + "opencode" + os.sep + "skills"):
        return "opencode"

    # Codex: ~/.codex/... (skills may be directly under .codex)
    if (os.sep + ".codex") in root:
        return "codex"

    # Fallback: read .aaw-target marker written by install.sh on first install
    marker = skills_root / ".aaw-target"
    if marker.is_file():
        target = marker.read_text("utf-8").strip()
        if target in ("claude", "codex", "opencode", "chrys"):
            return target

    return None


# ===================================================================
# PG03: Configuration file path resolution
# ===================================================================


def resolve_config_path(target: str, skills_root: Path) -> Path | None:
    """Return the agent configuration file path for *target*."""
    home = Path.home()

    if target == "claude":
        return home / ".claude.json"

    if target == "codex":
        codex_root = (
            skills_root.parent if skills_root.name == "skills" else skills_root
        )
        return codex_root / "config.toml"

    if target == "opencode":
        return skills_root.parent / "opencode.json"

    if target == "chrys":
        if os.name == "nt":
            appdata = os.environ.get(
                "APPDATA", str(home / "AppData" / "Roaming")
            )
            return Path(appdata) / "chrys" / "agents" / "Code.yaml"
        return home / ".chrys" / "agents" / "Code.yaml"

    return None


# ===================================================================
# PG04: MCP binary path resolution
# ===================================================================


def resolve_mcp_exe(skills_root: Path, platform: str) -> Path | None:
    """Return the absolute path to the MCP binary for *platform*.

    Returns ``None`` when the binary does not exist on disk (macOS not
    yet built, or file unexpectedly deleted).
    """
    exe_name = "mcp_server.exe" if platform == "windows" else "mcp_server"
    exe_path = skills_root / MCP_BINARY_DIR / MCP_BIN_SUBDIR / platform / exe_name

    if exe_path.is_file():
        return exe_path
    return None


# ===================================================================
# PG10: Legacy configuration detection
# ===================================================================


def _is_legacy_uv_config(entry: dict) -> bool:
    """Check whether an MCP entry is the old ``uv run python`` format."""
    command = entry.get("command", "")
    args = entry.get("args", [])

    # Claude / Codex / Chrys format: command is a string
    if command == "uv" and isinstance(args, list):
        args_str = " ".join(str(a) for a in args)
        if "fastmcp" in args_str and "mcp_server.py" in args_str:
            return True

    # OpenCode format: command is a list
    if isinstance(command, list) and len(command) > 0:
        if command[0] == "uv" and "fastmcp" in str(command):
            return True

    return False


# ===================================================================
# JSON / JSONC loading helpers
# ===================================================================


def _strip_jsonc_comments(text: str) -> str:
    """Remove // line comments and /* */ block comments from JSONC text.

    Respects string literals so that a ``//`` or ``/*`` inside a quoted
    string (e.g. a URL like ``"https://..."``) is never treated as a comment.
    """
    out = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            # line comment: skip to end of line (keep the newline)
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            # block comment: skip to */ (preserve newlines for line numbers)
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] in "\r\n":
                    out.append(text[i])
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _load_json_config(config_file: Path) -> dict:
    """Load a JSON/JSONC config file.

    - Missing file → empty dict (fresh write).
    - Parses ``//`` and ``/* */`` comments (Claude Code ~/.claude.json is JSONC).
    - Raises ``MCPConfigError`` on unparseable content — the caller must NOT
      overwrite the file in that case.
    """
    if not config_file.exists():
        return {}
    text = config_file.read_text("utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = json.loads(_strip_jsonc_comments(text))
        except json.JSONDecodeError as e:
            raise MCPConfigError(
                f"配置文件无法解析（含注释或语法错误），为安全起见未做修改: {config_file}: {e}",
                str(config_file),
            ) from e
    if not isinstance(data, dict):
        raise MCPConfigError(
            f"配置文件顶层不是 JSON 对象，为安全起见未做修改: {config_file}",
            str(config_file),
        )
    return data


# ===================================================================
# PG05: Claude MCP configuration injection
# ===================================================================


def upsert_claude_mcp(
    config_file: Path, exe_path: Path, skills_root: Path
) -> bool:
    """Upsert question-tracker into ``~/.claude.json``.

    Scope (user vs project) is inferred from *skills_root*.
    Returns ``True`` when the file was modified.
    Returns ``False`` when the file cannot be parsed (never overwrites).
    """
    # 1. Read existing config (JSONC-tolerant; refuse to overwrite on parse failure)
    try:
        data = _load_json_config(config_file)
    except MCPConfigError as e:
        logger.warning("%s", e)
        return False

    # 2. Determine scope
    home = Path.home().resolve()
    user_skills = (home / ".claude" / "skills").resolve()
    resolved_root = skills_root.resolve()
    if str(resolved_root).startswith(str(user_skills)):
        scope_key = "__global__"
        servers = data.setdefault("mcpServers", {})
    else:
        # Project scope
        scope_key = str(Path.cwd().resolve())
        projects = data.setdefault("projects", {})
        project = projects.setdefault(scope_key, {})
        servers = project.setdefault("mcpServers", {})

    # 3. Build entry
    entry = {"command": _posix(exe_path), "args": [], "env": {}}

    # 4. Upsert
    changed = _upsert_servers_entry(servers, MCP_SERVER_NAME, entry)

    # 5. Write back
    if changed:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8"
        )

    return changed


# ===================================================================
# PG06: Codex MCP configuration injection
# ===================================================================


def upsert_codex_mcp(config_file: Path, exe_path: Path) -> bool:
    """Upsert question-tracker into ``~/.codex/config.toml``.

    Uses regex-based text replacement to avoid TOML parsing.
    Returns ``True`` when the file was modified.
    """
    if config_file.exists():
        content = config_file.read_text("utf-8")
    else:
        content = ""

    section_name = f"[mcp_servers.{MCP_SERVER_NAME}]"
    target_block = (
        f"{section_name}\n"
        f'command = "{_posix(exe_path)}"\n'
        "args = []\n"
    )

    # Check if section already exists.
    # Match the section header followed by lines that do NOT start with '['.
    # A TOML section header always has '[' at column 0.
    pattern = re.compile(
        r"\n?" + re.escape(section_name) + r"(?:\n(?!\[)[^\n]*)*",
        re.DOTALL,
    )

    match = pattern.search(content)
    if match:
        existing = match.group(0)
        # Extract existing command value
        cmd_match = re.search(r'command\s*=\s*"([^"]*)"', existing)
        if cmd_match and cmd_match.group(1) == _posix(exe_path):
            return False  # Already up to date
        # Replace the section: remove the old one, insert the new one
        before = content[: match.start()]
        after = content[match.end() :]
        # Normalise whitespace around the replacement
        before = before.rstrip()
        after = after.lstrip()
        new_content = before + "\n" + target_block + "\n" + after
    else:
        # Append
        if content and not content.endswith("\n"):
            content += "\n"
        new_content = content + "\n" + target_block
        if new_content.startswith("\n"):
            new_content = new_content[1:]

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(new_content, "utf-8")
    return True


# ===================================================================
# PG07: OpenCode MCP configuration injection
# ===================================================================


def upsert_opencode_mcp(config_file: Path, exe_path: Path) -> bool:
    """Upsert question-tracker into ``opencode.json``.

    Returns ``True`` when the file was modified.
    Returns ``False`` when the file cannot be parsed (never overwrites).
    """
    try:
        data = _load_json_config(config_file)
    except MCPConfigError as e:
        logger.warning("%s", e)
        return False

    entry = {
        "type": "local",
        "command": [_posix(exe_path)],
        "enabled": True,
        "environment": {},
    }
    mcp = data.setdefault("mcp", {})

    if MCP_SERVER_NAME in mcp:
        existing = mcp[MCP_SERVER_NAME]
        existing_cmd = existing.get("command", [])
        if isinstance(existing_cmd, list) and len(existing_cmd) > 0:
            if existing_cmd[0] == _posix(exe_path):
                return False  # Already up to date

    mcp[MCP_SERVER_NAME] = entry

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8"
    )
    return True


# ===================================================================
# PG08: Chrys MCP configuration injection
# ===================================================================


def upsert_chrys_mcp(config_file: Path, exe_path: Path) -> bool:
    """Upsert question-tracker into ``~/.chrys/agents/Code.yaml``.

    Uses pure-text line-based manipulation: never parses the whole YAML,
    never rewrites the whole file.  Preserves user comments, blank lines,
    and formatting.

    When *config_file* does not exist, copies the chrys built-in Code.yaml
    as the base before injecting the MCP entry.

    Returns ``True`` when the file was modified.
    """
    config_file.parent.mkdir(parents=True, exist_ok=True)

    if not config_file.exists():
        return _create_chrys_from_builtin(config_file, exe_path)

    return _upsert_chrys_mcp_in_text(config_file, exe_path)


# -------------------------------------------------------------------
# Chrys helpers
# -------------------------------------------------------------------

_QUESTION_TRACKER_ENTRY_LINES = [
    "    - name: question-tracker\n",
    "      transport: stdio\n",
    "      command: {exe_path}\n",
    "      args: []\n",
    "      enabled: true\n",
]


def _format_mcp_entry(exe_path: Path) -> list[str]:
    """Format question-tracker MCP entry lines with correct indentation."""
    return [line.format(exe_path=_posix(exe_path)) for line in _QUESTION_TRACKER_ENTRY_LINES]


def _create_chrys_from_builtin(config_file: Path, exe_path: Path) -> bool:
    """Copy the chrys built-in Code.yaml, then inject the MCP entry."""
    try:
        from importlib.resources import files  # noqa: PLC0415
    except ImportError:
        # Python < 3.9 fallback
        return _create_chrys_minimal(config_file, exe_path)

    try:
        builtin_text = (
            files(CHRYS_BUILTIN_PACKAGE).joinpath(f"{CHRYS_PROFILE_NAME}.yaml").read_text("utf-8")
        )
    except Exception:
        logger.warning(
            "Cannot read chrys built-in Code.yaml; creating minimal profile."
        )
        return _create_chrys_minimal(config_file, exe_path)

    # Inject MCP entry into the builtin text
    mcp_lines = _format_mcp_entry(exe_path)
    result = _append_mcp_to_yaml_text(builtin_text, mcp_lines, _posix(exe_path))
    config_file.write_text(result, "utf-8")
    return True


def _create_chrys_minimal(config_file: Path, exe_path: Path) -> bool:
    """Create a minimal Code.yaml when the builtin template is unavailable."""
    mcp_lines = _format_mcp_entry(exe_path)
    lines = [
        f"name: {CHRYS_PROFILE_NAME}\n",
        "tools:\n",
        "  mcp:\n",
    ]
    lines.extend(mcp_lines)
    config_file.write_text("".join(lines), "utf-8")
    return True


def _append_mcp_to_yaml_text(text: str, mcp_lines: list[str], exe_path: str = "") -> str:
    """Append MCP entry to existing YAML text by locating the right position.

    *exe_path* is used only for the "already up-to-date" comparison.
    """
    lines = text.splitlines(keepends=True)

    # Locate tools section and mcp list
    tools_idx = _find_yaml_key(lines, "tools:", top_level=True)
    if tools_idx is None:
        # No tools section → append at end
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("tools:\n")
        lines.append("  mcp:\n")
        lines.extend(mcp_lines)
        return "".join(lines)

    mcp_idx = _find_yaml_key(lines, "mcp:", parent_key="tools:", parent_idx=tools_idx)
    if mcp_idx is None:
        # tools exists but no mcp → insert mcp after tools' other children
        # Find the last child of tools (next line at same or less indent after tools)
        insert_at = _find_section_end(lines, tools_idx, base_indent=0)
        indent = "  "
        lines.insert(insert_at, f"{indent}mcp:\n")
        for mcp_line in mcp_lines:
            insert_at += 1
            lines.insert(insert_at, f"{indent}{mcp_line}")
        return "".join(lines)

    # mcp exists → upsert or append question-tracker entry
    existing_idx = _find_mcp_entry(lines, mcp_idx, MCP_SERVER_NAME)
    if existing_idx is not None:
        # Check command
        existing_lines = _get_mcp_entry_lines(lines, existing_idx, mcp_idx)
        existing_command = _extract_yaml_field(existing_lines, "command")
        if existing_command is not None and _posix(existing_command) == _posix(exe_path):
            return text  # Already up to date
        # Replace
        entry_end = _find_mcp_entry_end(lines, existing_idx, mcp_idx)
        lines[existing_idx:entry_end] = mcp_lines
        return "".join(lines)

    # Insert at end of mcp list
    insert_at = _find_mcp_list_end(lines, mcp_idx)
    lines[insert_at:insert_at] = mcp_lines
    return "".join(lines)


def _find_yaml_key(
    lines: list[str],
    key: str,
    *,
    top_level: bool = False,
    parent_key: str | None = None,
    parent_idx: int | None = None,
) -> int | None:
    """Find line index of a YAML key.

    If *top_level*, looks for a line with zero indent starting with *key*.
    If *parent_key* and *parent_idx* are given, searches within that section.
    """
    if top_level:
        pattern = re.compile(rf"^{re.escape(key)}\s*$")
        parent_indent = 0
        start = 0
    elif parent_key is not None and parent_idx is not None:
        parent_line = lines[parent_idx]
        parent_indent = len(parent_line) - len(parent_line.lstrip())
        pattern = re.compile(rf"^{' ' * (parent_indent + 2)}{re.escape(key)}\s*$")
        start = parent_idx + 1
    else:
        return None

    for i in range(start, len(lines)):
        if pattern.match(lines[i]):
            return i
        # Stop if we encounter a top-level key (same indent as parent)
        if not top_level and i > start:
            stripped = lines[i].rstrip("\n").rstrip("\r")
            if stripped and not stripped.startswith(" ") and not stripped.startswith("\t"):
                # Back to top-level — mcp is not in this tools section
                # (tools might have no children at all)
                if (
                    stripped.endswith(":")
                    and len(stripped) - len(stripped.lstrip()) <= parent_indent
                ):
                    return None

    return None


def _find_section_end(lines: list[str], section_idx: int, base_indent: int) -> int:
    """Find the line index after the last child of a YAML section."""
    i = section_idx + 1
    while i < len(lines):
        stripped = lines[i].rstrip("\n").rstrip("\r")
        if stripped and not stripped.startswith(" ") and not stripped.startswith("\t"):
            return i
        i += 1
    return i


def _find_mcp_entry(
    lines: list[str], mcp_idx: int, server_name: str
) -> int | None:
    """Find the line index of ``- name: <server_name>`` within the mcp list."""
    mcp_line = lines[mcp_idx]
    mcp_indent = len(mcp_line) - len(mcp_line.lstrip())
    item_indent = mcp_indent + 2  # list items are indented 2 more than "mcp:"
    pattern = re.compile(
        rf"^{' ' * item_indent}-\s+name:\s+{re.escape(server_name)}\s*$"
    )

    for i in range(mcp_idx + 1, len(lines)):
        if pattern.match(lines[i]):
            return i
        # If we find a line at mcp_indent level that is NOT a list item, we've
        # left the mcp section
        stripped = lines[i].rstrip("\n").rstrip("\r")
        if stripped and not stripped.startswith(" " * (item_indent + 1)):
            line_indent = len(lines[i]) - len(lines[i].lstrip())
            if line_indent <= mcp_indent:
                return None
    return None


def _get_mcp_entry_lines(
    lines: list[str], entry_idx: int, mcp_idx: int
) -> list[str]:
    """Return all lines belonging to a single MCP entry."""
    mcp_line = lines[mcp_idx]
    mcp_indent = len(mcp_line) - len(mcp_line.lstrip())
    item_indent = mcp_indent + 2
    field_indent = item_indent + 2

    result = [lines[entry_idx]]
    for i in range(entry_idx + 1, len(lines)):
        stripped = lines[i].rstrip("\n").rstrip("\r")
        if not stripped:
            continue
        line_indent = len(lines[i]) - len(lines[i].lstrip())
        if line_indent <= item_indent and lines[i].lstrip().startswith("- "):
            break  # Next list item
        if line_indent <= mcp_indent:
            break  # Left mcp section
        result.append(lines[i])
    return result


def _find_mcp_entry_end(
    lines: list[str], entry_idx: int, mcp_idx: int
) -> int:
    """Find the exclusive end index of an MCP entry's lines."""
    mcp_line = lines[mcp_idx]
    mcp_indent = len(mcp_line) - len(mcp_line.lstrip())
    item_indent = mcp_indent + 2

    for i in range(entry_idx + 1, len(lines)):
        stripped = lines[i].rstrip("\n").rstrip("\r")
        if not stripped:
            continue
        line_indent = len(lines[i]) - len(lines[i].lstrip())
        if line_indent <= item_indent and lines[i].lstrip().startswith("- "):
            return i
        if line_indent <= mcp_indent:
            return i
    return len(lines)


def _find_mcp_list_end(lines: list[str], mcp_idx: int) -> int:
    """Find the line index where a new MCP entry should be inserted."""
    mcp_line = lines[mcp_idx]
    mcp_indent = len(mcp_line) - len(mcp_line.lstrip())

    for i in range(mcp_idx + 1, len(lines)):
        stripped = lines[i].rstrip("\n").rstrip("\r")
        if stripped and not stripped.startswith(" " * (mcp_indent + 1)):
            line_indent = len(lines[i]) - len(lines[i].lstrip())
            if line_indent <= mcp_indent:
                return i
    return len(lines)


def _extract_yaml_field(entry_lines: list[str], field: str) -> str | None:
    """Extract a field value from MCP entry lines."""
    pattern = re.compile(rf"^\s*{re.escape(field)}:\s*(.+)\s*$")
    for line in entry_lines:
        m = pattern.match(line)
        if m:
            return m.group(1).strip()
    return None


def _upsert_chrys_mcp_in_text(config_file: Path, exe_path: Path) -> bool:
    """Upsert MCP entry in an existing Code.yaml (pure-text)."""
    text = config_file.read_text("utf-8")
    result = _append_mcp_to_yaml_text(text, _format_mcp_entry(exe_path), _posix(exe_path))
    if result == text:
        return False
    config_file.write_text(result, "utf-8")
    return True


# ===================================================================
# PG09: Orchestration entry point
# ===================================================================


def _ensure_mcp_config(skills_root: Path) -> None:
    """Auto-detect agent type and inject MCP configuration.

    Called by ``update.py`` during the transaction swap phase.
    Silently skips when the agent cannot be identified or the MCP binary
    is not available.  Raises ``MCPConfigError`` on file write failures.
    """
    target = detect_target(skills_root)
    if target is None:
        return

    platform = detect_platform()
    exe_path = resolve_mcp_exe(skills_root, platform)
    if exe_path is None:
        return

    config_file = resolve_config_path(target, skills_root)
    if config_file is None:
        return

    if target == "claude":
        upsert_claude_mcp(config_file, exe_path, skills_root)
    elif target == "codex":
        upsert_codex_mcp(config_file, exe_path)
    elif target == "opencode":
        upsert_opencode_mcp(config_file, exe_path)
    elif target == "chrys":
        upsert_chrys_mcp(config_file, exe_path)


# ===================================================================
# Helper
# ===================================================================


def _upsert_servers_entry(
    servers: dict, name: str, entry: dict
) -> bool:
    """Upsert *entry* into *servers* dict under *name*.  Returns True if changed."""
    if name in servers:
        existing = servers[name]
        if _posix(str(existing.get("command", ""))) == _posix(entry["command"]):
            return False  # Already up to date
    servers[name] = entry
    return True
