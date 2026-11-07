# 根多帳（Netacho）開発プラン 第8版（11/5 未明）

このプランだけで作業を始められるように書いている。経緯や別の資料を読まなくてよい。
決定事項だけを書き、候補や検討の跡は残さない。

Soegaki は、このハッカソンで先に始めた別プロダクト（非公開）。根多帳とは別の repo で、開発の土台（§8 の表のファイル）だけを流用する。Soegaki の中身は読まなくてよい。

## 1. 前提

- **提出**：正式な締切は 11/8（日）20:00。**11/6（金）24:00 に一式を提出する**。良い素材が撮れたら、締切までに動画だけ差し替える
- **テーマ**：テーマ1「あなたにしかない深さを」。例の「ベテランの仕事のやり方を、本人が現場にいるうちに、次の人が受け継げる形にする」に答える
- **審査基準**：インパクト 30 / デモ 25 / Claude の活用 25 / 深さと実行 20
- **提出物**：3 分動画（YouTube 限定公開）、README（日本語）、public の repo
- **repo**：`~/claude-hackathon/netacho`（github.com/tomiya3/netacho、private、まだ空）。提出の直前に public にする
- **環境**：macOS、Python 3.12、uv、ffmpeg
- **費用**：API は全体 $30、1 回 $5 まで確認なし

### デプロイはまだしない

今は手元の Mac だけで動かす。Web に公開しない。理由：

- 演者のデータを手元から出さない。録音、型、演者の言葉は repo の外に置く約束で、サーバに置くと録音の保管と削除の責任が生じる
- 録音の扱いは演者の許諾が前提で、高座には客の声も入る。公開の場に置く合意を取っていない
- 認証が無い。`netacho serve` のトークンは手元の 1 人を守るだけで、複数の人が使う形には足りない
- 費用の上限は 1 人の手元を前提にしている。利用者ごとの上限と、費用を誰が持つかが決まっていない
- 価値の検証が先。まず 1 人の演者の次の高座で役に立つかを確かめる。Whisper と AST も手元の GPU と CPU で動かしている

デプロイを考えるのは、演者の許諾の形と認証が決まり、複数の演者が使いたいと分かってから。

## 2. どういうツールか

**一席の音声を入れると、演者の根多帳ができる。演者はそれを見て、次の高座の演出を決める。**

- 一行：「落語家が高座を振り返って、磨いた理由を帳面に残すためのツール。」（2026-11-08 に変えた。理由は progress.md）
- 根多帳：寄席の楽屋で、誰がどの噺をやったかを書く帳面
- 主役：演者のノートと型。演者の判断が型に残り、弟子に渡る
- 入力：高座の録音と、手で指定する演目名。台本は要らない
- 芸には触らない。道具は候補と問いを出すだけで、決めるのは演者。絵札は文字だけ

### 見せ場の流れ（3 分動画の軸）

1. 高座の反応の波が出る。ノートが「ここは仕込みがあるのに反応候補が少ない」と候補を出す
2. 演者が見て「ここに解説を入れたい」「ここは枕で説明しよう」と言う
3. 型に一行入る（`edited_by: performer`）
4. 次の高座の開演前の案内（場面順の文字札）に、その解説が出る。LLM は呼ばず、スクリプトだけで反映する
5. 弟子役が型を読むと「なぜここに解説を入れるのか」まで分かる。演者の判断が伝承になる

## 3. エージェント構成

### 考え方

**流れを決めるのはスクリプト。Claude は意味を読む所だけ。決めるのは演者。** 自律的に動くエージェントのループにはしない。尺、反応、根拠の確認は決定的に書けるうえ、演者の言葉を AI に書き換えさせないため。Claude は「読む範囲を意図して絞った役」を別々の文脈で呼ぶ。

| 担当 | やること | やらないこと |
| --- | --- | --- |
| スクリプト | 文字起こし、反応の検出、ノートと札の候補選び、実測、根拠の検証、マージ、札、速記、費用の管理 | 文章の解釈 |
| Claude の役 | 噺・型の抽出、根拠の照合、ノートの文、弟子としての指摘、2 本の対応づけ、笑いの所から聞く所を選ぶ | ノートと札の候補を選ぶ、演者の言葉を変える、型を確定する |
| 演者 | 候補を見て判断する。質問に答える | — |

### 役の一覧

Messages API + structured outputs。役ごとに別の文脈で、1 つずつ順に呼ぶ。自動の再試行はしない。

| 役 | ファイル | モデル | effort | max_tokens | 仕事 |
| --- | --- | --- | --- | --- | --- |
| 構成作家役 | `roles/writer.md` | claude-opus-5-5 | high | 32000 | 文字起こしから噺と型を抜き出す |
| 照合役 | `roles/checker.md` | claude-sonnet-5-5 | medium | 8000 | 項目の主張が引用で言えるかだけ判定する |
| ノート役 | `roles/noter.md` | claude-sonnet-5-5 | medium | 4000 | スクリプトが選んだ候補を、演者向けの短い文にする |
| 弟子役 | `roles/deshi.md` | claude-opus-5-5 | high | 8000 | 噺・型だけを読み、演れない所と本人に聞くべき所を挙げる |
| 語の表役 | `roles/lexicon.md` | claude-opus-5-5 | high | 8000 | 高座全体を読み、聞き違えた固有の語の表を作る |
| 速記役 | `roles/scribe.md` | claude-opus-5-5 | high | 32000 | 文字起こしの聞き違いを直し、話者を付けて清書する |
| 対応づけ役 | `roles/aligner.md` | claude-opus-5-5 | high | 32000 | 同じ演目の 2 本の、どの台詞が同じくだりかを読む |
| 聞き役 | `roles/asker.md` | claude-opus-5-5 | high | 16000 | 笑いの所から聞く所を選び、弟子の口調で問う |
| 読み直し役 | `roles/rechecker.md` | claude-opus-5-5 | high | 8000 | 答えた問いに、帳面だけで答えられるかを読む |

