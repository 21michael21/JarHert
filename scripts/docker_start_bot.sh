#!/usr/bin/env bash
set -euo pipefail

cd /app
exec python -m gateway_bot.telegram_app
