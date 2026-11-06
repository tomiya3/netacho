"""視聴画面に渡す JSON を書き出す。LLM は呼ばない。録音は書き出さない。"""

from __future__ import annotations

import difflib
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
LINE_MIN, LINE_BACK, LINE_JOIN = 4, 4, 2  # 笑いの一覧の台詞。短い相づちを飛ばし、さかのぼる行数とつなぐ行数


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


def _clean_fragment(quote: str, raw: str, text: str) -> str:
    """文字起こしの 1 行の一部 `quote` に当たる、清書の部分を返す。"""
    start = raw.find(quote)
    end = start + len(quote)
    head = tail = None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, raw, text, autojunk=False).get_opcodes():
        if head is None and i1 <= start < i2:
            head = j1 + (start - i1) if tag == "equal" else j1
        if i1 < end <= i2:
            tail = j1 + (end - i1) if tag == "equal" else j2
    return text[head:tail] if head is not None and tail is not None else quote


def clean_quotes(text: str, lines: dict) -> str:
    """Claude が文字起こしから引いた「」の中を、清書の字にする。見せる時だけで、正本は変えない。"""

    def swap(match: re.Match) -> str:
        quote = match[1]
        found = {_clean_fragment(quote, r["raw"], r["text"]) for r in lines.values() if quote in r["raw"]}
        # 短い引用が別々の字の行に当たるなら、どの行か決められない。清書で語が消えた引用も空にしない
        shown = found.pop() if len(found) == 1 else ""
        return f"「{shown}」" if shown.strip() else match[0]

    return re.sub(r"「([^「」]+)」", swap, text)


def clean_recheck(recheck: dict | None, lines: dict) -> dict | None:
    """読み直し役の引用と返事の字を、清書にする。見せる時だけで、正本は変えない。"""
    if not recheck or not lines:
        return recheck

    def side(row: dict | None) -> dict | None:
        if not row:
            return row
        return {
            **row,
            "quote": clean_quotes(f"「{row['quote']}」", lines)[1:-1],
            "reply": clean_quotes(row["reply"], lines),
        }

    def row_of(r: dict) -> dict:
        after = side(r["after"])
        if after and r["cites_answer"]:  # 引用は演者の答えの言葉なので、字を変えない
            after["quote"] = r["after"]["quote"]
        return {**r, "before": side(r["before"]), "after": after}

    rows = [row_of(r) for r in recheck["rows"]]
    return {**recheck, "rows": rows}


def clean_notes(candidates: dict | None, lines: dict) -> dict | None:
    """ノートの文の引用を、清書の字で見せる。ノート役は文字起こしを読んで書くので。"""
    if not candidates or not lines:
        return candidates
    rows = [
        {**c, "note": clean_quotes(c["note"], lines)} if c.get("note") else c
        for c in candidates["candidates"]
    ]
    return {**candidates, "candidates": rows}


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


def _run_title(kdir: Path) -> str | None:
    """清書のときに渡した演目の題。形が古くても、高座を一覧から落とさないように検証しない。"""
    try:
        run = json.loads((kdir / "run.json").read_text(encoding="utf-8"))
        return (run.get("seisho") or {}).get("title")
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def koza_list(neta: store.Neta) -> list[dict]:
    """この演者の高座の一覧。新しい順。作りかけの高座は読み飛ばす。"""
    rows = []
    for path in neta.base.parent.glob("*/koza/*/koza.json"):
        kdir = path.parent
        try:
            other = neta.sibling(path.parents[2].name)
            koza = store.read(path, "koza")
            # 答えるたびに全部の高座を読むので、要らない検証をしない
            scores = json.loads((kdir / "reactions.json").read_text(encoding="utf-8")).get("scores") or {}
            title = _run_title(kdir)
            questions = _optional(other.questions, "questions") or {"questions": []}
            row = {
                "neta": other.neta,
                "koza_id": koza["id"],
                "title": title or other.neta,
                "date": koza["date"],
                "venue": koza["venue"],
                "seconds": scores.get("seconds"),
                "open": sum(1 for q in questions["questions"] if "answer" not in q and "dismissed" not in q),
            }
        except (ValueError, OSError, AttributeError, KeyError, TypeError):  # 1 本の壊れで一覧を止めない
            print(f"読み飛ばした：{kdir.name} の高座", file=sys.stderr)
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["koza_id"])
    return sorted(rows, key=lambda r: r["date"], reverse=True)


