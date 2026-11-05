"""清書。速記役の出力を検査して、原文と並べて残す。

演者の言葉を書き換えないために、原文はいつも残す。text と speaker は Claude の推定として扱い、
言い換えすぎた文は原文に戻す。正本（噺・型）と札には入れない。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .render import marks_after

MIN_SIMILARITY = 0.5  # これより違う行は、直しすぎかもしれない印を付ける（歌の表記の修正もここに入る）。暫定
LENGTH_RATIO = (0.6, 1.5)  # 長さがこの範囲の外なら、削りか足しとみなして原文に戻す。暫定
LENGTH_MARGIN = 4  # 短い区間は比率では見られないので、この字数までの差は許す
SHORT_LINE = 6  # これ以下の短い区間（相づちなど）は、似ていなければ原文に戻す


def _length_ok(raw: str, text: str) -> bool:
    low, high = LENGTH_RATIO
    return len(raw) * low - LENGTH_MARGIN <= len(text) <= len(raw) * high + LENGTH_MARGIN


def _similar(raw: str, text: str) -> bool:
    return SequenceMatcher(None, raw, text, autojunk=False).ratio() >= MIN_SIMILARITY


def build(
    draft: dict, transcript: dict, reactions: dict | None, names: set[str] | None = None
) -> tuple[dict, dict]:
    """(seisho.json の中身, 数)。全区間を原文の順に残す。"""
    segs = transcript["segments"]
    ids = {s["id"] for s in segs}
    written: dict[str, list[dict]] = {}
    for row in draft["lines"]:
        written.setdefault(row["seg_id"], []).append(row)
    marks = marks_after(segs, reactions["reactions"]) if reactions else {}
    stats = dict.fromkeys(
        ("changed", "kept_raw", "missing", "duplicate", "empty", "heavy", "unknown_speaker"), 0
    )
    lines = []
    for i, seg in enumerate(segs):
        raw = seg["text"].strip()
        rows = written.get(seg["id"], [])
        row = rows[0] if len(rows) == 1 else None
        if len(rows) > 1:
            stats["duplicate"] += 1
        elif row is None and raw:  # 空の区間は役に渡していない
            stats["missing"] += 1
        text = row["text"].strip() if row else raw
        speaker = (row["speaker"] or None) if row else None
        if speaker is not None and names is not None and speaker not in names:
            speaker = None
            stats["unknown_speaker"] += 1
        line = {"seg_id": seg["id"], "speaker": speaker, "text": text, "raw": raw, "changed": False}
        if row is not None and raw and not text:
            stats["empty"] += 1
            line["text"] = raw
        elif not _length_ok(raw, text) or (len(raw) <= SHORT_LINE and not _similar(raw, text)):
            stats["kept_raw"] += 1
            line["text"] = raw
            line["proposal"] = text  # 演者が確かめられるように案は残す
        elif not _similar(raw, text):
            stats["heavy"] += 1
            line["heavy"] = True
        line["changed"] = line["text"] != raw
        if marks.get(i):
            line["marks"] = marks[i]
        stats["changed"] += line["changed"]
        if raw or line.get("marks"):
            lines.append(line)
    doc = {"schema_version": 1, "koza_id": transcript["koza_id"], "lines": lines}
    extra = sum(1 for k in written if k not in ids)
    return doc, {"lines": len(lines), "extra": extra, **stats}


def markdown(koza: dict, doc: dict, stats: dict | None = None) -> str:
    out = [
        f"# 清書：{koza['neta']}（{koza['id']}）",
        "",
    ]
    if stats and (stats["missing"] or stats["duplicate"]):
        out += [
            f"**速記役が返さなかった区間が {stats['missing'] + stats['duplicate']} あります。その行は原文のままです。**",
            "",
        ]
    out += [
        "Claude が文脈から聞き違いを直し、話者を付けた読み物です。話者と直した語は推定で、"
        "演者の言葉そのものではありません。直した行には原文を添えています。"
        "印は自動判定の候補です。",
        "",
    ]
    for line in doc["lines"]:
        who = f"{line['speaker']}（推定）：" if line["speaker"] else ""
        out.append(f"{who}{line['text']}{''.join(line.get('marks', []))}")
        if line["changed"]:
            note = "大きく直した。原文" if line.get("heavy") else "原文"
            out.append(f"　（{note}：{line['raw']}）")
        if "proposal" in line:
            out.append(f"　（長さが大きく変わるので原文のまま。案：{line['proposal']}）")
    return "\n".join(out).rstrip() + "\n"
