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

7. PR の URL と、レビューで見送った指摘を報告する。マージはユーザーが行う（Squash）
8. 最後に `/log`

## 積んだ PR（別の PR の上に作るとき）
- base を前の PR のブランチにし、本文に「#N の上に積んでいる。先に #N をマージしてから base を main に」と書く
- Squash merge と相性が悪い（前の PR が別コミットになり、次の PR で衝突する）。前がマージされたら `git merge main` を取り込んで push してから次をマージする
