"""噺・型の .md。弟子役はこれだけを読む。保留の項目は出さない。"""

from __future__ import annotations

QUOTE_MAX = 60


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


def _short(text: str) -> str:
    return text if len(text) <= QUOTE_MAX else text[:QUOTE_MAX] + "…"


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
    lines = [f"- `{item['id']}` 【{item['kind']}】{item['text']}{mark}"]
    lines.append(f"  - 根拠：「{_short(src['quote'])}」")
    if "payoff" in item:
        lines.append(f"  - 回収：「{_short(item['payoff']['quote'])}」")
    return lines


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
