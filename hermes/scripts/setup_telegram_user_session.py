from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", Path(__file__).resolve().parents[1])).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.telegram_text_export import telegram_export_settings


def _load_profile_env() -> None:
    path = hermes_home / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main() -> int:
    _load_profile_env()
    api_id, api_hash, session_path, _output_dir = telegram_export_settings()
    try:
        from telethon import TelegramClient
    except ModuleNotFoundError:
        print("Telethon не установлен в окружении Hermes.")
        return 2
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.start()
    try:
        authorized = await client.is_user_authorized()
    finally:
        await client.disconnect()
    session_file = Path(str(session_path) + ".session")
    if session_file.exists():
        # A Telethon session grants full account access: owner-only, like .env.
        os.chmod(session_file, 0o600)
    print("MTProto session authorized." if authorized else "MTProto session is not authorized.")
    return 0 if authorized else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
