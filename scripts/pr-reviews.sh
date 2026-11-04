#!/usr/bin/env bash
# 開いている PR の、まだ確かめていない自動レビューの指摘を出す。
#   scripts/pr-reviews.sh         未確認の指摘を PR ごとに出す
#   scripts/pr-reviews.sh --mark  ここまで確かめた、と印を付ける（.git の中。コミットされない）
# CI と claude-review が緑でも指摘は付く。緑かどうかではなく、指摘を読む
set -euo pipefail
repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
seen="$(git rev-parse --git-common-dir)/netacho-reviews-seen"
since=$(cat "$seen" 2>/dev/null || echo 1970-01-01T00:00:00Z)
if [ "${1:-}" = --mark ]; then
  date -u +%FT%TZ > "$seen"
  echo "確かめた印を付けた"
  exit 0
fi
total=0
for n in $(gh pr list --state open --limit 100 --json number --jq '.[].number'); do
  checks=$(gh pr checks "$n" 2>/dev/null | cut -f1,2 | tr '\n' ' ' || true)
  body=$(gh api "repos/$repo/pulls/$n/comments" --paginate --jq \
    ".[] | select(.created_at > \"$since\") | \"[\(.created_at)] \(.path):\(.line)\n\(.body)\n\"")
  count=$(gh api "repos/$repo/pulls/$n/comments" --paginate --jq \
    "[.[] | select(.created_at > \"$since\")] | length" | awk '{s+=$1} END {print s+0}')
  total=$((total + count))
  echo "== PR #${n}：未確認 ${count} 件 / ${checks}"
  [ -z "$body" ] || echo "$body"
done
echo "未確認の指摘 合計 ${total} 件"
