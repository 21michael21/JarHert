from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
if __package__ in {None, ""}:
    sys.path.insert(0, str(hermes_home))
    from native_tools.coding_jobs import NativeCodingJobStore
    from native_tools.mcp_api import personal_os_database_path
    from native_tools.telegram_text_export import read_export_for_analysis, run_telegram_export
else:
    from ..native_tools.coding_jobs import NativeCodingJobStore
    from ..native_tools.mcp_api import personal_os_database_path
    from ..native_tools.telegram_text_export import read_export_for_analysis, run_telegram_export


DIGEST_PROMPT = (
    "Сделай краткий дайджест этого Telegram-канала за сутки: главные темы, важные новости, "
    "что стоит прочитать внимательно. До 10 пунктов по-русски, каждый пункт — одна строка. "
    "Без воды и без пересказа рекламы."
)


def parse_channels(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()][:10]


def queue_channel_digests(
    *,
    channels: list[str],
    limit: int,
    owner_id: int,
    store: NativeCodingJobStore,
    export_runner=run_telegram_export,
    analysis_reader=read_export_for_analysis,
    prompt: str = DIGEST_PROMPT,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Export each channel once and queue one research job per channel.

    Idempotent per channel per UTC day: a timer rerun cannot double the digest.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    queued: list[dict[str, object]] = []
    for channel in channels:
        try:
            exported = export_runner(peer=channel, output_format="txt", limit=limit)
            analysis = analysis_reader(exported.path)
            job = store.enqueue(
                tg_user_id=owner_id,
                mode="research",
                prompt=prompt,
                source_text=analysis.text,
                source_label=f"digest:{channel}:{stamp}",
                idempotency_key=f"digest:{channel}:{stamp}",
                deliver_result=True,
            )
            queued.append({"channel": channel, "job_id": job.id, "status": job.status})
        except Exception as error:
            queued.append({"channel": channel, "error": type(error).__name__})
    return queued


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue daily digests for the owner's Telegram channels.")
    parser.add_argument("--channels", default=os.getenv("TELEGRAM_DIGEST_CHANNELS", ""))
    parser.add_argument("--limit", type=int, default=int(os.getenv("TELEGRAM_DIGEST_LIMIT", "200")))
    args = parser.parse_args()
    channels = parse_channels(args.channels)
    if not channels:
        print("channel_digest=skipped reason=no_channels")
        return 0
    owner_id = int(os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "0") or 0)
    if owner_id <= 0:
        print("channel_digest=failed reason=no_owner_chat_id", file=sys.stderr)
        return 2
    results = queue_channel_digests(
        channels=channels,
        limit=max(50, min(args.limit, 5000)),
        owner_id=owner_id,
        store=NativeCodingJobStore(personal_os_database_path()),
    )
    errors = [item for item in results if "error" in item]
    print(f"channel_digest=queued jobs={len(results) - len(errors)} errors={len(errors)}")
    for item in results:
        print(item)
    return 1 if errors and len(errors) == len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
