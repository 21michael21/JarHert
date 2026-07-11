from __future__ import annotations

import argparse
from pathlib import Path


def merge_native_tool_allowlist(source: Path, target: Path) -> list[str]:
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    target_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    source_tools, _ = _native_tool_block(source_lines)
    target_tools, insertion_index = _native_tool_block(target_lines)
    missing = [tool for tool in source_tools if tool not in target_tools]
    if missing:
        target_lines[insertion_index:insertion_index] = [f"        - {tool}\n" for tool in missing]
        target.write_text("".join(target_lines), encoding="utf-8")
    return missing


def _native_tool_block(lines: list[str]) -> tuple[list[str], int]:
    in_server = False
    in_tools = False
    in_include = False
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
            continue
        if in_include:
            if line.startswith("        - "):
                values.append(line.removeprefix("        - ").strip())
                continue
            return values, index
    if in_include:
        return values, len(lines)
    raise ValueError("jarhert_native MCP tool include block was not found")


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge missing JarHert native MCP tools into a live Hermes config.")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    merged = merge_native_tool_allowlist(args.source, args.target)
    print(f"merged_native_tools={len(merged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