### 見せる物の境界（この構成の要点）

| 役 | 読む物 | 読まない物 | 出す物 |
| --- | --- | --- | --- |
| 構成作家役 | transcript（区間 ID、間の秒数つき）、演目名、既存の噺・型、差し戻し | 反応 | `drafts/writer.json` |
| 照合役 | 項目の文と引用だけ | transcript 全体、演目名 | `drafts/checker.json` |
| ノート役 | 選ばれた候補、前後の引用、場面の尺、同じ演目で答えたか捨てた問い（20 件まで） | 反応の全体、型の全文 | `drafts/noter.json` |
| 弟子役 | 確定した噺・型の `.md` だけ | transcript、音声、反応、差し戻しの履歴 | `review/deshi.json` |
| 語の表役 | transcript の全文、演目の題、人物、演者から聞いた語 | 反応、型 | `drafts/lexicon.json` |
| 速記役 | transcript、演目の題、人物、語の表、演者から聞いた語 | 反応、型 | `drafts/scribe.json` |
| 対応づけ役 | 演目の題と、2 本の清書 | 反応、録音、型 | `drafts/aligner.json` |
| 聞き役 | 笑いのあった所の種類と、2 本の前後の台詞、実測の間、同じ演目で答えたか捨てた問い（20 件まで） | 録音、スコア、型の全文 | `drafts/asker.json` |
| 読み直し役 | 噺・型の `.md` と同じ形の帳面、答えた問いの文。どちらが答えの前の帳面かは知らない | transcript、音声、反応 | `drafts/recheck-before.json`、`drafts/recheck-after.json` |

- ノート役と聞き役に答えと捨てた理由を見せるのは、答えた所を聞き直さないだけでなく、演者の工夫の向きを次の問いに活かすため。未回答の問いは見せない。答えには型の一部や笑いの話が入ることがあるが、演者の言葉として許す。1 つの文は 200 字で切り、切ったと書く。答えの言葉（6 字以上の連続）がそのまま問いやノートに入ったら、スクリプトが落とす。言い換えは見ていない。6 字の一致は、よく使う語で誤って落とすことも、聞き違いで 1 字ずれて素通りすることもある。答えの語のうち見せた台詞にもある連続は、台詞から来た語として数えない。全角と半角はそろえる。仮名の揺れ（ひらがなとカタカナ）はそろえていない。台詞は 1 つずつ見る。落とした数は run.json の leaked（ノートは leaked と leaked_questions）に残し、実データで見て決める
- 構成作家役に反応を見せないのは、結果から型を逆算させないため
- 照合役に演目名を見せないのは、演目の知識で補って通してしまうのを防ぐため
- 弟子役に録音を見せないのは、書かれていない物を補わせないため。読めない所が、そのまま伝承の抜けになる
- 読み直し役には、答えの項目を外した帳面と全部入りの帳面を別の呼び出しで渡す。引用が帳面に無い判定はスクリプトが落として数え、落とした所は「書いていない」と扱わない。答えから読めたと数えるのは、引用が答えから取られ、前の帳面に無いときだけ。答えの前後の結果は `review/recheck.json`、数は run.json。画面は結果を作った高座でだけ出す
- 役は `drafts/` にしか書けない。`hanashi.json` と `kata.json` に書くのはスクリプトだけ

### 流れ

```
録音
 ↓ [script] 前処理：wav、VAD、文字起こし、区間 ID          → transcript.json
 ↓ [script] 反応の候補                                      → reactions.json
 ↓ [Claude] 構成作家役                                      → drafts/writer.json
 ↓ [script] 根拠の検証：ID の実在、範囲、kind の制約。落ちた項目は数える
 ↓ [Claude] 照合役                                          → drafts/checker.json
 ↓ [script] unsupported を保留へ。演者の項目を保ってマージ   → hanashi.json, kata.json
 ↓ [script] .md へ書き出し
 ↓ [Claude] 弟子役（1 周目）                                → review/deshi.json
 ↓ [script] 仕分け：rework は構成作家役へ、ask_performer は質問へ
 ↓ [Claude] 構成作家役（再生成は 1 回まで）→ 検証・照合・マージ
 ↓ [Claude] 弟子役（2 周目。指摘の減りを測る）
 ↓ [script] 回収 × 反応から「薄い」候補を選ぶ                → note_candidates.json
 ↓ [Claude] ノート役                                        → drafts/noter.json
 ↓ [script] ノート、速記                                    → note.md, sokki.md
 ─────── ここで演者が読む ───────
 ↓ [演者]   `netacho decide` / `netacho answer`             → kata.json（edited_by: performer）
 ↓ [script] 案内の札（LLM 呼び出し 0 回）                    → guide/guide.json
 ↓ [script] 視聴画面用に書き出す                             → out/view/
```

### 役の本文の要点

共通（`roles/common.md`）：落語の根多帳を作るパイプラインの一役。担当だけを処理し、指定の JSON だけを返す。根拠の無いことを書かない。知らないことは「不明」とする。

**構成作家役**

- 噺と型の基準は「別の演者がやっても同じか」。場面、人物、筋、サゲは噺。枕の中身、間、この演者だけの言い回しは型
- 見立て・地口・知らない物は、どちらかを判定する
- 全項目に `segs`（区間 ID の始まりと終わり）を付ける。`basis` は `heard`（録音で言っている）か `knowledge`（演目の知識）
- **型は `heard` だけ**。演目の知識で型を書かない。噺の `knowledge` は暫定として扱う
- 仕込みのある kind（見立て、地口、くすぐり、サゲ）は `payoff` に回収の区間を付ける。笑いどころの予測は `くすぐり` として書く
- `上下` は録音から分からないので書かない（演者の追記だけで入る）
- `間` は入力の `pause_after` の秒数を根拠にする。0.5〜5 秒で、根拠の最後の区間の直後にあること。文に書いた秒数は実際と合うこと。5 秒を超える空白は間と呼ばない
- 差し戻しが付いたときは、指摘の箇所だけ足すか直す。演者の項目は入力に含まれるが、変えない

