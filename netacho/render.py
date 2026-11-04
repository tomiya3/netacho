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


def note_markdown(
    koza: dict, candidates: dict, notes: dict[str, str], items: dict, scene_titles: dict
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


REACTION_LABEL = {"laugh": "（笑い）", "applause": "（拍手）", "reaction": "（反応）"}


def sokki_markdown(koza: dict, transcript: dict, reactions: dict) -> str:
    """速記。反応が始まった時点で話していた区間（無ければ直前の区間）の後ろに印を入れる。LLM は使わない。"""
    segs = transcript["segments"]
    after: dict[int, list[str]] = {}
    for r in sorted(reactions["reactions"], key=lambda x: x["t0"]):
        at = max((i for i, s in enumerate(segs) if s["start"] <= r["t0"]), default=-1)
        marks = after.setdefault(at, [])
        if REACTION_LABEL[r["kind"]] not in marks:  # 同じ所に同じ印を重ねない
            marks.append(REACTION_LABEL[r["kind"]])
    rows = reactions["reactions"]
    if not rows:
        note = "反応の候補は拾えていません（反応が無かったという意味ではありません）。"
    elif any(r["method"] == "manual" for r in rows):
        note = "印は耳で付けた時刻のあたりの行にあります。"
    else:
        note = "印の位置は目安です（音声の窓で拾っているので、前後の行のこともあります）。"
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
