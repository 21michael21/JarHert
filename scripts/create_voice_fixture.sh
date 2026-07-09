#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p dev/voice_fixtures

TEXT="${1:-напомни через десять минут проверить JarHert}"
AIFF="dev/voice_fixtures/live_e2e.aiff"
OUT="dev/voice_fixtures/live_e2e.m4a"

if ! command -v say >/dev/null 2>&1; then
  echo "missing macOS say command; record a short .oga/.m4a manually into dev/voice_fixtures/" >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "missing ffmpeg; install it or record a short .oga/.m4a manually into dev/voice_fixtures/" >&2
  exit 1
fi

say -o "$AIFF" "$TEXT"
ffmpeg -hide_banner -loglevel error -y -i "$AIFF" -vn -c:a aac -b:a 64k "$OUT"
rm -f "$AIFF"
echo "$OUT"