**照合役**

- 項目ごとに `supported`（引用で言える）、`weak`（一部だけ）、`unsupported`（言えない）のどれか。理由を一行
- 照合に回すのは `heard` の項目だけ。`knowledge` は録音に根拠が無いのが定義なので照合せず、暫定と表示して数える
- `間` も照合に回さない。根拠は秒数で引用に無いので、スクリプトが `pause_after` で確かめる
- `間` の文のうち、秒数以外の主張は誰も確かめない。役の本文で、秒数と場所だけを書かせる
- 落語の知識で補わない。引用だけを読む

**ノート役**

- 与えられた候補だけを文にする。足さない、順位を変えない
- 断定しない。「反応候補が少ない」と書く。「受けなかった」「反応なし」と書かない
- 演者への短い文。数字の羅列にしない。直し方は提案しない
- 「予測なしで反応あり」の笑いは、文字からは読めなかったことを言い、演者への問いを 1 つ書く

**弟子役**

- 「明日この噺を高座でやる弟子」として読む
- 指摘は場面を指す。「どこを演ろうとして、何が書いていないから演れないか」を具体的に書く。「もっと詳しく」のような一般論は書かない
- kind は 5 種類：`missing_step`（型が無くて演れない）、`unclear_scene`（どの場面か追えない）、`no_makura`（枕で何を説明するか不明）、`no_sage`（サゲの運びが無い）、`ask_performer`（なぜそう演るかは本人に聞くしかない）
- 直さない。書くのは指摘だけ

### 指摘の行き先

| kind | 行き先 | 上限 |
| --- | --- | --- |
| `missing_step`、`unclear_scene`、`no_makura`、`no_sage` | 構成作家役への差し戻し | 再生成 1 回。残りは「未解決」で残す |
| `ask_performer` | 演者への質問 `questions.json` | 構成作家役には渡さない |
| 場面が実在しない指摘 | スクリプトが落とす | 落とした数を run.json に |

### run.json に残す数字

役ごとのトークン・費用・時間、根拠の検証で落とした数、照合の 3 区分の数、スクリプトで確かめた間の数、弟子役の指摘数（1 周目 → 2 周目）、質問の数、札に出た演者の項目の数。

## 4. スクリプト側の処理

### 前処理

- `ffmpeg -vn -ac 1 -ar 16000` で 16kHz モノラル wav に。`--from` `--to` で抜粋を切り出す
- 文字起こしは Apple の GPU で動く `mlx-whisper`（`mlx-community/whisper-large-v3-turbo`）。使えない環境（CI の Linux など）では faster-whisper の `medium`、`device="cpu"`、`compute_type="int8"`。どちらも `language="ja"`、`condition_on_previous_text=False`、`word_timestamps=True`。実行ごとに結果は少し変わる
- MLX が使えなければ faster-whisper の `medium` に落ちる。どちらの方式でも空の行は除く。`pause_after` は空の行の手前までで測るので、笑いの長さは間に入らない。笑いの直後の間も `pause_after` には残らない。`--terms`（`initial_prompt`）が効くのは最初の窓（約 30 秒）だけ。`condition_on_previous_text=False` では次の窓で捨てられる。語の直しは清書が担う
- VAD はどちらの場合も faster-whisper 同梱の Silero を別に回し、両方残す。Whisper の区間に `seg-0001` と振り、次の区間との隙間を `pause_after` に入れる
- 演目の語を `initial_prompt` に入れられるようにする

### 反応の候補

音量の方式から AST（Audio Spectrogram Transformer）に置き換えた（理由は progress.md）。

1. モデルは `MIT/ast-finetuned-audioset-10-10-0.4593`。窓は 5 秒、2 秒刻み。多ラベルなので sigmoid を取る
2. 笑いは Laughter、Giggle、Chuckle, chortle、Belly laugh の最大。絶対値では判定できない（最大でも約 0.3）ので、同じ高座の最大値の半分以上を山とする（下限 0.08）
3. 拍手は Applause、Clapping の最大が 0.3 以上。Music のスコアに関係なく採用する
4. Music は太鼓でも節回しでも 0.5 前後になるので、はめものの判定には使わない。0.5 以上を音楽の候補として別に残すだけ
5. 切れ目が 1 窓までならつなぐ。強さは山の高さの、同じ高座の中の順位にする
6. `pause_after` が長い区間と、VAD が発話で文字起こしが空の区間は、補助の手がかりとして記録する

数値は暫定。耳で確かめて調整する。自動で読めなければ、耳で聞いて付けた時刻を `netacho mark` で入れ、`method: manual` にして画面に「手で付けた」と出す。

### 「薄い」候補の選び方

- 回収の区間の始まりから、回収の終わりの 6 秒後までの窓を見る
- 窓に候補が無い、または同じ高座の回収の中で強さが下位 4 分の 1 のものを「薄い」とする
- 窓に弱い山があれば「薄い」としない。候補は 6 件まで、サゲ・地口・見立てを先に出し、全数は残す
- 見せ場に使う候補は原音で確かめる
- 未検出を「反応なし」と扱わない

### 笑いどころの予測と実際

- 構成作家役は反応を見ずに回収を書く。スクリプトが AST の笑いと重ねて、的中（強い山）、弱い山（0.08 以上）、空振り、予測なしで反応ありに分ける
- 主な数字は笑いごと・山ごとに数える。項目ごとの区分は、1 つの笑いに項目が重なると膨らむ（同じ録音の作り直しで的中が 4 から 2 に見えて、当たった笑いは同じ 2 か所だった）ので、ノートの材料にとどめる
- 山は、0.08 以上のスコアのひと続き（切れ目が 1 窓までならつなぐ）。音声全体のスコアから決まり、手で付けた反応は入らない。予測の窓に入った山の割合を、同じ長さの窓を音声全体に一様に置いたときの見込みと比べる。見込みは山の幅を入れて計算する（窓が覆う割合と比べると、幅のある山に触れやすい分だけ予測に有利になる）
- 予測の窓に山がある割合と、音声全体を同じ長さの窓で見た割合は、`run.json` に残す
- 予測なしで反応ありの笑いは、15 秒以内の物を束ねて強い順に 5 件まで、ノート役が演者への問いにする。全数は別に数える。`questions.json` に入り、`netacho answer` で型に残る
- 拍手は比較に使わない。自動の反応が無い高座と、録音の末尾で切れた窓は判定しない
- 的中率は、窓が音声を覆う割合と、窓に入った笑いの数と一緒に見る。窓が高座の大半を覆うと意味を失う
- `くすぐり` も回収なので、「薄い」候補の母集団に入る。予測が増えると見立て・地口・サゲの順位の比べ方も変わる。意図した動き

