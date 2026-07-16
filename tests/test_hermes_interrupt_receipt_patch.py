from __future__ import annotations

from deploy.vps.patch_hermes_interrupt_receipt import patch_source


def test_patch_adds_receipt_only_for_a_completed_current_turn_plan() -> None:
    source = '''            except InterruptedError:
                if thinking_spinner:
                    thinking_spinner.stop("")
                    thinking_spinner = None
                if agent.thinking_callback:
                    agent.thinking_callback("")
                api_elapsed = time.time() - api_start_time
                agent._vprint(f"{agent.log_prefix}⚡ Interrupted during API call.", force=True)
                interrupted = True
                # Preserve any assistant text already streamed to the user
                _partial = agent._strip_think_blocks(
                    getattr(agent, "_current_streamed_assistant_text", "") or ""
                ).strip()
                if _partial:
                    messages.append({"role": "assistant", "content": _partial})
                    final_response = _partial
                else:
                    final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''

    patched = patch_source(source)

    assert '"Готово: подтверждённый план выполнен."' in patched
    assert "_current_turn_tool_text" in patched
    assert '"status": "succeeded"' in patched
    assert '"actions":' in patched
    assert "_current_turn_normalized_text" in patched
    assert '"action_plan" in _current_turn_tool_text' not in patched
    assert "if _completed_native_plan:" in patched
    assert patch_source(patched) == patched


def test_patch_upgrades_the_previous_receipt_heuristic() -> None:
    source = '''                else:
                    final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''
    latest = patch_source(source)
    previous = latest.replace(
        '''                        '"status": "succeeded"' in _current_turn_tool_text
                        and '"actions":' in _current_turn_tool_text
''',
        '''                        "action_plan" in _current_turn_tool_text
                        and '"status": "succeeded"' in _current_turn_tool_text
''',
    )

    assert patch_source(previous) == latest


def test_patch_upgrades_the_deployed_callback_guard() -> None:
    source = '''                else:
                    final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''
    latest = patch_source(source)
    previous = latest.replace(
        '''                    # An inline confirmation may look like an interrupt to the
                    # transport, but the durable action has already happened.
                    # Deliver its receipt even when the callback set an interrupt
                    # marker; ordinary interrupted generations still stay hidden.
                    if _completed_native_plan:
''',
        """                    if not agent._interrupt_message and _completed_native_plan:
""",
    )

    assert patch_source(previous) == latest


def test_patch_upgrades_the_deployed_direct_json_heuristic() -> None:
    source = '''                else:
                    final_response = f"{INTERRUPT_WAITING_FOR_MODEL_PREFIX}{api_elapsed:.1f}s elapsed)."
                agent._persist_session(messages, conversation_history)
                break
'''
    latest = patch_source(source)
    previous = latest.replace(
        '''                    # An inline confirmation may look like an interrupt to the
                    # transport, but the durable action has already happened.
                    # Deliver its receipt even when the callback set an interrupt
                    # marker; ordinary interrupted generations still stay hidden.
                    if _completed_native_plan:
''',
        """                    if not agent._interrupt_message and _completed_native_plan:
""",
    ).replace(
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

    assert patch_source(previous) == latest


def test_patch_rejects_an_unknown_upstream_shape() -> None:
    try:
        patch_source("def unrelated():\n    pass\n")
    except RuntimeError as error:
        assert "target" in str(error).lower()
    else:  # pragma: no cover - assertion guard
        raise AssertionError("The patch must fail closed for an unknown Hermes version.")
