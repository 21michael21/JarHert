from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_STYLE_PATH = Path(__file__).with_name("prompts") / "jarhert_communication_style.md"
PREFERENCE_OVERLAYS = {
    "short": (
        "Краткость: явный формат и длина из запроса важнее стилевых правил. Если просят коротко или "
        "одним предложением, дай 1–2 предложения; жёсткий предел — 240 символов. В остальных случаях "
        "по умолчанию используй не больше 5 коротких предложений; жёсткий предел — 500 символов. Не добавляй служебные "
        "заголовки «Вывод», «Действие» и «Факт/предположение» без прямой необходимости."
    ),
    "concise": (
        "Краткость: явный формат и длина из запроса важнее стилевых правил. Если просят коротко или "
        "одним предложением, дай 1–2 предложения; жёсткий предел — 240 символов. В остальных случаях "
        "по умолчанию используй не больше 5 коротких предложений; жёсткий предел — 500 символов. Не добавляй служебные "
        "заголовки «Вывод», «Действие» и «Факт/предположение» без прямой необходимости."
    ),
    "detailed": (
        "Детальный режим: дай достаточно обоснования для решения, но не повторяй мысль другими словами. "
        "Для сложной темы отдели вывод, причины и следующий шаг."
    ),
}


@dataclass(frozen=True)
class ResponseBudget:
    max_chars: int
    max_output_tokens: int


@dataclass(frozen=True)
class CommunicationStyleGuide:
    prompt: str
    version: str
    enabled: bool = True

    def render(self, preference: str) -> str:
        if not self.enabled or not self.prompt.strip():
            return ""
        overlay = PREFERENCE_OVERLAYS.get((preference or "").strip().lower(), PREFERENCE_OVERLAYS["concise"])
        return f"{self.prompt.strip()}\n\n{overlay}"

    def budget(self, user_prompt: str, preference: str, *, max_chars: int) -> ResponseBudget:
        if not self.enabled:
            return ResponseBudget(max_chars=max_chars, max_output_tokens=600)
        normalized_preference = (preference or "").strip().lower()
        if normalized_preference == "detailed":
            return ResponseBudget(max_chars=max_chars, max_output_tokens=600)
        if re.search(r"\b(?:коротко|кратко|одним предложением|в одном предложении)\b", user_prompt.lower()):
            return ResponseBudget(max_chars=min(max_chars, 360), max_output_tokens=100)
        return ResponseBudget(max_chars=min(max_chars, 700), max_output_tokens=180)


def load_communication_style(*, enabled: bool, path: str = "") -> CommunicationStyleGuide:
    if not enabled:
        return CommunicationStyleGuide("", version="disabled", enabled=False)
    prompt_path = Path(path).expanduser() if path.strip() else DEFAULT_STYLE_PATH
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Communication style prompt is empty: {prompt_path}")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    return CommunicationStyleGuide(prompt, version=f"style-{digest}")


def constrain_response_length(text: str, *, max_chars: int) -> str:
    clean = (text or "").strip()
    if len(clean) <= max_chars:
        return clean
    window = clean[:max_chars]
    minimum_boundary = min(80, max_chars // 3)
    sentence_boundary = max(window.rfind(marker) for marker in ".!?")
    if sentence_boundary >= minimum_boundary:
        return window[: sentence_boundary + 1].strip()
    line_boundary = window.rfind("\n", minimum_boundary, max_chars + 1)
    if line_boundary >= minimum_boundary:
        return window[:line_boundary].strip()
    word_boundary = window.rfind(" ", minimum_boundary, max_chars)
    if word_boundary < minimum_boundary:
        word_boundary = max_chars - 1
    return window[:word_boundary].rstrip(" ,;:-") + "…"
