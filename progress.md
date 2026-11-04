# 開発の記録

## 2026-11-05 🖥 直接
- やったこと：repo の土台を作った（プラン、役の呼び出し、費用の管理、前処理、フック、スキル、CI）
- 次：schema の契約、抜粋で一巡
- やったこと：§8 手順 10 の PR（#2）を通した。Issue #1 → フックの拒否（印なしの PR 作成）→ reviewer → 報告書 → PR → 自動レビュー 1 周
- 分かったこと：main の CI の赤は bash 5.2 の `patsub_replacement` と、Linux の `/tmp` の読み替えが原因。macOS の bash 3.2 では出ない。Docker のデーモンが動いていないので bash 5 は CI でしか確かめられない
- やったこと：T2（#3）。schemas 14 個、`check_refs`、ダミーの一式
- plan からの変更：`scene.koza_id` を足した（二席目の区間 ID と混ざらないように）。`hanashi.json` と `kata.json` は .gitignore の対象なので schema は `book.json` にまとめた
