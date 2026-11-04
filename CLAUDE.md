# 根多帳（Netacho）

一席の音声から演者の根多帳を作るツール。演者はそれを見て、次の高座の演出を決める。
文章・コメント・コミットメッセージは日本語。正本のプランは `docs/plan.md`。変えるときは理由を `progress.md` に。

## 動かし方
- `make setup`（ffmpeg + uv sync + git フック）、`make test`、`make lint`
- 秘密は環境変数だけ（`.env.example` 参照）。`.env` を読まない。コード・ログ・JSON に書かない
- 演者のデータは repo の外（`NETACHO_DATA`、既定 `~/netacho-data/`）。読み書きは CLI だけ

## 設計の約束
- LLM の周りは決定的コード。流れ、反応、候補選び、根拠の検証、マージ、札はスクリプト。Claude は読む・文にする・指摘するだけ
- 噺と型は分けて書く。基準は「別の演者がやっても同じか」。型は `heard`（録音で言っている）だけ
- 演者の言葉は書き換えない。演者の項目は再生成で上書きしない
- 札はスクリプトだけで作る（LLM 0 回）。承認済みの `staging` だけを写す
- 役は自分の `drafts/` だけに書く。`hanashi.json` と `kata.json` に書くのはスクリプトだけ。schema 検証は受信直後、保存の前
- 役に見せる物の境界を守る（plan §3）。弟子役は録音を読まない。直すのは構成作家役
- 演者の判断の候補はスクリプトが選ぶ。未検出を「反応なし」と扱わない
- 数字は実測だけ（run.json）。推定を README に書かない
- API は全体 $30、1 回 $5 まで確認なし。費用は `netacho/budget.py` が止める

## 書かないこと
- 演者のデータ（型、ノート、速記、文字起こし、録音）、本名、芸名、師匠の名前
- 所属・会社・社内の事情。個人情報
- テストの素材は自作のダミー（短い架空の噺）だけ

## コミットメッセージ
`<type>: <日本語の要約>` の形（Conventional Commits）。type は次のどれか：
feat（機能追加）/ fix（バグ修正）/ docs（文書だけ）/ style（整形だけ）/ refactor（機能追加でも修正でもない変更）/ perf（性能改善）/ test（テストの追加・修正）/ chore（ビルド・ツール・設定）

## 開発の型
- 1 タスク = 1 Issue = 1 PR。`/start <issue>` → 作業 → `/pr`。Squash merge（題名も上の規約で）
- PR は必ず `/pr` で作る。PR の前に文脈なしの `reviewer` を通す。reviewer を呼ぶとブランチに印が付き、印が無い PR 作成はフックで止まる
- 報告書は `docs/reports/`（`/report`）。小さな修正は Issue と報告書を省いてよい（PR 本文に「省略」と書く）。reviewer は省かない
- PR を作ると Claude Code Review が自動で走る。指摘は 3 周まで反映し、4 周目以降は Issue に回す。機能凍結の後は待たない
- 終わりに `/log`（`progress.md` に追記）
- フック（`.claude/settings.json` → `.claude/hooks/`）：`.env` / `*.key` / `credentials*.json` / `netacho-data/` への書き込みは拒否、消す・書き換える系の Bash は承認を挟む、PR 前レビューの印が無い PR 作成は止める、.py を書いたら ruff が自動で走る、progress.md に今日の記録が無ければ Stop 時に催促
- `.githooks/pre-push`：main への push を止める。`forbidden-words.txt`（repo の外）の語を検査する

## 承認を得てから実行するコマンド
消す・履歴を書き換える・マージする操作は、ユーザーに確かめてから。`.claude/hooks/guard-bash.sh` が承認（ask）を挟む。**正は同ファイルの一覧**で、主なものは：
- ブランチ・タグ・stash・worktree（-f）を消す、リモートのブランチを消す push。force push と main への push は禁止（settings.json の deny とフック）
- `git reset --hard`、`git clean -f`、`git checkout .` / `git restore .` / `-f`、rebase・filter-branch・update-ref -d などの履歴・参照の書き換え
- `rm -r` / `rm -f`（ただし rm だけのコマンドで、Claude の一時フォルダ `/tmp/claude-*` かこの repo の `out/` の下を消すときは承認なし。`out/` そのものと判定しきれない書き方は承認制）、`rmdir`、`find -delete`、`git rm -r`、中身を判定できない `bash -c` / `eval`
- `gh pr merge` / `gh pr close`、Issue を消す（閉じるのは承認なしで可）、repo の設定の変更、release・secret の削除、`gh api` の書き込み（-X、-f、graphql）
- マージはユーザーが「マージして」と言ったときだけ。承認は 1 回ごと（前に承認されたことを次に流用しない）。夜間など無人のときに承認待ちで止まったら、そこで止めて朝に報告する
- フックは文字列の判定なので抜け道はある（既知の穴は同ファイルの冒頭）。フックが止めなくても、この節に当たる操作は確かめてから