### 同じ演目の 2 本の比較

- 落語は同じ噺を何度も高座にかけて磨く。2 本を比べ、変えた所と変えていない所を演者に聞く
- どの台詞とどの台詞が同じくだりかは、対応づけ役が読む。話し言葉は毎回変わるので、文字の一致では揃わなかった（本番と稽古の 1 組で 3%）。役に笑いの位置は見せない
- スクリプトは、対応の区間の実在と順番を確かめ、笑い、間、尺を録音の実測から付ける
- 比べるのは笑いの量ではなくタイミング。スコアの大きさは使わず、各高座の中で山があるかだけを見る
- 間は文字起こしの区間から取らず、VAD の無音から取る。前に笑いがあれば、笑いの長さが混ざるので間と言わない
- 片方の山がごく少なければ、客のいない稽古と見て、本番の笑いを軸に比べる。「片方だけ」は出さない
- 枕は会ごとに変わるので、行どうしは比べない。対応づけ役が枕の終わりと話題を読み、話題ごとに下地かどうか、2 本の位置、笑いの山の数を出す。話題と下地は役の読み、位置と山の数は実測
- 対応の割合は枕を除いて測る
- 枕の笑いは聞き役に全部読ませ、問い 5 件の最後の 1 枠を枕に空けておく。もう 1 本に無いことは聞かず、なぜその話題にしたかを聞く。枕の問いが無ければ、噺の問いで埋める
- 噺の後など、最後の対応の外は比べない
- もう 1 本に無いくだりの笑いは、くだりの最初の 1 件だけを問う。同じくだりで何度も聞かないため
- 本番を 1 本目に指定する。スコアの無い高座は稽古と呼ばず、笑いを比べない
- 種類：同じ言葉（`kept`）、来る所がずれた（`moved`）、間が違う（`ma`）、笑いの所で言葉が違う（`words`）、もう 1 本に無い言葉で笑い（`added`）、片方の録音でだけ拾えた（`one_side`）、笑いの無い所で言葉が違う（`changed`、画面だけ）
- 窓は 5 秒、刻みは 2 秒なので、比べられるのは「どのくだりのどの辺りか」まで。テンポの差が積もらないよう、くだりの長さの比で写してから比べる。30 秒より遠い山とは組まない
- スクリプトは笑いのあった所を全部渡す（上限なし。費用は `budget.py` が止める）。聞き役が全部読んで、聞く価値の高い順に選ぶ。行全体が似ていても語の差を見る。問いは 5 件で止める。候補を計算で絞ると、会場に合わせた一言のような一番良い所を捨てたため
- 聞き役は前後の台詞を読んで、笑いの元の行と、もう 1 本の同じ行を決める
- 行はスクリプトが見せた中の物だけを認める。間のことを聞くときは、その 2 行の前の VAD の無音で差を確かめる
- 比較は 1 つの高座に 1 つ（`compare.json`）。別の組で比べ直すと、前の組の問いの文は残るが、録音と台詞の所は引けなくなる
- 検査に落ちた問いは出さない。場所の合わない問いを決まった文で埋めるより、少ないほうがよい。答えも捨てもしていない問いは、やり直しで作り直す

### マージ

- ツールの出力は `drafts/` に出し、スクリプトが正本に入れる
- `edited_by: performer` の項目は、再生成で上書きしない
- item の `id` は作成時に振り、編集しても変えない
- 照合で `unsupported` の項目は `held: true` にして、`.md` と札に出さない

### 札

- `kata.json` の `staging` のうち `approved: true` だけを、場面順に写す
- `guide.json` は「スクリプトが item から写す欄」と「LLM が書く欄」を分ける。今回の札は前者だけ
- 高座の音声との同期はしない
- 解決していない弟子役の指摘が付いた項目は札に出さない

### 速記

transcript に、反応の位置で印を入れた `sokki.md`。未校正と明記する。LLM は使わない。

### 清書

速記役が聞き違いを直し、話者を付けた `seisho.md`。原文はデータに必ず残す。画面は清書だけを見せ、話者と直した語は推定と示す。全区間があることと、言い換えすぎていないことをスクリプトが確かめる。正本と札には入れない。長い高座でも切れないように、区間を分けて呼ぶ。区間を分けると、ある塊で分かった語が別の塊に届かない。だから先に語の表役が全文を読み、聞き違えた固有の語の表を作って全部の塊に渡す。表の聞こえ方の字が文字起こしに無ければ、スクリプトが落とす。表は手がかりで、同じ字でも文脈で別の語の所は直さない。演者から聞いた語は `--terms` で渡す。

## 5. 契約（schemas/）

全 JSON に `schema_version`。参照先（場面、item、高座）の実在を検証する。

