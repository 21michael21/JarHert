#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABEL="com.jarhert.limits-push"
TEMPLATE="$ROOT/deploy/mac/${LABEL}.plist.template"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
ENV_FILE="$HOME/.config/jarhert/limits-push.env"
LOG_DIR="$HOME/.cache/jarhert"

if [[ ! -f "$ENV_FILE" ]] || ! grep -q '^JARHERT_LIMITS_INGEST_TOKEN=.' "$ENV_FILE"; then
  echo "Нет токена для ingest." >&2
  echo "Создай файл:  install -m 600 /dev/null '$ENV_FILE'" >&2
  echo "и впиши строку: JARHERT_LIMITS_INGEST_TOKEN=<тот же токен, что на VPS>" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
sed -e "s|@REPO_ROOT@|$ROOT|g" -e "s|@HOME_DIR@|$HOME|g" "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl print "gui/$(id -u)/$LABEL" >/dev/null

echo "Установлено: $TARGET"
echo "Интервал: 300 сек (RunAtLoad), лог: $LOG_DIR/limits-push.log"
