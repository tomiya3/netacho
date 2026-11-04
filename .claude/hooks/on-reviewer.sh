#!/usr/bin/env bash
# PostToolUse(Agent|Task)：文脈なしの reviewer サブエージェントを呼んだら、今のブランチに「レビューを呼んだ印」を付ける。
# guard-bash.sh が PR 作成の前に見る。印には呼んだ時点の HEAD を書き、それが今の HEAD の祖先のときだけ有効
# （同じ名前のブランチを別の履歴で作り直したら無効）。印は .git の中に置く（コミットされない）
command -v jq >/dev/null || exit 0
[ "$(jq -r '.tool_input.subagent_type // empty')" = reviewer ] || exit 0
branch=$(git branch --show-current 2>/dev/null)
[ -n "$branch" ] || exit 0
dir="$(git rev-parse --git-common-dir)/netacho-reviewed"
mkdir -p "$dir"
echo "$(date -u +%FT%TZ) $(git rev-parse HEAD)" > "$dir/${branch//\//_}"
exit 0