```
item: id, kind, priority(1-3), text, basis(heard|knowledge), source, payoff?, held?,
      edited_by(tool|performer), drafted_by?, history[{at, before, after, by}]
kind: 場面 | 人物 | 見立て | 地口 | くすぐり | 知らない物 | 上下 | 間 | 枕 | サゲ | staging

source（どちらか一方）:
  録音由来 {koza_id, segs:[first,last]}   t0・t1・quote はスクリプトが区間から作る
  演者の追記 {speaker, recorded_at, words, approved, candidate_id?}

staging（kind=staging の item の追加欄）:
  anchor{scene_id, position(before|after|within)}, card_text, reason, approved
  時刻は別の高座へ移せないので、位置は場面で持つ。card_text と reason は演者の言葉のまま

hanashi / kata: scenes[{id,title,order,segs}], characters[], items[]
koza: id, date, venue, neta, performer_id, audio_sha256, excerpt{from,to}?
transcript: segments[{id,start,end,text,words,no_speech_prob,pause_after}], vad[]
timeline: segments[{seg_id,scene_id,speaker(null可),confidence}]
reactions: reactions[{id,t0,t1,kind(laugh|applause|reaction),strength_rank(null可),method(auto|manual)}], music[], hints[], scores
note_candidates: candidates[{id,item_id,payoff_t,window,reaction_ids,why(no_candidate|low_rank)}]
gaps: judged, hits[], weak[], misses[], surprises[{id,reaction_id,t0,t1,scene_id,before_quote,question?}], peak_rate, baseline_rate, humps_total, humps_hit, humps_expected
review: issues[{id,role,kind,anchor{scene_id|item_id},attempt,what_is_missing,round,resolved}]
questions: questions[{id,issue_id,scene_id,text,answer?,dismissed?,spot_id?}]
compare: koza_a, koza_b, other_neta, share, comparable, laughs_compared, practice, humps_a, humps_b, passages, dropped, scenes[], spots[{id,kind,scene_id,a,b?,laugh_a?,laugh_b?,ma_a?,ma_b?,span_a,span_b?}], counts
guide: cards[{order,scene_id,item_id,text,copied_fields,llm_fields}]
drafts/writer: scenes, characters, items（source.segs と payoff だけ。時刻・引用は書かない）
drafts/checker: verdicts[{item_id,verdict,reason}]
drafts/noter: notes[{candidate_id,text}]
```

kind の振り分けの既定：枕・間・上下・staging は型、場面・人物・サゲは噺。見立て・地口・くすぐり・知らない物は構成作家役が決める。

## 6. repo の形と CLI

演者のデータは repo の外に置く。repo に入れるのはコード、schema、テスト、役の本文。テストの素材は自作のダミー（短い架空の噺）だけ。

```
netacho/                        repo
  netacho/  cli.py runner.py schema.py budget.py merge.py evidence.py
            reactions.py candidates.py guide.py render.py
            preprocess/{audio,speech}.py
  roles/    common.md writer.md checker.md noter.md deshi.md scribe.md aligner.md asker.md
  schemas/  *.json
  viewer/   index.html
  tests/    fixtures/dummy_koza/, test_*.py
  docs/     plan.md, reports/
  .claude/  .githooks/  .github/workflows/ci.yml
  CLAUDE.md README.md progress.md LICENSE Makefile pyproject.toml .python-version

$NETACHO_DATA/（既定 ~/netacho-data/）
  raw/                           元の録音
  forbidden-words.txt            repo に入れてはいけない語
  <performer-a>/<演目>/
    hanashi.json/.md  kata.json/.md
    drafts/  review/deshi.json  review/recheck.json  questions.json  guide/guide.json
    koza/<日付-会場>/
      koza.json transcript.json reactions.json timeline.json
      note_candidates.json note.md sokki.md run.json
```

演者は ID（`performer-a`）で扱い、データにも名前を入れない。

```
netacho prep    --audio X --performer ID --neta 演目 --koza KOZA [--from S --to S] [--terms 語,語]
netacho build   --performer ID --neta 演目 --koza KOZA [--reuse]   # 噺・型・照合・弟子役の 2 周
netacho note    --performer ID --neta 演目 --koza KOZA [--reuse]   # 候補、ノート役、速記
netacho run     （prep + build + note）
netacho mark    --koza KOZA --tsv FILE                             # 手で付けた反応の時刻
netacho decide  --performer ID --neta 演目 --speaker S --words W --card T --why Y --scene ID [--candidate C --koza KOZA] [--position before|within|after]
netacho answer  --performer ID --neta 演目 --question Q --speaker S --words W
netacho reread  --performer ID --neta 演目 --koza KOZA             # 演者の判断が入った型を弟子役に読み直させる
netacho recheck --performer ID --neta 演目 --koza KOZA [--reuse]   # 答えた問いを、答えの前後の帳面で読ませる
netacho seisho  --performer ID --neta 演目 --koza KOZA [--title 題] [--terms 語,語]  # 語の表、清書（語の表役、速記役）
netacho guide   --performer ID --neta 演目                         # LLM 0 回
netacho view    --performer ID --neta 演目 --koza KOZA             # out/view/ へ書き出す
netacho compare --performer ID --neta 演目 --koza KOZA --with-koza KOZA2 [--with-neta 演目2]  # 2 本を比べて問う
netacho serve   [--performer ID] [--neta 演目 --koza KOZA] [--port N]  # 画面。録音を入れる、問いに答える
netacho auth                                                         # API の鍵を確かめる（課金なし）
netacho dismiss --performer ID --neta 演目 --question Q --speaker S --reason R  # 的外れの問いを捨てる
```

