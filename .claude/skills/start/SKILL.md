---
name: start
description: Issue 番号を受けて、文脈を読み、作業ブランチを切る。/start <issue番号>。番号なしなら Issue を作ってから切る
---

引数は Issue 番号（例 `/start 3`）。番号がなければ、ユーザーの説明から Issue を作る。

## Issue を作るとき（必ず確認する）
```
gh issue create --title "..." --body "..."
```
- `-q` などの無いフラグを付けない。**出力に Issue の URL が出たことを確認し、その番号を使う**。URL が出なければ作れていない
- 本文は「目的 / 受け入れ条件 / 対象ファイル」
- 作った直後に `gh issue view <N> --json title,state` で、番号が自分の Issue を指していることを確認する（番号は PR と共用なので、ずれると別のものを指す）

## 手順
1. `CLAUDE.md`、`progress.md` の末尾エントリ、`gh issue view <N>` を読む
2. `git switch main && git pull --ff-only`
3. ブランチ名は `<N>-<短い英語スラッグ>`（例 `3-pack-tts-sec`）で `git switch -c`
4. Issue の受け入れ条件を箇条書きで復唱してから作業に入る

1セッション＝1Issue＝1PR。Issue に書いていないことを足さない。