def laugh_lines(reactions: dict, transcript: dict | None, lines: dict) -> dict:
    """笑いごとの、直前の清書の台詞。清書の無い高座には出さない。

    1 区間は短い切れ端のことが多いので、相づちを飛ばして 2 行までつなぐ。
    """
    if not transcript or not lines:
        return {}
    segs = sorted((s for s in transcript["segments"] if s["text"].strip()), key=lambda s: s["start"])
    found = {}
    for r in reactions["reactions"]:
        if r["kind"] != "laugh":
            continue
        before = [lines[s["id"]] for s in segs if s["start"] < r["t0"] and s["id"] in lines][-LINE_BACK:]
        rows = [row for row in before if len(row["text"].strip()) >= LINE_MIN][-LINE_JOIN:]
        if rows:
            text = "　".join(row["text"].strip() for row in rows)
            found[r["id"]] = {"text": text, "speaker": rows[-1].get("speaker")}
    return found


def scene_times(scenes: list[dict], transcript: dict | None, timeline: dict | None) -> list[dict]:
    """場面に、この高座の中の時刻を付ける。波の帯に使う。この高座に無い場面は時刻なしのまま。"""
    if not transcript or not timeline:
        return scenes
    spans = {seg["id"]: (seg["start"], seg["end"]) for seg in transcript["segments"]}
    found: dict[str, list[float]] = {}
    for row in timeline["segments"]:
        span = spans.get(row["seg_id"])
        if span is not None and row["scene_id"] is not None:
            found.setdefault(row["scene_id"], []).extend(span)
    timed = {key: {"t0": min(values), "t1": max(values)} for key, values in found.items()}
    return [{**s, **timed.get(s["id"], {})} for s in scenes]


def compare_view(neta: store.Neta, kdir: Path) -> dict | None:
    """2 本の比較と、2 本目の波。2 本目が消えていても比較は見せる。"""
    doc = _optional(kdir / "compare.json", "compare")
    if doc is None:
        return None
    other = neta.sibling(doc["other_neta"]).koza_dir(doc["koza_b"]) / "reactions.json"
    return {
        **doc,
        "neta_a": neta.neta,
        "neta_b": doc["other_neta"],
        "other_reactions": _optional(other, "reactions"),
    }


def compares_from_others(neta: store.Neta, koza_id: str) -> list[dict]:
    """別の高座の側で作った、この高座との比較。開いている高座を変えずに見られるように。"""
    rows = []
    for path in sorted(neta.base.parent.glob("*/koza/*/compare.json")):
        try:
            doc = store.read(path, "compare")
            if doc["koza_b"] == koza_id and doc["other_neta"] == neta.neta:
                owner = path.parents[2].name
                other = _optional(path.parent / "reactions.json", "reactions")
                rows.append({**doc, "neta_a": owner, "neta_b": neta.neta, "other_reactions": other})
        except (ValueError, OSError, KeyError):
            print(f"読み飛ばした：{path.parent.name} の比較", file=sys.stderr)
    return rows


def book_items(book: dict, lookup: dict, koza_id: str, transcript: dict, lines: dict) -> list[dict]:
    """帳面の項目を、清書の字で見せる。演者の言葉と、照合に通らなかった保留の項目はそのまま。"""
    rows = clean_items(_with_scene(book["items"], lookup), koza_id, transcript, lines)
    keep = lambda i: i.get("edited_by") == "performer" or i.get("held")  # noqa: E731
    return [i if keep(i) else {**i, "text": clean_quotes(i["text"], lines)} for i in rows]


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
    reactions = store.read(kdir / "reactions.json", "reactions")
    return {
        "koza": store.read(kdir / "koza.json", "koza"),
        "reactions": reactions,
        "laugh_lines": laugh_lines(reactions, transcript, lines),
        "neta": neta.neta,
        "kozas": koza_list(neta),
        "candidates": clean_notes(_optional(kdir / "note_candidates.json", "note_candidates"), lines),
        "gaps": cleaned,
        "seisho": clean,
        "note": note.read_text(encoding="utf-8") if note.exists() else None,
        "scenes": scene_times(
            sorted(hanashi["scenes"], key=lambda s: s["order"]),
            transcript,
            _optional(kdir / "timeline.json", "timeline"),
        ),
        "characters": hanashi["characters"],
        "hanashi": book_items(hanashi, lookup, koza_id, transcript or empty, lines),
        "kata": book_items(kata, lookup, koza_id, transcript or empty, lines),
        "guide": _optional(neta.guide, "guide"),
        "review": clean_review(_optional(neta.review, "review"), questions, shown),
        "questions": shown,
        "recheck": clean_recheck(recheck, lines)
        if (recheck := _optional(neta.recheck, "recheck")) and recheck.get("koza_id") == koza_id
        else None,
        "places": places,
        "compare": compare_view(neta, kdir),
        "compares": compares_from_others(neta, koza_id),
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