`netacho view` の後、`make viewer` で `http://localhost:8000/`（配信は `out/view` だけ。読むだけ）。答えるときは `netacho serve` が出す URL を開く。127.0.0.1 だけで待ち受け、書き込みには起動ごとのトークン、Host、Origin を確かめる。読み取りは `make viewer` と同じでトークンを求めない。札のタブからは、場面、言葉、札の文、理由を入れて札に出せる（`netacho decide` と同じ `performer.decide` を通す。位置は場面の中に固定で、選ぶときは CLI を使う）。笑いへの問いには、その所の録音の短い切り抜きと前後の台詞を添える。切り抜きは、1 本の問いでは前 12 秒、後 6 秒。比較の問いでは台詞の頭の 8 秒前から、台詞と笑いの終わりの 6 秒後まで（90 秒まで）。切り抜きはトークンのある画面にだけ渡し、`out/view` には置かない。画面の引用と笑いへの問いは、清書があれば清書で見せる。保存する根拠は文字起こしのままで、照合もそれで行う。Claude の直した文で Claude の推定を確かめないため。`serve` は既定のブラウザで開く（`--no-open` で止める）ので、トークン入りの URL はブラウザの履歴に残る。トークンは起動ごとに変わる。`serve` は CLI のサブコマンドで、書き込み（答え、答えの直し、公演日と会場の直し）は `netacho answer` と同じ `performer` を通す（演者本人がコマンドを打たずに答えるため）。公演日が分からない高座は `0000-00-00` で持つ。画面から録音を入れられる（`netacho/jobs.py`）。録音は高座のフォルダに `source.*` として置き、鍵の確認、prep、build、note、seisho（比べる相手を選んだら compare も）を、段階ごとに別のプロセスの `python -m netacho` で順に回す。端末で打つのと同じ道なので、`budget.py` の上限も効く。1 回 $5 は録音 1 本（やり直しも含む）で数える。始めたときの累計を `NETACHO_RUN_BASE_USD` で各段階に渡し、それより後に使った分をその回の使用とする（サーバを再起動すると数え始めに戻る。同じ時に端末で回した分も入るので、多めに数える）。ディスクに録音の 2 倍と 1 GB の空きが無ければ受け取らない。同時に 1 本だけ。状態はサーバのメモリに持ち、画面を閉じても続く。落ちたら止まった段階から `--reuse` でやり直す（prep には付けない）。各段階の出力は高座のフォルダの `job.log` にだけ書き、応答には段階の名前と終了コードだけを出す。始める前に、上限と費用の累計と、鍵の有無（値は出さない）を画面に出す。高座が 1 本も無ければ高座を開かずに起動し、録音を入れる画面だけを出す。`--neta` と `--koza` を省くと、いちばん新しい高座を開く。画面は 5 枚：反応の波と候補、型、札、弟子役の指摘と質問、清書。「Claude の候補」「演者の決定」「スクリプトの転記」を色とラベルで見分けられるようにする。

### 費用の管理（`budget.py`）

- 呼ぶ前に上限額を出す：`count_tokens` の入力 × 入力単価 ＋ `max_tokens` × 出力単価。キャッシュの割引は見込まない
- 1 回が $5 を超えるか、累計との合計が $30 を超えるなら呼ばずに止まる
- 累計は `out/ledger.json` に足す
- `--reuse` のキャッシュキーは、入力・役の本文・モデル・schema のハッシュ
- transcript は構成作家役の先頭に置き `cache_control` を付ける
- 単価（USD / 100 万トークン。入力、出力、キャッシュ読み、キャッシュ書き）：Opus 5.5 は 4 / 20 / 0.20 / 5、Sonnet 5.5 は 2 / 10 / 0.20 / 2.5

一席の見積もり（40 分、transcript 約 3 万トークンと仮定、未実測）：

| 役 | 回数 | 上限 |
| --- | --- | --- |
| 構成作家役 | 2 | 約 $1.5 |
| 弟子役 | 2 | 約 $0.4 |
| 照合役 | 2 | 約 $0.2 |
| ノート役 | 1 | 約 $0.05 |
| 合計 | | **約 $2.2** |

3〜5 分の抜粋ならもっと安い。まず抜粋で実測して、この表を置き換える。

## 7. 開発の体制

| 誰 | 役割 |
| --- | --- |
| メインの Claude Code（Opus 5.5） | 実装。`~/claude-hackathon/netacho` で動かす |
| `reviewer` サブエージェント（Sonnet、読み取り専用、文脈なし） | PR の前に必ず 1 回。差分の取り方、ねらい、見てほしい点だけを渡し、結論は渡さない |
| ユーザー | マージの判断、耳での確認、演者とのやり取り、動画、提出 |
| Claude Code Review（GitHub Actions） | PR を作ると自動で走る |
| `@claude`（GitHub Actions） | Issue や PR のコメントで呼べる。枝の実装には使わない |

使わないもの：judge の採点。

手順（1 タスク = 1 Issue = 1 PR）：

1. `/start <issue>`：Issue を読むか作り、main から `<N>-<slug>` のブランチを切る
2. 実装
3. `/pr`：`make lint && make test` → 文脈なしの reviewer → `/report` で `docs/reports/` に報告書 → PR 作成（`Closes #N`）
4. `scripts/pr-reviews.sh` で自動レビューの指摘を読み、反映する。上限は 3 周で、4 周目以降は Issue に回す。**機能凍結の後は待たない**
5. ユーザーが「マージして」と言ったら Squash merge
6. `/log`：progress.md と Issue に記録

小さな修正は、Issue と報告書を省いてよい（PR 本文に「省略」と書く）。reviewer は省かない。報告書に演者のデータ、名前、所属を書かない。

フック（`.claude/settings.json`）：

| いつ | スクリプト | 内容 |
| --- | --- | --- |
| Write / Edit の前 | `guard-write.sh` | `.env`、`*.key`、`credentials*.json`、`netacho-data/` への直接の書き込みを拒否（演者のデータは CLI だけが書く） |
| Bash の前 | `guard-bash.sh` | reviewer の印が無い PR 作成と main への push を拒否。消す・書き換える・マージする系は承認を挟む |
| Write / Edit の後 | `ruff-after-edit.sh` | `.py` に ruff |
| Agent の後 | `on-reviewer.sh` | reviewer を呼んだら、ブランチに印を付ける |
| Stop | `log-reminder.sh` | 今日の progress.md が無ければ催促 |

`.githooks/pre-push`：main への push を止める。さらに `forbidden-words.txt` の語を、push する内容とコミットメッセージから検査する（語のリストは repo の外）。CI：ruff、pytest、gitleaks。

スキル：`/start`（Issue → ブランチ）、`/pr`（テスト → reviewer → 報告書 → PR）、`/report`（`docs/reports/YYYY-MM-DD-<slug>.md`。承認者向けに、意図・判断・数字・依頼を先に、技術は付録）、`/log`（`progress.md` と Issue に記録）。

