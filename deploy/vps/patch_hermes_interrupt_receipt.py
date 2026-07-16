#!/usr/bin/env python3
"""Install JarHert's narrow receipt recovery into a compatible Hermes source.

Some Codex transports interrupt immediately after an inline-confirmed MCP tool
has completed. Hermes then sends its internal interruption status instead of a
human receipt. The patch recovers only a successful action plan from the tail
of the *current* user turn. It fails closed when the upstream shape changes.
"""

from __future__ import annotations

import argparse
from pathlib import Path


_OLD_BRANCH = '''                else:
                    final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''

_PARTIAL_PREFIX = '''                _partial = agent._strip_think_blocks(
                    getattr(agent, "_current_streamed_assistant_text", "") or ""
                ).strip()
                if _partial:
                    messages.append({"role": "assistant", "content": _partial})
                    final_response = _partial
'''

_RECEIPT_MARKER = "# JarHert durable confirmation receipt."

_RECEIPT_PREFIX = '''                _partial = agent._strip_think_blocks(
                    getattr(agent, "_current_streamed_assistant_text", "") or ""
                ).strip()
                # JarHert durable confirmation receipt. The Codex transport
                # can put its own interruption text into the streamed buffer;
                # inspect the current turn before treating that text as a reply.
                _receipt_messages = []
                for _turn_messages in (messages, getattr(agent, "_session_messages", [])):
                    _turn_tail = []
                    for _message in reversed(_turn_messages or []):
                        if isinstance(_message, dict) and _message.get("role") == "user":
                            _receipt_messages.extend(_turn_tail)
                            break
                        _turn_tail.append(_message)
                _receipt_text = json.dumps(_receipt_messages, ensure_ascii=False, default=str).replace("\\\\", "")
                _completed_native_plan = (
                    '"status": "succeeded"' in _receipt_text
                    and '"actions":' in _receipt_text
                )
                if _completed_native_plan:
                    final_response = "Готово: подтверждённый план выполнен."
                    messages.append({"role": "assistant", "content": final_response})
                    agent._persist_session(messages, conversation_history)
                    return {
                        "final_response": final_response,
                        "messages": messages,
                        "api_calls": api_call_count,
                        "completed": True,
                        "interrupted": False,
                    }
                if _partial:
                    messages.append({"role": "assistant", "content": _partial})
                    final_response = _partial
'''

_SESSION_BRANCH = '''                else:
                    # A fresh user message is a real latest-request-wins interrupt.
                    # With no new message, Codex can occasionally interrupt after
                    # a native action plan has already completed. Keep that result
                    # visible instead of silently dropping the only receipt.
                    _current_turn_tool_messages = []
                    for _turn_messages in (messages, getattr(agent, "_session_messages", [])):
                        _turn_tail = []
                        for _message in reversed(_turn_messages or []):
                            if isinstance(_message, dict) and _message.get("role") == "user":
                                _current_turn_tool_messages.extend(_turn_tail)
                                break
                            _turn_tail.append(_message)
                    _current_turn_tool_text = json.dumps(
                        _current_turn_tool_messages, ensure_ascii=False, default=str
                    )
                    _current_turn_normalized_text = _current_turn_tool_text.replace("\\\\", "")
                    _completed_native_plan = (
                        '"status": "succeeded"' in _current_turn_normalized_text
                        and '"actions":' in _current_turn_normalized_text
                    )
                    if _completed_native_plan:
                        final_response = "Готово: подтверждённый план выполнен."
                        messages.append({"role": "assistant", "content": final_response})
                        agent._persist_session(messages, conversation_history)
                        return {
                            "final_response": final_response,
                            "messages": messages,
                            "api_calls": api_call_count,
                            "completed": True,
                            "interrupted": False,
                        }
                    else:
                        final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''

_PREVIOUS_BRANCH = _SESSION_BRANCH.replace(
    '''                    for _turn_messages in (messages, getattr(agent, "_session_messages", [])):
                        _turn_tail = []
                        for _message in reversed(_turn_messages or []):
                            if isinstance(_message, dict) and _message.get("role") == "user":
                                _current_turn_tool_messages.extend(_turn_tail)
                                break
                            _turn_tail.append(_message)
''',
    '''                    for _message in reversed(messages):
                        if isinstance(_message, dict) and _message.get("role") == "user":
                            break
                        _current_turn_tool_messages.append(_message)
''',
)

