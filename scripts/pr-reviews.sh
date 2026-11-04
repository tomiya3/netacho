#!/usr/bin/env bash
# 開いている PR の、まだ確かめていない指摘を出す。行コメント、PR 全体のコメント、レビューの本文の 3 か所を見る。
#   scripts/pr-reviews.sh         未確認の指摘を PR ごとに出す
#   scripts/pr-reviews.sh --mark  直前に出した所まで読んだ、と印を付ける（.git の中。コミットされない）
# CI と claude-review が緑でも指摘は付く。緑かどうかではなく、指摘を読む
# 取得に失敗した PR があれば非 0 で終わり、印を付けられない（読めていない物を既読にしない）
set -uo pipefail
seen="$(git rev-parse --git-common-dir)/netacho-reviews-seen"
pending="$seen.pending"
if [ "${1:-}" = --mark ]; then
  [ -f "$pending" ] || { echo "先に scripts/pr-reviews.sh を最後まで成功させる" >&2; exit 1; }
  mv "$pending" "$seen"
  echo "読んだ印を付けた（$(cat "$seen") まで）"
  exit 0
fi
rm -f "$pending"
repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner) || exit 1
numbers=$(gh pr list --state open --limit 100 --json number --jq '.[].number') || exit 1
since=$(cat "$seen" 2>/dev/null || echo 1970-01-01T00:00:00Z)
now=$(date -u +%FT%TZ)  # 読み始めの時刻を印にする（読んでいる間に付いた物を既読にしない）
total=0
failed=0
for n in $numbers; do
  checks=$(gh pr checks "$n" 2>/dev/null | LC_ALL=C cut -f1,2 | tr '\n' ' ')
  if ! inline=$(gh api "repos/$repo/pulls/$n/comments" --paginate --jq \
        '.[] | {at: .created_at, who: .user.login, where: "\(.path):\(.line // .original_line)", body}') ||
     ! general=$(gh api "repos/$repo/issues/$n/comments" --paginate --jq \
        '.[] | {at: .created_at, who: .user.login, where: "PR 全体", body}') ||
     ! reviews=$(gh api "repos/$repo/pulls/$n/reviews" --paginate --jq \
        '.[] | select(.body != "") | {at: .submitted_at, who: .user.login, where: "レビューの本文", body}'); then
    echo "== PR #${n}：取得失敗"
    failed=1
    continue
  fi
  rows=$(printf '%s\n%s\n%s\n' "$inline" "$general" "$reviews" |
    jq -s --arg since "$since" 'map(select(.at > $since)) | sort_by(.at)')
  count=$(jq length <<<"$rows")
  total=$((total + count))
  echo "== PR #${n}：未確認 ${count} 件 / ${checks}"
  jq -r '.[] | "[\(.at)] \(.who) \(.where)\n\(.body)\n"' <<<"$rows"
done
echo "未確認の指摘 合計 ${total} 件"
[ "$failed" = 0 ] || exit 1
echo "$now" > "$pending"