### CLAUDE.md に書くこと

- 何を作っているか、動かし方（`make setup`、`make test`、`make lint`）
- 秘密は環境変数だけ。`.env` を読まない
- 演者のデータ、本名、所属、個人情報を repo に入れない。テストはダミーだけ
- LLM の周りは決定的コード。Claude は読む・文にする・指摘するだけ
- 噺と型は分けて書く。型は `heard` だけ
- 演者の言葉は書き換えない。演者の項目は再生成で上書きしない
- 札はスクリプトだけで作る（LLM 0 回）
- 役は自分の `drafts/` だけ。検証は受信直後、保存の前
- 弟子役は録音を読まない。直すのは構成作家役
- 数字は実測だけ。推定を README に書かない
- コミットは `<type>: <日本語の要約>`（feat / fix / docs / style / refactor / perf / test / chore）
- 文章・コメント・コミットメッセージは日本語
- 1 タスク = 1 Issue = 1 PR。`/start` → 作業 → `/pr`。PR の前に文脈なしの reviewer を通す。マージはユーザーが言ったときだけ。承認は 1 回ごと
- 自動レビューの指摘は 3 周まで反映し、4 周目以降は Issue に回す
- 報告書は `docs/reports/`。終わりに `/log`
- 「承認を得てから実行するコマンド」の節（Soegaki の CLAUDE.md の同名の節をそのまま写す。`guard-bash.sh` の理由文が参照している）

## 8. repo の準備（最初のタスク）

履歴は持ち込まない。`~/claude-hackathon/soegaki` から次のファイルをコピーして直す。README に「開発の土台は、このハッカソンで先に始めた別プロダクト Soegaki（非公開）から流用」と一行書く。

| 元のファイル | 先 | 直すこと |
| --- | --- | --- |
| `soegaki/runner.py` | `netacho/runner.py` | `PRICE`、`Usage`、`call_role`、`role_prompt`、`_body` だけ残す。`RETRIES=0`。役ごとの `EFFORT`、`MAX_TOKENS` に。`budget` を通す。`messages.stream` + `get_final_message` にする（max_tokens が大きいため） |
| `soegaki/schema.py` | `netacho/schema.py` | `load`、`api_schema`、`_strip`、`validate` だけ残す |
| `soegaki/preprocess/speech.py` | 同名 | 既定を `medium` に。`character_names` を `terms` に。区間 ID と `pause_after` を足す |
| `soegaki/preprocess/audio.py` | 同名 | 抜粋の `--from` `--to` を足す |
| `pyproject.toml`、`.python-version` | 同名 | 名前を netacho に。依存は anthropic、faster-whisper、jsonschema、numpy、python-dotenv。Python は 3.12 固定（3.14 は av が合わない）。`claude-agent-sdk` は外す |
| `Makefile`、`scripts/setup.sh`、`.github/workflows/ci.yml`、`LICENSE` | 同名 | Makefile は setup、test、lint、viewer だけ |
| `.claude/settings.json`、`.claude/hooks/*` | 同名 | 印の名前を `netacho-reviewed` に（`on-reviewer.sh` と `guard-bash.sh`）。`guard-write.sh` は上の表のとおり |
| `.claude/agents/reviewer.md` | 同名 | 読む節と見る順を第 2〜6 節に合わせて書き直す |
| `.claude/skills/start`、`pr`、`report`、`dev-log` | 同名 | そのまま使う。`report` のスクショ手順の映像の行と「デモ素材」の語だけ、演者のデータに合わせて直す |
| `.github/workflows/claude-code-review.yml`、`claude.yml` | 同名 | review の prompt の観点を §2〜§6 と §7 の CLAUDE.md の約束に書き換える（読む節の名前も）。自分のブランチの PR に絞る条件と、持ち主だけが @claude を呼べる条件は残す |
| `.githooks/pre-push` | 同名 | 語の検査を足す |
| `tests/test_guard_bash.py`、`tests/fixtures/guard_bash_cases.tsv` | 同名 | そのまま |

持ち込まないもの：`pack`、`rescue`、`tts`、`mix`、`listener_input`、`ids.py`、`roles/`、`schemas/`、`docs/`、`spike/`、`input/`、`out/`、視聴画面の本体、`judge.md`。

`.gitignore`：`.env`、`.env.*`（`!.env.example`）、`*.key`、`credentials*.json`、`.venv/`、`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.ruff_cache/`、`models/`、`.cache/`、`.DS_Store`、`out/`、`koza/`、`hanashi.json`、`kata.json`、`sokki.md`、`*.mp4`、`*.mov`、`*.MOV`、`*.MP4`、`*.wav`、`*.m4a`、`*.mp3`、`*.aac`、`*.flac`。

順番：

1. `git -C ~/claude-hackathon/netacho config user.email 211286362+tomiya3@users.noreply.github.com`（**コミットの前に必ず**）
2. コピーして直す。`make test` の guard_bash が通ること
3. `~/netacho-data/forbidden-words.txt` をユーザーが作り、`git grep -iFf` で手で検査する
4. main に最初のコミット
5. ユーザーが `! git -C ~/claude-hackathon/netacho push -u origin main` を実行（`setup.sh` の前。フックが有効になると止まるため）
6. `make setup`
7. `ANTHROPIC_API_KEY` を環境変数に用意する
8. ユーザーが netacho に `CLAUDE_CODE_OAUTH_TOKEN` を登録し、Claude の GitHub App を netacho にも入れる（これが無いと自動レビューと @claude は動かない。登録するまで PR は失敗の印が付くだけで、作業は止まらない）
9. `~/claude-hackathon/netacho` で新しいセッションを始める
10. 小さな PR を 1 本作り、`/start` → フックの拒否（印なしの PR 作成）→ reviewer → `/report` → `/pr` → 自動レビュー、を一度通して確かめる

## 9. タスク

