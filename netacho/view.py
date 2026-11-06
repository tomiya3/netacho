"""視聴画面に渡す JSON を書き出す。LLM は呼ばない。録音は書き出さない。"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from . import gaps, render, store

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "view"
VIEWER = ROOT / "viewer" / "index.html"
PLACE_BEFORE, PLACE_AFTER = 15.0, 3.0  # 問いの前後に見せる台詞の範囲。秒


def _optional(path: Path, schema: str) -> dict | None:
    return store.read(path, schema) if path.exists() else None


def _with_scene(items: list[dict], lookup: dict) -> list[dict]:
    return [{**i, "scene_id": render.scene_of_item(i, lookup)} for i in items]


def _timelines(neta: store.Neta, koza_id: str) -> list[dict]:
    """全高座の timeline。作りかけの別の高座があっても、見たい高座は開けるように読み飛ばす。"""
    rows = []
    for path in sorted((neta.base / "koza").glob("*/timeline.json")):
        try:
            rows.append(store.read(path, "timeline"))
        except ValueError:
            if path.parent.name == koza_id:
                raise
            print(f"読み飛ばした：{path.parent.name} の timeline.json", file=sys.stderr)
    return rows


def question_places(
    gaps_doc: dict | None, questions: dict | None, transcript: dict, clean: dict | None
) -> dict:
    """笑いへの問いごとの、時刻と前後の台詞。清書があれば清書で、無ければ文字起こしで。

    問いに添えた台詞は文字起こしのままなので、これが無いと演者にはどこのことか分からない。
    """
    if not gaps_doc or not questions:
        return {}
    lines = {row["seg_id"]: row for row in (clean or {"lines": []})["lines"]}
    koza_id = gaps_doc["koza_id"]
    places = {}
    for s in gaps_doc["surprises"]:
        head = gaps.attempt_of(s, koza_id)
        q = next((x for x in questions["questions"] if x["text"].startswith(head)), None)
        if q is None:
            continue
        rows, all_clean = [], True
        for seg in transcript["segments"]:
            if seg["end"] <= s["t0"] - PLACE_BEFORE or seg["start"] >= s["t0"] + PLACE_AFTER:
                continue
            line = lines.get(seg["id"])
            if line is not None and line["raw"].strip() != seg["text"].strip():
                line = None  # 文字起こしを作り直す前の清書は、別の台詞に当たる
            all_clean = all_clean and line is not None
            rows.append(
                {"speaker": line["speaker"], "text": line["text"]}
                if line
                else {"speaker": None, "text": seg["text"].strip()}
            )
        places[q["id"]] = {"t0": s["t0"], "clean": bool(rows) and all_clean, "lines": rows}
    return places


def clean_lines(transcript: dict | None, clean: dict | None) -> dict:
    """区間 ID ごとの清書の行。文字起こしを作り直す前の古い行は使わない。"""
    if not transcript or not clean:
        return {}
    raw = {s["id"]: s["text"].strip() for s in transcript["segments"]}
    return {
        row["seg_id"]: row
        for row in clean["lines"]
        if row["text"].strip() and raw.get(row["seg_id"]) == row["raw"].strip()
    }


def _clean_span(span: dict, order: list[str], lines: dict) -> dict:
    """引用を清書にする。原文は `quote_raw` に残す。1 行でも清書が無ければ元のまま。"""
    first, last = span["segs"]
    if first not in order or last not in order:
        return span
    ids = order[order.index(first) : order.index(last) + 1]
    if not all(i in lines for i in ids):
        return span
    return {**span, "quote": "".join(lines[i]["text"] for i in ids), "quote_raw": span["quote"]}


def clean_items(items: list[dict], koza_id: str, transcript: dict, lines: dict) -> list[dict]:
    """画面に渡す項目の引用を清書にする。正本は書き換えない。別の高座の項目は変えない。"""
    order = [s["id"] for s in transcript["segments"]]
    out = []
    for item in items:
        row = item
        # 保留の引用は照合に通らなかった物。清書にすると直って見えるので、文字起こしのまま見せる
        if (
            lines
            and not item.get("held")
            and item["source"].get("koza_id") == koza_id
            and item["source"].get("segs")
        ):
            row = {**item, "source": _clean_span(item["source"], order, lines)}
            if "payoff" in item:
                row["payoff"] = _clean_span(item["payoff"], order, lines)
        out.append(row)
    return out


def clean_transcript(transcript: dict, lines: dict) -> dict:
    """本文を清書に置き換えた文字起こし。画面の台詞を作るためだけに使う。"""
    return {
        **transcript,
        "segments": [
            {**s, "text": lines[s["id"]]["text"]} if s["id"] in lines else s for s in transcript["segments"]
        ],
    }


def readable_question(question: str | None, before: str) -> str:
    """問いが引いた語が清書にそのままあれば、問いを残す。無ければ決まった文にする。

    問いは文字起こしの語を引いて書かれるので、聞き違いの語を画面に出さないように確かめる。
    """
    if question and all(q in before for q in re.findall(r"[「『](.+?)[」』]", question)):
        return question
    return gaps.FALLBACK_QUESTION


def clean_gaps(gaps_doc: dict | None, transcript: dict, lines: dict) -> dict | None:
    """読めなかった笑いの問いと直前の台詞を、清書で見せる。"""
    if not gaps_doc or not lines:
        return gaps_doc
    overlay = clean_transcript(transcript, lines)
    rows = []
    for s in gaps_doc["surprises"]:
        before = gaps.before_quote(overlay, s["t0"])
        rows.append({**s, "question": readable_question(s.get("question"), before), "before_quote": before})
    return {**gaps_doc, "surprises": rows}


def clean_questions(questions: dict | None, cleaned: dict | None) -> dict | None:
    """笑いへの問いの文を、清書で読める文にする。答えと id はそのまま。"""
    if not questions or not cleaned:
        return questions
    texts: dict[str, str] = {}
    for s in cleaned["surprises"]:
        head = gaps.attempt_of(s, cleaned["koza_id"])
        q = next((x for x in questions["questions"] if x["text"].startswith(head)), None)
        if q is not None:
            texts[q["id"]] = f"{gaps.clock(s['t0'])} の笑いの所：{s['question']}"
    rows = [
        {**q, "text": texts[q["id"]], "text_raw": q["text"]} if q["id"] in texts else q
        for q in questions["questions"]
    ]
    return {**questions, "questions": rows}


def clean_review(review: dict | None, questions: dict | None, shown: dict | None) -> dict | None:
    """笑いへの問いから来た指摘だけ、画面の問いと同じ文にする。ほかの指摘は変えない。"""
    if not review or not questions or not shown:
        return review
    texts = {q["issue_id"]: q["text"] for q in shown["questions"] if "text_raw" in q}
    rows = [{**i, "what_is_missing": texts[i["id"]]} if i["id"] in texts else i for i in review["issues"]]
    return {**review, "issues": rows}


def compare_view(neta: store.Neta, kdir: Path) -> dict | None:
    """2 本の比較と、2 本目の波。2 本目が消えていても比較は見せる。"""
    doc = _optional(kdir / "compare.json", "compare")
    if doc is None:
        return None
    other = neta.sibling(doc["other_neta"]).koza_dir(doc["koza_b"]) / "reactions.json"
    return {**doc, "other_reactions": _optional(other, "reactions")}


def payload(neta: store.Neta, koza_id: str) -> dict:
    """1 つの高座を見るのに要る物を 1 つの JSON にまとめる。"""
    kdir = neta.koza_dir(koza_id)
    # 正本は演目全体の物。別の高座が出所の項目も場面を引けるように、全高座の timeline を読む
    lookup = render.scene_lookup(_timelines(neta, koza_id))
    hanashi = store.read(neta.hanashi, "book")
    kata = store.read(neta.kata, "book")
    note = kdir / "note.md"
    gaps_doc = _optional(kdir / "gaps.json", "gaps")
    clean = _optional(kdir / "seisho.json", "seisho")
    questions = _optional(neta.questions, "questions")
    transcript = _optional(kdir / "transcript.json", "transcript")
    lines = clean_lines(transcript, clean)
    places = question_places(gaps_doc, questions, transcript, clean) if transcript else {}
    empty = {"segments": []}
    cleaned = clean_gaps(gaps_doc, transcript or empty, lines)
    shown = clean_questions(questions, cleaned if lines else None)
    return {
        "koza": store.read(kdir / "koza.json", "koza"),
        "reactions": store.read(kdir / "reactions.json", "reactions"),
        "candidates": _optional(kdir / "note_candidates.json", "note_candidates"),
        "gaps": cleaned,
        "seisho": clean,
        "note": note.read_text(encoding="utf-8") if note.exists() else None,
        "scenes": sorted(hanashi["scenes"], key=lambda s: s["order"]),
        "characters": hanashi["characters"],
        "hanashi": clean_items(_with_scene(hanashi["items"], lookup), koza_id, transcript or empty, lines),
        "kata": clean_items(_with_scene(kata["items"], lookup), koza_id, transcript or empty, lines),
        "guide": _optional(neta.guide, "guide"),
        "review": clean_review(_optional(neta.review, "review"), questions, shown),
        "questions": shown,
        "recheck": _optional(neta.recheck, "recheck"),
        "places": places,
        "compare": compare_view(neta, kdir),
        "run": _optional(kdir / "run.json", "run"),
    }


def write(neta: store.Neta, koza_id: str, out: Path = OUT) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "view.json"
    path.unlink(missing_ok=True)  # 失敗したとき、前の高座の画面を残さない
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload(neta, koza_id), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    shutil.copy2(VIEWER, out / "index.html")  # 配信は out/view だけ（repo 全体を出さない）
    return path
