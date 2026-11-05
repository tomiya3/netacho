"""噺・型の .md。弟子役はこれだけを読む。

保留の項目は出さない。録音の引用も出さない（弟子役に transcript を読ませず、書かれていない物を
補わせないため）。根拠は JSON と視聴画面で見る。
"""

from __future__ import annotations


def scene_lookup(timelines: list[dict]) -> dict[tuple[str, str], str]:
    """(高座 ID, 区間 ID) → 場面 ID。"""
    return {
        (t["koza_id"], s["seg_id"]): s["scene_id"]
        for t in timelines
        for s in t["segments"]
        if s["scene_id"] is not None
    }


def scene_of_item(item: dict, lookup: dict[tuple[str, str], str]) -> str | None:
    if "anchor" in item:
        return item["anchor"]["scene_id"]
    src = item["source"]
    if "segs" in src:
        return lookup.get((src["koza_id"], src["segs"][0]))
    return None


def _line(item: dict) -> list[str]:
    src = item["source"]
    if "words" in src:  # 演者の言葉は切らずにそのまま
        lines = [
            f"- `{item['id']}` 【{item['kind']}・演者】{item['text']}",
            f"  - 演者の言葉：「{src['words']}」",
        ]
        if item.get("reason"):
            lines.append(f"  - 理由：{item['reason']}")
        if item.get("approved"):
            lines.append(f"  - 札：{item['card_text']}")
        return lines
    mark = "（暫定）" if item["basis"] == "knowledge" else ""
    return [f"- `{item['id']}` 【{item['kind']}】{item['text']}{mark}"]


