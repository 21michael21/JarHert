from __future__ import annotations

import argparse
from pathlib import Path


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
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    stt_block = _top_level_block(source_lines, "stt")
    if stt_block and _top_level_block(target_lines, "stt") is None:
        separator = "" if not target_lines or target_lines[-1].endswith("\n\n") else "\n"
        target.write_text("".join(target_lines) + separator + "".join(stt_block), encoding="utf-8")
        merged.append("stt")
    return merged


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
