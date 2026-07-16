from __future__ import annotations

import argparse
import re
from pathlib import Path


MANAGED_NATIVE_ENV_KEYS = (
    "HERMES_NATIVE_SEND_COMMAND",
    "HERMES_OWNER_TELEGRAM_CHAT_ID",
    "HERMES_TOOL_BUNDLES",
)


def merge_native_tool_allowlist(source: Path, target: Path) -> list[str]:
    """Merge the versioned native-tool allowlist without changing live choices."""
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    source_tools, _, _ = _native_tool_block(source_lines)
    target_tools, insertion_index, target_prefix = _native_tool_block(target_lines)
    missing = [tool for tool in source_tools if tool not in target_tools]
    if missing:
        target_lines[insertion_index:insertion_index] = [f"{target_prefix}- {tool}\n" for tool in missing]
        target.write_text("".join(target_lines), encoding="utf-8")
    return missing


def merge_profile_config(source: Path, target: Path) -> list[str]:
    """Merge only profile-owned defaults that are safe to add to a live config.

    The live profile owns model, provider, credentials and any explicit STT
    choice.  Versioned JarHert config may add native tools and, when the live
    profile has no voice section yet, the free local STT baseline.
    """
    try:
        merged = [f"tool:{tool}" for tool in merge_native_tool_allowlist(source, target)]
    except ValueError:
        merged = []
    try:
        merged.extend(f"env:{key}" for key in merge_native_env(source, target, MANAGED_NATIVE_ENV_KEYS))
    except ValueError:
        pass
    if merge_optional_readonly_mcp(source, target, server_name="github_readonly"):
        merged.append("mcp:github_readonly")
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    stt_block = _top_level_block(source_lines, "stt")
    if stt_block and _top_level_block(target_lines, "stt") is None:
        separator = "" if not target_lines or target_lines[-1].endswith("\n\n") else "\n"
        target.write_text("".join(target_lines) + separator + "".join(stt_block), encoding="utf-8")
        merged.append("stt")
        target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

    # Telegram is JarHert's personal inbox, not an operator console. Add the
    # versioned quiet display profile only when the live profile has no display
    # configuration at all; an existing live block remains the owner's choice.
    display_block = _top_level_block(source_lines, "display")
    if display_block and _top_level_block(target_lines, "display") is None:
        separator = "" if not target_lines or target_lines[-1].endswith("\n\n") else "\n"
        target.write_text("".join(target_lines) + separator + "".join(display_block), encoding="utf-8")
        merged.append("display")
    elif _migrate_legacy_busy_input_mode(source, target):
        # JarHert previously shipped queue mode, which lets stale Telegram
        # requests finish after the user has changed their mind. Migrate only
        # that historical value; steer/interrupt remain an explicit live choice.
        merged.append("display.busy_input_mode")
    if merge_top_level_scalar(source, target, key="context_file_max_chars"):
        merged.append("context_file_max_chars")
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    compression_block = _top_level_block(source_lines, "compression")
    if compression_block and _top_level_block(target_lines, "compression") is None:
        separator = "" if not target_lines or target_lines[-1].endswith("\n\n") else "\n"
        target.write_text("".join(target_lines) + separator + "".join(compression_block), encoding="utf-8")
        merged.append("compression")
    return merged


def merge_optional_readonly_mcp(source: Path, target: Path, *, server_name: str) -> bool:
    """Add one disabled, version-owned optional MCP block without changing live model choices."""
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    source_block = _mcp_server_block(source_lines, server_name)
    if source_block is None or _mcp_server_block(target_lines, server_name) is not None:
        return False
    insertion_index = _mcp_servers_end(target_lines)
    if insertion_index is None:
        separator = "" if not target_lines or target_lines[-1].endswith("\n\n") else "\n"
        target.write_text("".join(target_lines) + separator + "mcp_servers:\n" + "".join(source_block), encoding="utf-8")
        return True
    target_lines[insertion_index:insertion_index] = source_block
    target.write_text("".join(target_lines), encoding="utf-8")
    return True


def merge_native_env(source: Path, target: Path, keys: tuple[str, ...]) -> list[str]:
    """Add only explicitly managed native environment pass-through keys."""
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    source_values, _, _ = _native_env_block(source_lines)
    target_values, insertion_index, item_prefix = _native_env_block(target_lines)
    missing = [key for key in keys if key in source_values and key not in target_values]
    if missing:
        additions = []
        for key in missing:
            source_line = source_values[key]
            additions.append(
                source_line if source_line.startswith(item_prefix) else f"{item_prefix}{source_line.lstrip()}"
            )
        target_lines[insertion_index:insertion_index] = additions
        target.write_text("".join(target_lines), encoding="utf-8")
    return missing


