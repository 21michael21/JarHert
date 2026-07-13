#!/usr/bin/env bash
set -Eeo pipefail

REMOTE=$JARHERT_VPS
if [[ -z "$REMOTE" ]]; then
  echo "Set JARHERT_VPS=deploy@your-vps-host." >&2
  exit 2
fi
PROFILE_DIR=/home/deploy/.hermes/profiles/jarhert
if [[ -n "$HERMES_PROFILE_DIR" ]]; then
  PROFILE_DIR=$HERMES_PROFILE_DIR
fi

ssh "$REMOTE" "PROFILE_DIR='$PROFILE_DIR' bash -s" <<'REMOTE_SCRIPT'
set -Eeuo pipefail

command -v git >/dev/null || { echo "git is required to install GitHub MCP." >&2; exit 2; }
command -v go >/dev/null || { echo "Go is required to build the official GitHub MCP binary." >&2; exit 2; }

source_dir="$PROFILE_DIR/vendor/github-mcp-server"
binary_dir="$PROFILE_DIR/bin"
binary="$binary_dir/github-mcp-server"
mkdir -p "$PROFILE_DIR/vendor" "$binary_dir"

if [[ -d "$source_dir/.git" ]]; then
  git -C "$source_dir" fetch --depth 1 origin main
  git -C "$source_dir" checkout --detach FETCH_HEAD
else
  rm -rf "$source_dir"
  git clone --depth 1 https://github.com/github/github-mcp-server.git "$source_dir"
fi

go -C "$source_dir" build -o "$binary" ./cmd/github-mcp-server
chmod 700 "$binary"
"$binary" stdio --help >/dev/null

echo "github_mcp_binary_ready=true"
echo "Add a fine-grained read-only token to the profile .env, set GITHUB_MCP_ENABLED=true,"
echo "then explicitly enable github_readonly in config.yaml before restarting Hermes."
REMOTE_SCRIPT