_DEBUG_SESSION_BRANCH = _SESSION_BRANCH.replace(
    "# a native action plan has already completed. Keep that result",
    "# a native action plan has already completed.  Keep that result",
).replace(
    '''                    _current_turn_tool_messages = []
                    for _turn_messages in (messages, getattr(agent, "_session_messages", [])):
''',
    '''                    _current_turn_tool_messages = []
                    # A Codex transport interruption can arrive after the
                    # tool result is flushed from ``messages`` into the
                    # agent's in-memory session. Inspect only the tail after
                    # the latest user message in either current-turn view;
                    # old completed plans must not create a new receipt.
                    for _turn_messages in (messages, getattr(agent, "_session_messages", [])):
''',
).replace(
    '''                    _completed_native_plan = (
                        '"status": "succeeded"' in _current_turn_normalized_text
                        and '"actions":' in _current_turn_normalized_text
                    )
                    if _completed_native_plan:
''',
    '''                    _completed_native_plan = (
                        '"status": "succeeded"' in _current_turn_normalized_text
                        and '"actions":' in _current_turn_normalized_text
                    )
                    logger.warning(
                        "JarHert receipt probe: current=%d session=%d status=%s actions=%s",
                        len(messages or []),
                        len(getattr(agent, "_session_messages", []) or []),
                        '"status": "succeeded"' in _current_turn_normalized_text,
                        '"actions":' in _current_turn_normalized_text,
                    )
                    # An inline confirmation may look like an interrupt to the
                    # transport, but the durable action has already happened.
                    # Deliver its receipt even when the callback set an interrupt
                    # marker; ordinary interrupted generations still stay hidden.
                    if _completed_native_plan:
''',
)


def _legacy_variants(branch: str) -> tuple[str, ...]:
    """Return exact older receipt branches that were shipped during recovery."""
    returnless = branch.replace(
        '''                    if _completed_native_plan:
                        final_response = "Готово: подтверждённый план выполнен."
                        messages.append({"role": "assistant", "content": final_response})
                        agent._persist_session(messages, conversation_history)
                        return {
                            "final_response": final_response,
                            "messages": messages,
                            "api_calls": api_call_count,
                            "completed": True,
                            "interrupted": False,
                        }
''',
        '''                    if _completed_native_plan:
                        final_response = "Готово: подтверждённый план выполнен."
                        interrupted = False
''',
    )
    guarded = returnless.replace(
        "                    if _completed_native_plan:\n",
        "                    if not agent._interrupt_message and _completed_native_plan:\n",
        1,
    )
    direct = guarded.replace(
        '''                    _current_turn_normalized_text = _current_turn_tool_text.replace("\\\\", "")
                    _completed_native_plan = (
                        '"status": "succeeded"' in _current_turn_normalized_text
                        and '"actions":' in _current_turn_normalized_text
                    )
''',
        '''                    _completed_native_plan = (
                        '"status": "succeeded"' in _current_turn_tool_text
                        and '"actions":' in _current_turn_tool_text
                    )
''',
    )
    action_plan = direct.replace(
        '''                        '"status": "succeeded"' in _current_turn_tool_text
                        and '"actions":' in _current_turn_tool_text
''',
        '''                        "action_plan" in _current_turn_tool_text
                        and '"status": "succeeded"' in _current_turn_tool_text
''',
    )
    return branch, returnless, guarded, direct, action_plan


_KNOWN_BRANCHES = (
    _OLD_BRANCH,
    *_legacy_variants(_PREVIOUS_BRANCH),
    *_legacy_variants(_SESSION_BRANCH),
    *_legacy_variants(_DEBUG_SESSION_BRANCH),
)


def patch_source(source: str) -> str:
    """Return a patched source, or fail closed for an unknown Hermes revision."""
    if _RECEIPT_MARKER in source:
        return source
    for branch in _KNOWN_BRANCHES:
        target = _PARTIAL_PREFIX + branch
        if source.count(target) == 1:
            return source.replace(target, _RECEIPT_PREFIX + _SESSION_BRANCH, 1)
    # Unit fixtures and narrowly vendored upstream fragments sometimes contain
    # only the branch. They still receive the session-tail receipt recovery;
    # a full Hermes source always matches the stricter prefix form above.
    for branch in _KNOWN_BRANCHES:
        if source.count(branch) == 1:
            return source.replace(branch, _SESSION_BRANCH, 1)
    raise RuntimeError("Hermes interrupt receipt patch target was not found exactly once.")


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
