from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "deploy" / "vps" / "patch_hermes_forward_origin.py"

ADAPTER_FRAGMENT = '''        return MessageEvent(
            text=message.text or "",
            message_type=msg_type,
        )
'''


def _apply(target: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(PATCH), str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_forward_origin_patch_prefixes_forwarded_messages(tmp_path: Path) -> None:
    target = tmp_path / "adapter.py"
    target.write_text(ADAPTER_FRAGMENT, encoding="utf-8")

    assert _apply(target) == "forward_origin_patch=applied"
    # Second run is a no-op.
    assert _apply(target) == "forward_origin_patch=already_applied"

    updated = target.read_text(encoding="utf-8")
    assert 'event_text = f"[Переслано из: {forward_label}]\\n{event_text}"' in updated
    assert "def _jarhert_forward_origin_label" in updated


def test_forward_origin_patch_fails_closed_on_unknown_revision(tmp_path: Path) -> None:
    target = tmp_path / "adapter.py"
    target.write_text("return MessageEvent(text=msg.text)\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(PATCH), str(target)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Unsupported Hermes adapter revision" in result.stderr


def test_forward_origin_label_covers_channel_chat_user_and_hidden() -> None:
    namespace: dict[str, object] = {}
    helper_source = PATCH.read_text(encoding="utf-8")
    helper = helper_source.split("HELPER = '''", 1)[1].split("'''", 1)[0]
    exec(helper, namespace)  # noqa: S102 - isolated helper test.
    label = namespace["_jarhert_forward_origin_label"]

    class Obj:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    channel = Obj(forward_origin=Obj(sender_chat=None, chat=Obj(title="Канал", username="kanal"), sender_user=None))
    assert label(channel) == "Канал (@kanal)"

    user = Obj(
        forward_origin=Obj(sender_chat=None, chat=None, sender_user=Obj(first_name="Иван", last_name=None, username="ivan")),
        forward_from_chat=None,
    )
    assert label(user) == "Иван (@ivan)"

    hidden = Obj(forward_origin=Obj(sender_chat=None, chat=None, sender_user=None, sender_user_name="Секрет"))
    assert label(hidden) == "Секрет (скрытый автор)"

    plain = Obj(forward_origin=None, forward_from_chat=None, forward_from=None)
    assert label(plain) == ""
