from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_STYLE_PATH = Path(__file__).with_name("prompts") / "jarhert_communication_style.md"
PROFILE_MAX_CHARS_PATTERN = re.compile(r"^<!--\s*jarhert-style\s+max_response_chars=(\d+)\s*-->\s*")
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
    "expressive": (
        "Экспрессивный режим: используй живой разговорный тон, иронию и уместный русский мат, если это помогает "
        "точнее передать мысль или пользователь сам просит такой стиль. Не ругайся ради шума. Не добавляй мат в "
        "сообщения другим людям, деловые тексты, подтверждения tools и инструкции, если пользователь прямо этого "
        "не попросил. Без оскорблений, травли, слюров, угроз и сексуализированной грубости. Ответ всё равно должен "
        "быть коротким, полезным и конкретным."
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
    max_response_chars: int | None = None

    def render(self, preference: str, *, policy_instruction: str = "") -> str:
        if not self.enabled or not self.prompt.strip():
            return ""
        overlay = PREFERENCE_OVERLAYS.get((preference or "").strip().lower(), PREFERENCE_OVERLAYS["concise"])
        parts = [self.prompt.strip(), overlay]
        if policy_instruction.strip():
            parts.append(policy_instruction.strip())
        return "\n\n".join(parts)

    def budget(self, user_prompt: str, preference: str, *, max_chars: int) -> ResponseBudget:
        if not self.enabled:
            return ResponseBudget(max_chars=max_chars, max_output_tokens=600)
        normalized_preference = (preference or "").strip().lower()
        if normalized_preference == "detailed":
            return ResponseBudget(max_chars=max_chars, max_output_tokens=600)
        if re.search(r"\b(?:коротко|кратко|одним предложением|в одном предложении)\b", user_prompt.lower()):
            short_limit = 240 if self.max_response_chars is not None else 360
            return ResponseBudget(max_chars=min(max_chars, self.max_response_chars or short_limit, short_limit), max_output_tokens=100)
        return ResponseBudget(max_chars=min(max_chars, self.max_response_chars or 700), max_output_tokens=180)


def load_communication_style(*, enabled: bool, path: str = "") -> CommunicationStyleGuide:
    if not enabled:
        return CommunicationStyleGuide("", version="disabled", enabled=False)
    prompt_path = Path(path).expanduser() if path.strip() else DEFAULT_STYLE_PATH
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    max_response_chars = None
    match = PROFILE_MAX_CHARS_PATTERN.match(prompt)
    if match:
        max_response_chars = int(match.group(1))
        if not 160 <= max_response_chars <= 2_500:
            raise ValueError(f"Communication style max_response_chars is outside 160..2500: {max_response_chars}")
        prompt = prompt[match.end() :].strip()
    if not prompt:
        raise ValueError(f"Communication style prompt is empty: {prompt_path}")
    digest_source = f"max_response_chars={max_response_chars or ''}\n{prompt}"
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    return CommunicationStyleGuide(prompt, version=f"style-{digest}", max_response_chars=max_response_chars)


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
