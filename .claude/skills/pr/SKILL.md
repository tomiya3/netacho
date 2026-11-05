---
name: pr
description: テスト → 文脈なしレビュー → 報告書 → PR 作成。/pr。PR を作るときは必ずこの手順（夜間にまとめて作るときも）。ブランチ名の Issue 番号を Closes に付ける
---

PR は必ずこの手順で作る。複数の PR をまとめて作るとき（夜間の作業など）も、PR ごとに全部の手順を通す。

手順：
1. `make lint && make test` を通す。落ちていたら直してから進む
2. **文脈なしレビュー（省略しない）**：Agent ツールで `subagent_type: reviewer` を起動し、差分を見せる
   - 渡すのは「差分の取り方」「この PR のねらい」「特に見てほしい点」だけ。自分の結論や「問題ないはず」は渡さない（答えを誘導しない）
   - 差分の取り方は base との三点 diff：`git diff origin/<base>...HEAD`（積んだ PR は base が前の PR のブランチ）。作業ツリーが別ブランチなら `git show <branch>:<path>` で読むよう書く
   - 戻った指摘は 1 件ずつ「直す」か「見送る（理由）」を決める。直したら `make lint && make test` をやり直す
   - reviewer を呼ぶと、PostToolUse フック（`.claude/hooks/on-reviewer.sh`）がブランチに印を付ける。印が無いと PR 作成はフック（`guard-bash.sh`）で止まる。印は「呼んだ」ことしか示さないので、指摘への対応を飛ばさない。レビュー後に大きく変えたとき（機能の追加・設計の変更・フックや workflow の作り直し）は、もう一度呼ぶ
   - 大きな変更なら、追加で `/code-review` を回してもよい（文脈なしレビューの代わりにはしない）
3. `/report` で報告書を作る（`docs/reports/YYYY-MM-DD-<slug>.md`）。実行結果・数字・スクショを含める
4. コミットが未了なら CLAUDE.md の規約（`<type>: 日本語要約`）でコミット。`git push -u origin <branch>`（main には push しない）
5. **Closes の番号を検証する**：`gh issue view <N> --json title,state` で、番号が**この作業の Issue**を指すことを確認。PR 番号や別の Issue なら Closes を書かない（間違った Closes はマージ時に無関係の Issue を閉じる）
6. `gh pr create` で PR を作る。本文の型：

```
## 目的
（Issue の目的を1〜2行）

## 変更
- 

## 確認
- [ ] make lint / make test
- [ ] 文脈なしレビュー（reviewer）済み：指摘 N 件、反映 M・見送り K（見送りは理由つきで下に）
- 報告書：docs/reports/YYYY-MM-DD-<slug>.md

Closes #<N>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

7. **自動レビューの指摘を読む（省略しない）**：チェックの完了を待ち（`gh pr checks <N> --watch`。登録前で失敗したら少し待ってやり直す）、`scripts/pr-reviews.sh` で指摘を読む
   - チェックが緑でも指摘は付く。緑かどうかではなく、指摘の本文を読む。スクリプトは行コメント、PR 全体のコメント、レビューの本文を出す
   - 1 件ずつ「反映」「見送り（理由）」「Issue へ」を決める。反映したら push して、もう一度待って読む
   - 3 周まで反映し、4 周目以降は Issue に回す（高の指摘も Issue に書いて報告する）。機能凍結の後は待たない
   - 周回は、その PR に指摘が付いた回数で数える。下の PR を取り込んだ push で付いた分も数える
   - 周回と判断を PR 本文の確認欄に残す。全部読んで判断したら `scripts/pr-reviews.sh --mark`。チェックの失敗や取得の失敗で読めなかった物があれば `--mark` しない
   - 積んだ PR は下の PR から順に直して、上へ取り込む
8. PR の URL と、見送った指摘を報告する。マージはユーザーが行う（Squash）
9. 最後に `/log`

## 積んだ PR（別の PR の上に作るとき）
- base を前の PR のブランチにし、本文に「#N の上に積んでいる。先に #N をマージしてから base を main に」と書く
- Squash merge と相性が悪い（前の PR が別コミットになり、次の PR で衝突する）。前がマージされたら `git merge main` を取り込んで push してから次をマージする
