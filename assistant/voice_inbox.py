from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from assistant.action_schema import ActionType, PlannedAction
from assistant.natural_router import route_natural_text


_DATE_RE = re.compile(r"\b(?P<value>сегодня|завтра|послезавтра|через\s+неделю)\b", re.IGNORECASE)
_EVENT_RE = re.compile(r"\b(?:встреч[а-я]*|созвон|колл|звонок|демо)\b", re.IGNORECASE)
_TIME_RE = re.compile(r"\b(?:в|на|к)\s*(?P<hour>[01]?\d|2[0-3])(?::(?P<minute>[0-5]\d))?\b", re.IGNORECASE)
_SCHEDULE_RE = re.compile(r"\b(?:отправ(?:ь|ить)|пришли|скинь)\b[^.!?]{0,120}\bрасписани[ея]\b", re.IGNORECASE)
_FILM_RE = re.compile(r"\b(?:оцени|посоветуй|как\s+думаешь)\b[^.!?]{0,120}\b(?:фильм|кино)\s+[«\"']?(?P<title>[^«»\"'?.!]+)", re.IGNORECASE)
_CLAUSE_SPLIT_RE = re.compile(
    r"(?:[.!?;]+\s*|\b(?:а\s+ещё|ещё|потом|также)\s+|\bи\s+(?=(?:напомни|сохрани|запиши|создай|добавь|поставь)))",
    re.IGNORECASE,
)
_LEADING_FILLER_RE = re.compile(r"^(?:бро\s*,?\s*)?(?:слушай\s*,?\s*)?(?:смотри\s*,?\s*)?(?:потом|ещё|а\s+ещё|и)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class VoiceInboxParse:
    actions: tuple[PlannedAction, ...]
    followups: tuple[str, ...]

    @property
    def handled(self) -> bool:
        return bool(self.actions or self.followups)


def parse_voice_inbox(text: str, *, today: date | None = None) -> VoiceInboxParse:
    """Extract clear, low-risk voice items without waiting for an LLM round-trip."""
    value = " ".join((text or "").strip().split())
    if not value:
        return VoiceInboxParse((), ())

    actions: list[PlannedAction] = []
    followups: list[str] = []
    current_day = today or date.today()
    for sentence in _sentences(value):
        event = _calendar_event(sentence, today=current_day)
        if event is not None:
            _append_action(actions, event)

        schedule_followup = _schedule_followup(sentence)
        if schedule_followup:
            _append_followup(followups, schedule_followup)

        film_followup = _film_followup(sentence)
        if film_followup:
            _append_followup(followups, film_followup)

        for clause in _clauses(sentence):
            if _calendar_event(clause, today=current_day) is not None:
                continue
            if _schedule_followup(clause) or _film_followup(clause):
                continue
            route = route_natural_text(clause)
            for action in route.actions:
                _append_action(actions, action)

    return VoiceInboxParse(tuple(actions), tuple(followups))


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|;\s*", text) if part.strip()]


def _clauses(sentence: str) -> list[str]:
    result: list[str] = []
    for item in _CLAUSE_SPLIT_RE.split(sentence):
        clean = _LEADING_FILLER_RE.sub("", item.strip())
        if clean:
            result.append(clean)
    return result


def _calendar_event(sentence: str, *, today: date) -> PlannedAction | None:
    date_match = _DATE_RE.search(sentence)
    event_match = _EVENT_RE.search(sentence)
    if date_match is None or event_match is None:
        return None
    time_matches = list(_TIME_RE.finditer(sentence))
    if not time_matches:
        return None
    time_match = next((item for item in time_matches if item.start() >= event_match.end()), time_matches[0])

    if time_match.start() >= event_match.end():
        title_source = sentence[event_match.start() : time_match.start()]
    else:
        title_source = sentence[event_match.start() :]
    title = _event_title(title_source)
    start_day = _relative_day(date_match.group("value"), today=today)
    clock = _clock(time_match)
    starts_at = datetime.fromisoformat(f"{start_day} {clock}")
    ends_at = starts_at + timedelta(minutes=60)
    start = starts_at.strftime("%Y-%m-%d %H:%M")
    end = ends_at.strftime("%Y-%m-%d %H:%M")
    return PlannedAction(
        ActionType.CALENDAR_CREATE,
        payload={"title": title, "start": start, "end": end},
        confidence=0.98,
        reason="voice_inbox_v2",
    )


def _event_title(value: str) -> str:
    clean = re.sub(r"\b(?:в|на|к)\s*$", "", value, flags=re.IGNORECASE)
    clean = " ".join(clean.strip(" .,!?:;—-").split())
    if not clean:
        return "Встреча"
    return clean[:1].upper() + clean[1:]


def _relative_day(value: str, *, today: date) -> str:
    normalized = " ".join(value.lower().split())
    if normalized == "сегодня":
        return today.isoformat()
    if normalized == "завтра":
        return (today + timedelta(days=1)).isoformat()
    if normalized == "послезавтра":
        return (today + timedelta(days=2)).isoformat()
    return (today + timedelta(days=7)).isoformat()


def _clock(match: re.Match[str]) -> str:
    return f"{int(match.group('hour')):02d}:{match.group('minute') or '00'}"


def _schedule_followup(sentence: str) -> str | None:
    if _SCHEDULE_RE.search(sentence) is None:
        return None
    when = "на завтра" if re.search(r"\bзавтра\b", sentence, re.IGNORECASE) else ""
    return f"Кому отправить расписание {when}?".replace("  ", " ").rstrip()


def _film_followup(sentence: str) -> str | None:
    match = _FILM_RE.search(sentence)
    if match is None:
        return None
    title = " ".join(match.group("title").strip(" ,:—-").split())
    if not title:
        return "Скинь год или ссылку на фильм: так я не перепутаю его с другим названием."
    return f"По фильму «{title}» скинь год или ссылку: под этим названием есть разные фильмы."


def _append_action(items: list[PlannedAction], action: PlannedAction) -> None:
    key = (action.type.value, tuple(sorted(action.payload.items())))
    if any((item.type.value, tuple(sorted(item.payload.items()))) == key for item in items):
        return
    items.append(action)


def _append_followup(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)
