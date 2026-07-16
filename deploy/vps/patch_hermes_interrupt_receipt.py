#!/usr/bin/env python3
"""Apply JarHert's narrow post-tool interruption recovery to Hermes source.

Hermes correctly cancels stale turns when a newer user message arrives.  A
Codex transport can also raise ``InterruptedError`` immediately after an MCP
action has succeeded, without a newer message.  That used to hide the only
human-readable result.  This guarded source patch preserves latest-request-
wins while returning a deterministic receipt for a completed *current-turn*
action plan.
"""

from __future__ import annotations

import argparse
from pathlib import Path


_OLD = '''                else:
                    final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''

_NEW = '''                else:
                    # A fresh user message is a real latest-request-wins interrupt.
                    # With no new message, Codex can occasionally interrupt after
                    # a native action plan has already completed.  Keep that result
                    # visible instead of silently dropping the only receipt.
                    _current_turn_tool_messages = []
                    for _message in reversed(messages):
                        if isinstance(_message, dict) and _message.get("role") == "user":
                            break
                        _current_turn_tool_messages.append(_message)
                    _current_turn_tool_text = json.dumps(
                        _current_turn_tool_messages, ensure_ascii=False, default=str
                    )
                    _completed_native_plan = (
                        '"status": "succeeded"' in _current_turn_tool_text
                        and '"actions":' in _current_turn_tool_text
                    )
                    if not agent._interrupt_message and _completed_native_plan:
                        final_response = "Готово: подтверждённый план выполнен."
                        interrupted = False
                    else:
                        final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''

_PREVIOUS_NEW = _NEW.replace(
    '''                        '"status": "succeeded"' in _current_turn_tool_text
                        and '"actions":' in _current_turn_tool_text
''',
    '''                        "action_plan" in _current_turn_tool_text
                        and '"status": "succeeded"' in _current_turn_tool_text
''',
)


def patch_source(source: str) -> str:
    """Return a patched source or fail closed when Hermes changed upstream."""
    if _NEW in source:
        return source
    if source.count(_PREVIOUS_NEW) == 1:
        return source.replace(_PREVIOUS_NEW, _NEW, 1)
    if source.count(_OLD) != 1:
        raise RuntimeError("Hermes interrupt receipt patch target was not found exactly once.")
    return source.replace(_OLD, _NEW, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    path = args.source.expanduser().resolve()
    source = path.read_text(encoding="utf-8")
    patched = patch_source(source)
    if patched != source:
        path.write_text(patched, encoding="utf-8")
        print("interrupt_receipt_patch=applied")
    else:
        print("interrupt_receipt_patch=already_applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
