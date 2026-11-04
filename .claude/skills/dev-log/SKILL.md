---
name: log
description: 作業の終わりに progress.md へ日誌を追記する。/log で呼ぶ
---

先に `scripts/pr-reviews.sh` で、開いている PR の未確認の指摘が残っていないか確かめる。残っていれば読んで判断し（`/pr` の手順 7）、残した物は日誌の「次」に書く。

`progress.md` の末尾に、次の書式で1エントリ追記する。すでに今日のエントリがあれば、そこに箇条書きを足す。

```
## YYYY-MM-DD <手段タグ>
- やったこと：
- 分かったこと：
- 数字：（あれば。実測だけ）
- 次：
```

手段タグ：📱 Claude Code on the web / 💻 Remote Control / 🖥 直接 / 🤖 GitHub Actions

未完のタスクなら「今の状態・次の一手」も書く。該当する Issue があれば、同じ要約をコメントする（`gh issue comment N --body ...`）。