def to_markdown(book: dict, lookup: dict[tuple[str, str], str]) -> str:
    title = {"hanashi": "噺", "kata": "型"}[book["book"]]
    out = [f"# {title}：{book['neta']}", ""]
    if book["book"] == "hanashi" and book["characters"]:
        out += ["## 人物", ""]
        out += [f"- `{c['id']}` {c['name']}：{c['note']}" for c in book["characters"]]
        out.append("")
    shown = [i for i in book["items"] if not i.get("held")]
    for scene in sorted(book["scenes"], key=lambda s: s["order"]):
        out += [f"## `{scene['id']}` {scene['title']}", ""]
        rows = [i for i in shown if scene_of_item(i, lookup) == scene["id"]]
        out += [line for i in rows for line in _line(i)] or ["（書いてある項目なし）"]
        out.append("")
    scene_ids = {s["id"] for s in book["scenes"]}
    rest = [i for i in shown if scene_of_item(i, lookup) not in scene_ids]
    if rest:
        out += ["## 場面の分からない項目", ""]
        out += [line for i in rest for line in _line(i)]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def clock(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _rate_line(gaps: dict) -> list[str]:
    if gaps.get("peak_rate") is None or gaps.get("baseline_rate") is None:  # 前の版の gaps.json には無い
        return []
    return [
        f"- 予測の窓 {gaps['peak_n']} 件のうち、笑いの山（弱い山を含む）がある割合 {gaps['peak_rate']:.0%}。"
        f"同じ長さの窓を音声全体に置くと {gaps['baseline_rate']:.0%}"
    ]


def gaps_markdown(gaps: dict, items: dict) -> list[str]:
    """予測と実際の比較。判定できない高座では、そう書く。"""
    if not gaps["judged"]:
        return ["## 笑いどころの予測と実際", "", "自動で拾った笑いが無いので、比べていません。", ""]
    weak = f"、弱い山 {len(gaps['weak'])}" if "weak" in gaps else ""  # 前の版は測っていない。0 と書かない
    out = [
        "## 笑いどころの予測と実際",
        "",
        "Claude は反応を見ずに、文字起こしだけで笑いどころを予測しました。",
        "",
        f"- 的中 {len(gaps['hits'])}{weak}、空振り {len(gaps['misses'])}、"
        f"予測なしで反応あり {gaps['surprise_total']}",
        f"- 笑い {gaps['laughs_total']} か所のうち、予測の窓に入ったのは {gaps['laughs_hit']} か所。"
        f"窓は音声の {gaps['window_share']:.0%} を覆っている",
        "- 空振りは、検出できなかっただけのこともあります。反応が無かったという意味ではありません",
        *([] if weak else ["- 前の版の数え方です。空振りに弱い山を含みます。note をやり直すと分かれます"]),
        *_rate_line(gaps),
        "",
    ]
    if gaps["surprises"]:
        out += [f"### 文字からは読めなかった所（{gaps['surprise_total']} か所から、離れた強い順に）", ""]
        for s in gaps["surprises"]:
            out += [
                f"- {clock(s['t0'])} {s.get('question', '')}",
                f"  - 前の台詞：「{s['before_quote']}」",
            ]
        out.append("")
    if gaps["misses"]:
        out += ["### 予測したが、反応候補が見つからない所", ""]
        out += [f"- 【{items[m['item_id']]['kind']}】{items[m['item_id']]['text']}" for m in gaps["misses"]]
        out.append("")
    return out


def note_markdown(
    koza: dict,
    candidates: dict,
    notes: dict[str, str],
    items: dict,
    scene_titles: dict,
    gaps: dict | None = None,
) -> str:
    """演者が読むノート。文はノート役、それ以外はスクリプトが写す。"""
    out = [
        f"# ノート：{koza['neta']}（{koza['id']}）",
        "",
        "反応の候補は音量から機械的に拾ったものです。候補が少ない所を挙げていますが、"
        "受けなかったという意味ではありません。原音で確かめてください。",
        "",
    ]
    if candidates.get("notices"):
        out += [*candidates["notices"], ""]
    if gaps is not None:
        out += gaps_markdown(gaps, items)
    if not candidates["candidates"]:
        out += ["今回は候補を選びませんでした。反応が十分だったという意味ではありません。", ""]
    for c in candidates["candidates"]:
        it = items[c["item_id"]]
        out += [
            f"## `{c['id']}` {clock(c['payoff_t'])} {scene_titles.get(c['item_id'], '場面不明')}",
            "",
            f"- {notes[c['id']]}",
            f"- 項目：【{it['kind']}】{it['text']}（`{it['id']}`）",
            f"- 仕込み：「{it['source']['quote']}」",
            f"- 回収：「{it['payoff']['quote']}」",
            "",
        ]
    return "\n".join(out).rstrip() + "\n"


SAME_MARK_SEC = 5.0  # 同じ行で、これより近い同じ印は 1 つにまとめる
REACTION_LABEL = {"laugh": "（笑い）", "applause": "（拍手）", "reaction": "（反応）"}


def marks_after(segs: list[dict], rows: list[dict]) -> dict[int, list[str]]:
    """反応の印を、反応が始まった時点で話していた区間（無ければ直前の区間）の後ろに置く。-1 は先頭。"""
    after: dict[int, list[str]] = {}
    last: dict[tuple[int, str], float] = {}
    for r in sorted(rows, key=lambda x: x["t0"]):
        at = max((i for i, s in enumerate(segs) if s["start"] <= r["t0"]), default=-1)
        label = REACTION_LABEL[r["kind"]]
        if r["t0"] - last.get((at, label), -SAME_MARK_SEC) >= SAME_MARK_SEC:  # 離れていれば別の印
            after.setdefault(at, []).append(label)
        last[(at, label)] = r["t0"]
    return after


def sokki_markdown(koza: dict, transcript: dict, reactions: dict) -> str:
    """速記。文字起こしに反応の印を入れる。LLM は使わない。"""
    segs = transcript["segments"]
    after = marks_after(segs, reactions["reactions"])
    rows = reactions["reactions"]
    if not rows:
        note = "反応の候補は拾えていません（反応が無かったという意味ではありません）。"
    elif any(r["method"] == "manual" for r in rows):
        note = "印は耳で付けた時刻のあたりの行にあります。"
    else:
        note = (
            "印は自動判定の候補で、種類も外れることがあります。"
            "位置は目安です（音声の窓で拾っているので、前後の行のこともあります）。"
        )
    out = [
        f"# 速記：{koza['neta']}（{koza['id']}）",
        "",
        f"未校正です。文字起こしの誤りを含みます。{note}",
        "",
    ]
    if after.get(-1):  # 最初の台詞より前の反応は、台詞に付けず印だけの行にする
        out.append("".join(after[-1]))
    for i, s in enumerate(segs):
        line = s["text"].strip() + "".join(after.get(i, []))
        if line:
            out.append(line)
    if not segs:
        out.append("（文字起こしの区間がありません）")
    return "\n".join(out).rstrip() + "\n"
