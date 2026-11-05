"""清書。速記役の出力を検査して、原文と並べて残す。

演者の言葉を書き換えないために、原文はいつも残す。text と speaker は Claude の推定として扱い、
言い換えすぎた文は原文に戻す。正本（噺・型）と札には入れない。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .render import REACTION_LABEL, marks_after

MIN_SIMILARITY = 0.5  # 原文とこれより違う文は、聞き違いの修正ではなく言い換えとみなす。暫定


def _similar(raw: str, text: str) -> bool:
    return SequenceMatcher(None, raw, text, autojunk=False).ratio() >= MIN_SIMILARITY


def build(draft: dict, transcript: dict, reactions: dict | None) -> tuple[dict, dict]:
    """(seisho.json の中身, 数)。全区間を原文の順に残す。"""
    written: dict[str, list[dict]] = {}
    for row in draft["lines"]:
        written.setdefault(row["seg_id"], []).append(row)
    marks = marks_after(transcript["segments"], reactions["reactions"]) if reactions else {}
    lines = []
    changed = kept_raw = missing = 0
    for i, seg in enumerate(transcript["segments"]):
        raw = seg["text"].strip()
        rows = written.get(seg["id"], [])
        row = rows[0] if len(rows) == 1 else None
        if row is None:
            missing += 1
        text = row["text"].strip() if row else raw
        speaker = (row["speaker"] or None) if row else None
        if not text or not _similar(raw, text):
            if row is not None and raw:
                kept_raw += 1
            text = raw
        line = {
            "seg_id": seg["id"],
            "speaker": speaker,
            "text": text,
            "raw": raw,
            "changed": text != raw,
        }
        if marks.get(i):
            line["marks"] = marks[i]
        changed += line["changed"]
        if raw or line.get("marks"):
            lines.append(line)
    doc = {"schema_version": 1, "koza_id": transcript["koza_id"], "lines": lines}
    return doc, {"lines": len(lines), "changed": changed, "kept_raw": kept_raw, "missing": missing}


def markdown(koza: dict, doc: dict) -> str:
    out = [
        f"# 清書：{koza['neta']}（{koza['id']}）",
        "",
        "Claude が文脈から聞き違いを直し、話者を付けた読み物です。話者と直した語は推定で、"
        "演者の言葉そのものではありません。直した行には原文を添えています。"
        "印は自動判定の候補です。",
        "",
    ]
    for line in doc["lines"]:
        who = f"{line['speaker']}（推定）：" if line["speaker"] else ""
        out.append(f"{who}{line['text']}{''.join(line.get('marks', []))}")
        if line["changed"]:
            out.append(f"　（原文：{line['raw']}）")
    return "\n".join(out).rstrip() + "\n"


__all__ = ["REACTION_LABEL", "build", "markdown"]
