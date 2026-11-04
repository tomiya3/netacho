#!/usr/bin/env bash
# PostToolUse(Write|Edit)：.py を書いたら ruff check --fix と ruff format をそのファイルに
f=$(jq -r '.tool_input.file_path // empty')
case "$f" in
  *.py)
    cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0
    uv run ruff check --fix -q "$f" >/dev/null 2>&1
    uv run ruff format -q "$f" >/dev/null 2>&1
    ;;
esac
exit 0
