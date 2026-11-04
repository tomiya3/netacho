#!/usr/bin/env bash
# Stop：progress.md に今日の見出しが無ければ /log を促す
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0
today=$(date +%Y-%m-%d)
if ! grep -q "^## $today" progress.md 2>/dev/null; then
  jq -n --arg d "$today" '{systemMessage: ("progress.md に今日（" + $d + "）の記録がありません。区切りで /log を")}'
fi
exit 0