def merge_top_level_scalar(source: Path, target: Path, *, key: str) -> bool:
    """Add a safe profile default only when the live profile has no value."""
    prefix = f"{key}:"
    source_line = next((line for line in source.read_text(encoding="utf-8").splitlines() if line.startswith(prefix)), None)
    if source_line is None:
        return False
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    if any(line.startswith(prefix) for line in target_lines):
        return False
    separator = "" if not target_lines or target_lines[-1].endswith("\n\n") else "\n"
    target.write_text("".join(target_lines) + separator + f"{source_line}\n", encoding="utf-8")
    return True


def _native_tool_block(lines: list[str]) -> tuple[list[str], int, str]:
    in_server = False
    in_tools = False
    in_include = False
    include_indent = 0
    item_prefix = "        "
    values: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if line.startswith("  jarhert_native:"):
            in_server = True
            continue
        if in_server and line.startswith("  ") and not line.startswith("    ") and stripped:
            break
        if in_server and line.startswith("    tools:"):
            in_tools = True
            continue
        if in_tools and line.startswith("      include:"):
            in_include = True
            include_indent = len(line) - len(line.lstrip())
            item_prefix = " " * (include_indent + 2)
            continue
        if in_include:
            stripped_indent = len(line) - len(line.lstrip())
            if line.lstrip().startswith("- ") and stripped_indent >= include_indent:
                item_prefix = line[:stripped_indent]
                values.append(line.lstrip().removeprefix("- ").strip())
                continue
            if not stripped:
                continue
            return values, index, item_prefix
    if in_include:
        return values, len(lines), item_prefix
    raise ValueError("jarhert_native MCP tool include block was not found")


def _native_env_block(lines: list[str]) -> tuple[dict[str, str], int, str]:
    in_server = False
    in_env = False
    values: dict[str, str] = {}
    item_prefix = "      "
    for index, line in enumerate(lines):
        stripped = line.strip()
        if line.startswith("  jarhert_native:"):
            in_server = True
            continue
        if in_server and line.startswith("  ") and not line.startswith("    ") and stripped:
            break
        if in_server and line.startswith("    env:"):
            in_env = True
            continue
        if in_env:
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= 4:
                return values, index, item_prefix
            if not stripped:
                continue
            key, separator, _value = line.strip().partition(":")
            if separator:
                item_prefix = line[:indent]
                values[key] = line
    if in_env:
        return values, len(lines), item_prefix
    raise ValueError("jarhert_native MCP env block was not found")


def _mcp_server_block(lines: list[str], server_name: str) -> list[str] | None:
    in_servers = False
    start: int | None = None
    marker = f"  {server_name}:"
    for index, line in enumerate(lines):
        if line.startswith("mcp_servers:"):
            in_servers = True
            continue
        if not in_servers:
            continue
        if line and not line.startswith((" ", "\t", "\n", "\r")):
            break
        if line.startswith(marker):
            start = index
            continue
        if start is not None and line.startswith("  ") and not line.startswith("    ") and line.strip():
            return lines[start:index]
    return lines[start:] if start is not None else None


def _mcp_servers_end(lines: list[str]) -> int | None:
    in_servers = False
    for index, line in enumerate(lines):
        if line.startswith("mcp_servers:"):
            in_servers = True
            continue
        if in_servers and line and not line.startswith((" ", "\t", "\n", "\r")):
            return index
    return len(lines) if in_servers else None


def _top_level_block(lines: list[str], key: str) -> list[str] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            start = index
            continue
        if start is not None and line and not line.startswith((" ", "\t", "\n", "\r")):
            return lines[start:index]
    return lines[start:] if start is not None else None


def _migrate_legacy_busy_input_mode(source: Path, target: Path) -> bool:
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    source_block = _top_level_block(source_lines, "display") or []
    if not any(re.match(r"^\s*busy_input_mode:\s*interrupt\s*$", line.rstrip()) for line in source_block):
        return False
    target_block = _top_level_block(target_lines, "display")
    if target_block is None:
        return False
    for index, line in enumerate(target_lines):
        match = re.match(r"^(\s*busy_input_mode:\s*)queue(\s*(?:#.*)?\n?)$", line)
        if match:
            target_lines[index] = f"{match.group(1)}interrupt{match.group(2)}"
            target.write_text("".join(target_lines), encoding="utf-8")
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge missing JarHert native MCP tools into a live Hermes config.")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    merged = merge_profile_config(args.source, args.target)
    print(f"merged_profile_defaults={len(merged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
