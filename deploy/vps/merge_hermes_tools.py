from __future__ import annotations

import argparse
from pathlib import Path


MANAGED_NATIVE_ENV_KEYS = ("HERMES_NATIVE_SEND_COMMAND",)


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
    return merged


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


def _top_level_block(lines: list[str], key: str) -> list[str] | None:
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            start = index
            continue
        if start is not None and line and not line.startswith((" ", "\t", "\n", "\r")):
            return lines[start:index]
    return lines[start:] if start is not None else None


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