順に通す。**木曜のうちに抜粋で一巡する。深くするのはその後。**

| # | タスク | できた条件 |
| --- | --- | --- |
| T0 | §8 の repo 準備 | 初回 push が済み、CI が緑。手順 10 の PR で、Issue、フック、reviewer、報告書、自動レビューが動いた |
| T1 | 過去の高座の全編を文字起こし（裏で回す） | 40 分にかかる時間が分かり、質を人が読んだ |
| T2 | 契約：schemas と検証 | ダミーの全 JSON が検証を通る。壊れた参照で落ちる |
| T3 | 前処理と反応の候補 | 抜粋 3〜5 分から transcript と reactions が出る。上位 10 件を耳で確かめた |
| T4 | 構成作家役、根拠の検証、照合役、マージ | 抜粋から噺・型が出る。落とした数と照合の数が run.json に出る |
| T5 | ノート役 | 抜粋から note.md が出る。「薄い」候補の根拠が確かめられる |
| T6 | 弟子役と仕分け | 場面を指した具体的な指摘が出る。ask_performer が questions.json に出る。2 周目で減る |
| T7 | decide、answer、guide | 演者の一行で札が変わる。LLM 0 回のテストが通る。再生成で上書きされないテストが通る |
| T8 | 速記 | sokki.md が出る |
| T9 | 視聴画面 | 4 枚が動く。3 種類の表示を見分けられる |
| T10 | 全編を回す（提出の条件にしない） | 費用と時間の実測 |

作らない：切り抜き、版切替、解説ボタン、稽古モード、玄人版、二分割、追従、多言語字幕、子供役、予告編、速記の PDF、映像の解析、演目の自動判定、話者割り当ての評価。

話者：`timeline.speaker` は null を許す。文字起こしと演目の知識から確度の高いものだけ入れる。札は話者に依存させない。

## 10. 日程

| 段階 | やること |
| --- | --- |
| 11/5 | T0 → T1（裏で回す）→ T2 → T3 → T4 → T5 → T6 → T7 を粗く一巡 → 画面を仮収録 → 絵コンテ。**24:00 に判断** |
| 11/6 | 見せ場を抜粋で仕上げる → T8 → T9 → **機能凍結** → 演者と軽く撮影 → 動画、README → 最終確認 → public、YouTube 限定公開 → **24:00 までに提出** |
| 11/7 以降 | 良い素材があれば動画を差し替え、リンクを確かめる。提出済みの一式は壊さない |

木曜 24:00 の判断：

- 反応の波が自動で読めない → 耳で付けた時刻で波を描き「手で付けた」と出す
- 弟子役が一般論しか言わない → 1 周の指摘を見せるだけにする
- 照合役に手が回らない → 根拠の検証（スクリプト）だけ残して切る
- 見せ場の 3・4（一言 → 型 → 札、LLM なし）は外部に依存しないので、どの場合も残す

### 演者の撮影

インタビューはしない。短時間で軽く撮る。

1. ノートの画面を見てもらう
2. 「ここに解説を入れたい」の一言をもらう
3. `netacho decide` で入れて、札が変わる画面を見てもらう

撮影の最中に API を呼ばない。分析は生成済みの JSON を使う。弟子役の読み直しは撮影の後に回して、編集でつなぐ。

撮れなかったら、演者が実際に言った言葉を作者が代わりに入力する。それも無ければ、作者の判断で流れを通し、画面と動画で「作者が演者役」と明示する。

## 11. 動画と README

3 分の割り振り：

| 秒 | 内容 |
| --- | --- |
| 0:00–0:20 | 課題（作者のナレーションと字幕）。芸は口移しで、高座の反応は本人の記憶にしか残らない |
| 0:20–1:00 | 録音を入れる → 反応の波、根拠つきの型、弟子役の指摘 |
| 1:00–1:45 | 演者が見て、一言。型に入る |
| 1:45–2:15 | 札が変わる。LLM を呼んでいないことを見せる |
| 2:15–2:40 | 判断の理由が弟子役に渡る |
| 2:40–3:00 | Claude の役割と限界。決めるのは演者 |

Claude の活用として見せるもの：見せる物の境界で分けた 4 役、根拠の検証と照合で落とした件数、弟子役から演者への問い、LLM を呼ばない経路を意図して残したこと、費用の管理、Claude Code での開発（フック、文脈なしの reviewer）。

数字は実測だけ：反応検出を耳で確かめた結果、根拠つき item の割合、照合の 3 区分、弟子役の指摘の減り方、一席の費用と時間。

README（日本語）：一行と画像 → 動画リンク → 使い方（repo にはダミーだけで、`make test` と視聴画面がダミーで動く）→ 仕組みと役の構成 → 数字 → Claude の使い方 → 限界 → ライセンス（MIT）→ 土台の流用の一行（別プロダクト Soegaki）。

## 12. 検証

1. T0：`make test`、`make lint`、CI が緑
2. 11/5：schema の検証が動く。抜粋で一巡が通る。落ちた item の数が run.json に出る。反応の上位 10 件を耳で確かめた結果が残っている
3. 11/6：`staging` を一行足すと、LLM を呼ばずに札が変わる（テストで LLM の呼び出しが 0 回）。演者の項目が再生成で上書きされない（テスト）。費用が run.json に出る
4. 提出前：gitleaks と語の検査が通る。動画と画面に名前、所属、客の顔が映っていない。author が noreply。提出リンクをログアウトした状態で開く
5. 11/7：差し替えたら、リンクと再生をもう一度確かめる

## 13. ユーザーがやること

- 演者との連絡（録音の受け取り、撮影の段取り）
- 過去の高座の録音を `~/netacho-data/raw/` に置く
- `forbidden-words.txt` を作る
- 初回 push、マージの指示、`ANTHROPIC_API_KEY` の用意
- netacho への `CLAUDE_CODE_OAUTH_TOKEN` の登録と、Claude の GitHub App の追加
- 反応の耳での確認
- 動画の編集、YouTube へのアップロード、public への切り替え、提出
