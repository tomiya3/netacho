"""流れ。順番を決めるのはスクリプトで、Claude は役として 1 つずつ呼ばれるだけ。"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import candidates, compare, evidence, gaps, guide, merge, render, review, seisho, store, view
from .budget import Budget
from .runner import RoleCache, Usage, call_role
from .schema import check_refs


@dataclass
class Context:
    neta: store.Neta
    koza: dict
    transcript: dict
    client: object
    budget: Budget
    usage: Usage
    cache: RoleCache
    hanashi: dict
    kata: dict
    dropped: Counter = field(default_factory=Counter)
    kept: int = 0
    checker: Counter = field(default_factory=Counter)
    deshi_dropped: int = 0
    asked_scenes: set = field(default_factory=set)


def load_books(neta: store.Neta) -> tuple[dict, dict]:
    hanashi = (
        store.read(neta.hanashi, "book")
        if neta.hanashi.exists()
        else merge.empty_book("hanashi", neta.performer, neta.neta)
    )
    kata = (
        store.read(neta.kata, "book")
        if neta.kata.exists()
        else merge.empty_book("kata", neta.performer, neta.neta)
    )
    return hanashi, kata


def koza_ids(neta: store.Neta) -> set[str]:
    return {p.parent.name for p in (neta.base / "koza").glob("*/koza.json")}


def timelines(neta: store.Neta) -> list[dict]:
    return [store.read(p, "timeline") for p in sorted((neta.base / "koza").glob("*/timeline.json"))]


def transcript_text(transcript: dict) -> str:
    """区間 ID と間の秒数つきの文字起こし。"""
    lines = []
    for s in transcript["segments"]:
        pause = s["pause_after"] or 0
        tail = ""
        if evidence.is_blank(pause):  # 間と書かせない
            tail = f"（空白 {pause:.1f} 秒）"  # 原因は書かない（反応を見せないため）
        elif pause >= evidence.MIN_PAUSE_SEC:
            tail = f"（間 {pause:.1f} 秒）"
        lines.append(f"{s['id']} {s['text']}{tail}")
    return "\n".join(lines)


def _existing(hanashi: dict, kata: dict) -> dict:
    """構成作家役に見せる既存の噺・型。id と文だけ。"""

    def rows(book: dict) -> list[dict]:
        keys = ("id", "kind", "text", "edited_by")
        return [{k: i[k] for k in keys} | {"book": book["book"]} for i in book["items"]]

    return {
        "scenes": [{k: s[k] for k in ("id", "title", "order")} for s in hanashi["scenes"]],
        "characters": hanashi["characters"],
        "items": rows(hanashi) + rows(kata),
    }


def writer_content(ctx: Context, rework: list[dict] | None) -> list[dict]:
    """構成作家役の入力。transcript を先頭に置いて cache_control を付ける。反応は見せない。"""
    rest = {
        "演目": ctx.koza["neta"],
        "既存の噺・型": _existing(ctx.hanashi, ctx.kata),
        "差し戻し": rework or [],
    }
    return [
        {
            "type": "text",
            "text": "文字起こし（区間 ID と本文）：\n" + transcript_text(ctx.transcript),
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": json.dumps(rest, ensure_ascii=False, indent=1)},
    ]


def checker_rows(items: list[dict]) -> list[dict]:
    """照合役に見せる物：項目の文と引用だけ。"""
    rows = []
    for it in items:
        row = {"item_id": it["id"], "text": it["text"], "quotes": [it["source"]["quote"]]}
        if "payoff" in it:
            row["quotes"].append(it["payoff"]["quote"])
        rows.append(row)
    return rows


def _check_items(ctx: Context, items: list[dict], draft_name: str) -> set[str]:
    """照合して、保留にする id を返す。判定の返らなかった項目も保留にする（確かめられていないので）。"""
    # 間の根拠は秒数で、引用には書いていない。照合役には確かめられないので、スクリプトの検証だけで通す
    ma = [i for i in items if i["kind"] == "間" and i["basis"] == "heard"]
    heard = [i for i in items if i["basis"] == "heard" and i["kind"] != "間"]
    ctx.checker["ma_by_script"] += len(ma)
    ctx.checker["unchecked_knowledge"] += len(items) - len(heard) - len(ma)
    if not heard:
        return set()
    text = json.dumps(checker_rows(heard), ensure_ascii=False, indent=1)
    doc = call_role(
        ctx.client, "checker", [{"type": "text", "text": text}], "checker", ctx.usage, ctx.budget, ctx.cache
    )
    store.write(ctx.neta.drafts / draft_name, doc, "checker")
    verdicts: dict[str, str] = {}
    severity = {"supported": 0, "weak": 1, "unsupported": 2}
    for v in doc["verdicts"]:  # 同じ項目に複数の判定が返ったら、一番厳しいものを取る
        current = verdicts.get(v["item_id"], "supported")
        verdicts[v["item_id"]] = max(current, v["verdict"], key=severity.__getitem__)
    held = set()
    for it in heard:
        verdict = verdicts.get(it["id"], "no_verdict")
        ctx.checker[verdict] += 1
        if verdict in {"unsupported", "no_verdict"}:
            held.add(it["id"])
    return held


def write_pass(ctx: Context, *, rework: list[dict] | None = None, suffix: str = "") -> set[str]:
    """構成作家役 → 根拠の検証 → 照合役 → マージ → .md。文が変わった場面を返す。"""
    draft = call_role(
        ctx.client,
        "writer",
        writer_content(ctx, rework),
        "writer",
        ctx.usage,
        ctx.budget,
        ctx.cache,
    )
    store.write(ctx.neta.drafts / f"writer{suffix}.json", draft, "writer")
    tool_ids, performer_ids = merge.split_ids(ctx.hanashi, ctx.kata)
    items, dropped = evidence.verify_items(
        draft, ctx.transcript, tool_ids=tool_ids, performer_ids=performer_ids
    )
    scenes, bad_scenes = evidence.verify_scenes(draft, ctx.transcript)
    ctx.dropped.update(dropped)
    if bad_scenes:
        ctx.dropped["scene_bad_seg"] += bad_scenes
    before = {i["id"]: i["text"] for i in ctx.hanashi["items"] + ctx.kata["items"]}
    merge.assign_ids(items, ctx.hanashi, ctx.kata)
    changed = [i for i in items if before.get(i["id"]) != i["text"]]
    held = _check_items(ctx, items, f"checker{suffix}.json")
    merge.merge(
        ctx.hanashi, ctx.kata, items=items, scenes=scenes, characters=draft["characters"], held_ids=held
    )
    save_books(ctx)
    ctx.kept += len(items)  # 正本に入ってから数える
    lookup = render.scene_lookup(timelines(ctx.neta))
    # 保留の文は弟子役に見えない。直ったと数えない
    shown = [i for i in changed if i["id"] not in held and not evidence.has_internal_id(i["text"])]
    return {render.scene_of_item(i, lookup) for i in shown}


def save_books(ctx: Context) -> None:
    """参照を確かめてから、正本、timeline、.md を書く。"""
    kdir = ctx.neta.koza_dir(ctx.koza["id"])
    tl = evidence.timeline(ctx.hanashi["scenes"], ctx.transcript)
    check_refs(
        ctx.hanashi,
        ctx.kata,
        koza=ctx.koza,
        koza_ids=koza_ids(ctx.neta),
        transcript=ctx.transcript,
        timeline=tl,
    )
    store.write(ctx.neta.hanashi, ctx.hanashi, "book")
    store.write(ctx.neta.kata, ctx.kata, "book")
    store.write(kdir / "timeline.json", tl, "timeline")
    write_markdown(ctx.neta, ctx.hanashi, ctx.kata)


def write_markdown(neta: store.Neta, hanashi: dict, kata: dict) -> None:
    lookup = render.scene_lookup(timelines(neta))
    neta.hanashi.with_suffix(".md").write_text(render.to_markdown(hanashi, lookup), encoding="utf-8")
    neta.kata.with_suffix(".md").write_text(render.to_markdown(kata, lookup), encoding="utf-8")


def _context(
    performer: str,
    neta_name: str,
    koza_id: str,
    *,
    client,
    budget: Budget | None,
    reuse: bool,
    root: Path | None,
) -> Context:
    neta = store.Neta(performer, neta_name, root)
    kdir = neta.koza_dir(koza_id)
    koza = store.read(kdir / "koza.json", "koza")
    transcript = store.read(kdir / "transcript.json", "transcript")
    hanashi, kata = load_books(neta)
    run_path = kdir / "run.json"
    usage = Usage(run_path, {"schema_version": 1, "koza_id": koza_id})
    if run_path.exists():  # 費用の実測は作り直しても消さない。キャッシュに当たった分は足さない
        usage.restore(store.read(run_path, "run"))
    cache = RoleCache((root or store.data_root()) / ".cache" / "roles", reuse=reuse)
    return Context(neta, koza, transcript, client, budget or Budget(), usage, cache, hanashi, kata)


def _finish(ctx: Context) -> dict:
    """run.json を書いて、数字を返す。"""
    ctx.usage.extra |= {
        "evidence": {
            "kept": ctx.kept,
            "dropped": dict(ctx.dropped),
            "held_internal_id": sum(
                i.get("held_reason") == "internal_id" for i in ctx.hanashi["items"] + ctx.kata["items"]
            ),
        },
        "checker": {
            k: ctx.checker[k]
            for k in ("supported", "weak", "unsupported", "unchecked_knowledge", "no_verdict", "ma_by_script")
        },
    }
    doc = ctx.usage.to_json()
    store.write(ctx.neta.koza_dir(ctx.koza["id"]) / "run.json", doc, "run")
    return doc


def deshi_content(neta: store.Neta) -> list[dict]:
    """弟子役に見せる物：確定した噺・型の .md だけ。"""
    hanashi = neta.hanashi.with_suffix(".md").read_text(encoding="utf-8")
    kata = neta.kata.with_suffix(".md").read_text(encoding="utf-8")
    return [{"type": "text", "text": hanashi + "\n" + kata}]


def read_review(neta: store.Neta) -> tuple[dict, dict]:
    doc = (
        store.read(neta.review, "review")
        if neta.review.exists()
        else review.empty_review(neta.performer, neta.neta)
    )
    questions = (
        store.read(neta.questions, "questions")
        if neta.questions.exists()
        else review.empty_questions(neta.performer, neta.neta)
    )
    return doc, questions


def deshi_pass(ctx: Context, doc: dict, questions: dict, round_no: int) -> tuple[list[dict], int]:
    """弟子役を 1 回呼び、指摘を受け取り、質問を足す。(今回の指摘, 落とした数)。"""
    raw = call_role(ctx.client, "deshi", deshi_content(ctx.neta), "deshi", ctx.usage, ctx.budget, ctx.cache)
    store.write(ctx.neta.drafts / f"deshi-{round_no}.json", raw, "deshi")
    current, dropped = review.receive(raw, doc, ctx.hanashi, ctx.kata, round_no)
    lookup = render.scene_lookup(timelines(ctx.neta))
    no_scene, asked, skipped = review.add_questions(
        current, questions, ctx.hanashi, ctx.kata, lookup, skip_scenes=ctx.asked_scenes
    )
    ctx.asked_scenes |= asked
    dropped += no_scene
    doc["issues"] = [i for i in doc["issues"] if i["id"] not in skipped]  # 答える先の無い指摘を残さない
    current = [i for i in current if i["id"] not in skipped]
    ctx.deshi_dropped += dropped
    return current, dropped


def save_review(ctx: Context, doc: dict, questions: dict) -> None:
    doc.pop("suppressed_answered", None)  # 数は run.json に出す。review.json には入れない
    check_refs(
        ctx.hanashi, ctx.kata, koza=ctx.koza, koza_ids=koza_ids(ctx.neta), review=doc, questions=questions
    )
    store.write(ctx.neta.review, doc, "review")
    store.write(ctx.neta.questions, questions, "questions")


def build(
    performer: str,
    neta_name: str,
    koza_id: str,
    *,
    client,
    budget: Budget | None = None,
    reuse: bool = False,
    root: Path | None = None,
) -> dict:
    """噺・型を作り、弟子役に読ませ、差し戻しを 1 回だけ反映する。返すのは run.json の中身。"""
    ctx = _context(performer, neta_name, koza_id, client=client, budget=budget, reuse=reuse, root=root)
    doc, questions = read_review(ctx.neta)
    ctx.asked_scenes |= settled_scenes(questions)
    base_round = max((i["round"] for i in doc["issues"]), default=0)

    def record(first: list[dict], second: list[dict] | None) -> None:
        ctx.usage.extra["deshi"] = {
            "round1": len(first),
            "round2": None if second is None else len(second),
            "dropped_no_anchor": ctx.deshi_dropped,
        }
        ctx.usage.extra["questions"] = len(questions["questions"])

    try:
        write_pass(ctx)
        first, _ = deshi_pass(ctx, doc, questions, base_round + 1)
        save_review(ctx, doc, questions)
        record(first, None)
        rework = review.rework_of(first) + merge.id_rework(ctx.hanashi, ctx.kata)
        if rework:
            touched = write_pass(ctx, rework=rework, suffix="-2")
            second, dropped = deshi_pass(ctx, doc, questions, base_round + 2)
            review.settle_rework(
                first,
                second,
                items={i["id"]: i for i in ctx.hanashi["items"] + ctx.kata["items"]},
                lookup=render.scene_lookup(timelines(ctx.neta)),
                touched_scenes=touched,
                second_dropped=dropped,
            )
            save_review(ctx, doc, questions)
            record(first, second)
    finally:  # 落ちても、それまでの数字を残す
        result = _finish(ctx)
    return result


def settled_scenes(questions: dict) -> set[str]:
    """演者が答えたか捨てた問いの場面。同じ場面を言い方を変えて問い直さない。"""
    return {q["scene_id"] for q in questions["questions"] if "answer" in q or "dismissed" in q}


def after_performer(neta: store.Neta, *, first_card: bool = False) -> None:
    """演者が決めた後の更新。指摘が解決になると札が変わる。CLI と画面で同じにする。

    `first_card` は札がまだ無くても作る。答えだけでは札を作らない。
    """
    write_markdown(neta, store.read(neta.hanashi, "book"), store.read(neta.kata, "book"))
    if first_card:
        guide.write(neta)
    else:
        guide.refresh(neta)


def reread(
    performer: str,
    neta_name: str,
    koza_id: str,
    *,
    client,
    budget: Budget | None = None,
    reuse: bool = False,
    root: Path | None = None,
) -> list[dict]:
    """演者の判断が入った型を、弟子役にもう一度読ませる。足した指摘を返す。"""
    ctx = _context(performer, neta_name, koza_id, client=client, budget=budget, reuse=reuse, root=root)
    run_path = ctx.neta.koza_dir(koza_id) / "run.json"
    write_markdown(ctx.neta, ctx.hanashi, ctx.kata)
    doc, questions = read_review(ctx.neta)
    ctx.asked_scenes |= settled_scenes(questions)
    try:
        current, _ = deshi_pass(ctx, doc, questions, max((i["round"] for i in doc["issues"]), default=0) + 1)
        suppressed = doc.pop("suppressed_answered", 0)
        save_review(ctx, doc, questions)
        stats = ctx.usage.extra.get("deshi", {"round1": 0, "round2": None, "dropped_no_anchor": 0})
        dropped = stats["dropped_no_anchor"] + ctx.deshi_dropped
        ctx.usage.extra["deshi"] = {
            **stats,
            "dropped_no_anchor": dropped,
            "reread": len(current),
            "suppressed_answered": suppressed,
        }
        ctx.usage.extra["questions"] = len(questions["questions"])
    finally:  # 落ちても、呼んだ分の費用を残す
        store.write(run_path, ctx.usage.to_json(), "run")
    guide.refresh(ctx.neta)  # 指摘が増えて札から外れる項目があれば反映する（LLM 0 回）
    return current


# ノート役の文の検査。断定、客の気持ち、直し方の提案、数字の羅列を通さない。
# 「反応候補」の言い方は約束の 3 つだけ許し、それ以外の「反応…ない」は落とす
NOTE_ALLOWED = (
    "反応候補が少な",
    "反応候補が小さ",
    "反応候補が見つからな",
    "反応候補が見つかりません",
    "客いじり",
)
QUESTION_ALLOWED = ("笑いどころと読めませんでした", "笑いどころと読めなかった")  # 演者への問いの言い方
NOTE_BANNED = re.compile(
    r"(反応|候補|笑い|ウケ|受け|手応え)[^。]{0,8}"
    r"(なし|無し|なかった|無かった|ない|無い|ません|起きな|起こらな|薄|弱|出な)"
    r"|無反応|沈黙|静か|シーン|滑っ|滑り|スベ|盛り上がらな|失敗|ダメ|だめ|客|音量"
    r"|した方が|すべき|おすすめ|お勧め|てはどう|てみては|直すと|変えると"
    r"|[0-9〇一二三四五六七八九十百]+\s*(秒|分|回|件|位)"
)
NOTE_MAX_SENTENCES = 2
AFTER_QUOTE_MAX = 120
NOTE_FALLBACK = {
    "no_candidate": "回収の後に、反応候補が少ない所です。",
    "low_rank": "回収の後の反応候補が、この高座の中では小さい方です。",
}


def noter_rows(cands: dict, items: dict, transcript: dict, hanashi: dict, tl: dict) -> list[dict]:
    """ノート役に見せる物：選ばれた候補、前後の引用、場面の尺だけ。"""
    scenes = {s["id"]: s for s in hanashi["scenes"]}
    scene_of = {s["seg_id"]: s["scene_id"] for s in tl["segments"]}
    rows = []
    for c in cands["candidates"]:
        it = items[c["item_id"]]
        t0, t1 = c["window"]
        after = "".join(s["text"] for s in transcript["segments"] if t0 <= s["start"] < t1)[:AFTER_QUOTE_MAX]
        row = {
            "candidate_id": c["id"],
            "kind": it["kind"],
            "item": it["text"],
            "why": {
                "no_candidate": "回収の後に反応候補が見つからない",
                "low_rank": "回収の後の反応候補が小さい方",
            }[c["why"]],
            "setup_quote": it["source"]["quote"],
            "payoff_quote": it["payoff"]["quote"],
            "after_quote": after,
        }
        scene_id = scene_of.get(it["payoff"]["segs"][0])
        part = [s for s in transcript["segments"] if scene_of.get(s["id"]) == scene_id]
        if scene_id in scenes and part:  # 尺はこの高座の timeline から出す（別の高座でも同じ場面 ID なので）
            row["scene"] = scenes[scene_id]["title"]
            row["scene_seconds"] = round(part[-1]["end"] - part[0]["start"])
        rows.append(row)
    return rows


def note_is_ok(text: str, allowed: tuple[str, ...] = NOTE_ALLOWED) -> bool:
    plain = unicodedata.normalize("NFKC", text).strip()
    if not plain or len(re.findall(r"[。！？!?]", plain)) > NOTE_MAX_SENTENCES:
        return False
    for phrase in allowed:
        plain = plain.replace(phrase, "")
    return NOTE_BANNED.search(plain) is None


def surprise_rows(found: dict) -> list[dict]:
    """予測なしで反応ありの笑い。ノート役に見せるのは直前の台詞だけ。"""
    return [
        {"candidate_id": s["id"], "kind": "予測なしで反応あり", "before_quote": s["before_quote"]}
        for s in found["surprises"]
    ]


def checked_questions(doc: dict | None, found: dict) -> None:
    """問いの文を入れる。無い、重なる、検査に落ちるものは決まった文にする。"""
    written: dict[str, list[str]] = {}
    for n in (doc or {"notes": []})["notes"]:
        written.setdefault(n["candidate_id"], []).append(n["text"].strip())
    for s in found["surprises"]:
        texts = written.get(s["id"], [])
        ok = len(texts) == 1 and note_is_ok(texts[0], QUESTION_ALLOWED)
        s["question"] = texts[0] if ok else gaps.FALLBACK_QUESTION
        s["question_source"] = "claude" if ok else "fixed"


def checked_notes(doc: dict | None, cands: dict) -> tuple[dict[str, str], set[str]]:
    """候補ごとに 1 件の文を返す。無い、重なる、検査に落ちるものは決まった文にする。

    返すのは (文, 決まった文にした候補の ID)。
    """
    written: dict[str, list[str]] = {}
    for n in (doc or {"notes": []})["notes"]:
        written.setdefault(n["candidate_id"], []).append(n["text"].strip())
    out: dict[str, str] = {}
    fixed: set[str] = set()
    for c in cands["candidates"]:
        texts = written.get(c["id"], [])
        if len(texts) == 1 and note_is_ok(texts[0]):
            out[c["id"]] = texts[0]
        else:
            out[c["id"]] = NOTE_FALLBACK[c["why"]]
            fixed.add(c["id"])
    return out, fixed


def note(
    performer: str,
    neta_name: str,
    koza_id: str,
    *,
    client,
    budget: Budget | None = None,
    reuse: bool = False,
    root: Path | None = None,
) -> dict:
    """候補を選び、ノート役が文にして、note.md を書く。返すのは run.json の中身。"""
    ctx = _context(performer, neta_name, koza_id, client=client, budget=budget, reuse=reuse, root=root)
    kdir = ctx.neta.koza_dir(koza_id)
    run_path = kdir / "run.json"
    if run_path.exists():
        ctx.usage.restore(store.read(run_path, "run"))  # build の分に積み足す
    stale_files = ("note.md", "sokki.md", "note_candidates.json", "gaps.json")
    for stale in (*(kdir / n for n in stale_files), ctx.neta.drafts / "noter.json"):  # 古い文を残さない
        stale.unlink(missing_ok=True)
    reactions = store.read(kdir / "reactions.json", "reactions")
    (kdir / "sokki.md").write_text(
        render.sokki_markdown(ctx.koza, ctx.transcript, reactions), encoding="utf-8"
    )
    tl = store.read(kdir / "timeline.json", "timeline")
    all_items = ctx.hanashi["items"] + ctx.kata["items"]
    cands = candidates.select(all_items, reactions)
    check_refs(
        ctx.hanashi,
        ctx.kata,
        koza=ctx.koza,
        koza_ids=koza_ids(ctx.neta),
        transcript=ctx.transcript,
        timeline=tl,
        reactions=reactions,
        note_candidates=cands,
    )
    items = {i["id"]: i for i in all_items}
    found = gaps.compare(all_items, reactions, ctx.transcript, tl)
    doc = None
    rows = noter_rows(cands, items, ctx.transcript, ctx.hanashi, tl) + surprise_rows(found)
    if rows:
        text = json.dumps(rows, ensure_ascii=False, indent=1)
        content = [{"type": "text", "text": text}]
        try:
            doc = call_role(ctx.client, "noter", content, "noter", ctx.usage, ctx.budget, ctx.cache)
        except Exception:
            store.write(run_path, ctx.usage.to_json(), "run")  # 費用は実測として残す
            raise
        store.write(ctx.neta.drafts / "noter.json", doc, "noter")
    notes, fixed = checked_notes(doc, cands)
    checked_questions(doc, found)
    store.write(kdir / "gaps.json", found, "gaps")
    review_doc, questions = read_review(ctx.neta)
    gaps.add_questions(found, review_doc, questions)
    save_review(ctx, review_doc, questions)
    for c in cands["candidates"]:
        c["note"] = notes[c["id"]]
        c["note_source"] = "fixed" if c["id"] in fixed else "claude"
    store.write(kdir / "note_candidates.json", cands, "note_candidates")  # 文が揃ってから 1 回だけ書く
    scene_of = {s["seg_id"]: s["scene_id"] for s in tl["segments"]}
    titles = {s["id"]: s["title"] for s in ctx.hanashi["scenes"]}
    item_scene = {
        c["item_id"]: titles.get(scene_of.get(items[c["item_id"]]["payoff"]["segs"][0]), "場面不明")
        for c in cands["candidates"]
    }
    (kdir / "note.md").write_text(
        render.note_markdown(ctx.koza, cands, notes, items, item_scene, found), encoding="utf-8"
    )
    ctx.usage.extra["notes"] = {"candidates": len(cands["candidates"]), "fallback": len(fixed)}
    ctx.usage.extra["gaps"] = gaps.counts(found)
    ctx.usage.extra["questions"] = len(questions["questions"])
    result = ctx.usage.to_json()
    store.write(run_path, result, "run")
    return result


COMPARE_QUESTIONS = 5  # 2 本の比較で演者に問う数。多いと答えてもらえない
FIXED_ASK = {
    "kept": "ここ、2 本とも同じ言葉で、同じ所に来てますね。ここは変えない所ですか？",
    "moved": "ここ、来る所が 2 本でずれてますね。わざとですか？",
    "ma": "ここ、一言の前の間が 2 本で違いますね。わざとですか？",
    "one_side": "ここ、片方の録音でだけ拾えています。何か変えましたか？",
    "words": "ここ、言い方を変えてますね。わざとですか？",
}


def compare_side(neta: store.Neta, koza_id: str) -> compare.Side:
    """比べる 1 本を読む。言葉の比較は清書どうしで行うので、清書が無ければ止める。"""
    kdir = neta.koza_dir(koza_id)
    if not (kdir / "seisho.json").exists():
        raise store.StoreError(f"{neta.neta} の {koza_id} に清書が無い。先に netacho seisho を実行する")
    transcript = store.read(kdir / "transcript.json", "transcript")
    lines = view.clean_lines(transcript, store.read(kdir / "seisho.json", "seisho"))
    reactions = store.read(kdir / "reactions.json", "reactions")
    timeline = store.read(kdir / "timeline.json", "timeline")
    return compare.side_of(koza_id, transcript, lines, reactions, timeline)


def ask_rows(spots: list[dict]) -> list[dict]:
    """聞き役に見せる物：所の種類と、2 本の台詞だけ。"""
    return [
        {"spot_id": s["id"], "kind": s["kind"], "quote_a": s["a"]["quote"], "quote_b": s["b"]["quote"]}
        for s in spots
    ]


def ask_is_ok(text: str, spot: dict) -> bool:
    """検査に通る問いか。引いた語が、見せた台詞にそのままあること。"""
    if not note_is_ok(text, ("客いじり",)):
        return False
    return view.readable_question(text, spot["a"]["quote"] + spot["b"]["quote"]) == text


def compare_koza(
    performer: str,
    neta_name: str,
    koza_id: str,
    *,
    other_koza: str,
    other_neta: str | None = None,
    client,
    budget: Budget | None = None,
    reuse: bool = False,
    root: Path | None = None,
) -> dict:
    """同じ演目の 2 本を比べ、選んだ所を演者に問う。問いは 1 本目の演目の帳面に入る。"""
    ctx = _context(performer, neta_name, koza_id, client=client, budget=budget, reuse=reuse, root=root)
    other = store.Neta(performer, other_neta or neta_name, root)
    a = compare_side(ctx.neta, koza_id)
    b = compare_side(other, other_koza)
    titles = {s["id"]: s["title"] for s in ctx.hanashi["scenes"]}
    doc = {**compare.compare(a, b, titles), "other_neta": other.neta}
    kdir = ctx.neta.koza_dir(koza_id)
    fallback = added = 0
    if doc["comparable"]:
        chosen = [s for s in compare.select(doc["spots"], COMPARE_QUESTIONS) if s["scene_id"] in titles]
        text = json.dumps(ask_rows(chosen), ensure_ascii=False, indent=1)
        content = [{"type": "text", "text": text}]
        reply = (
            call_role(ctx.client, "asker", content, "asker", ctx.usage, ctx.budget, ctx.cache)
            if chosen
            else None
        )
        store.write(ctx.neta.drafts / "asker.json", reply or {"schema_version": 1, "questions": []}, "asker")
        written: dict[str, list[str]] = {}
        for row in (reply or {"questions": []})["questions"]:
            written.setdefault(row["spot_id"], []).append(row["text"].strip())
        review_doc, questions = read_review(ctx.neta)
        asked = {i["attempt"] for i in review_doc["issues"]}
        n_issue = max((int(i["id"].split("-")[1]) for i in review_doc["issues"]), default=0) + 1
        n_q = max((int(q["id"].split("-")[1]) for q in questions["questions"]), default=0) + 1
        for spot in chosen:
            attempt = f"{render.clock(spot['a']['t'])} の比較（{koza_id} と {other_koza}）"
            texts = written.get(spot["id"], [])
            ok = len(texts) == 1 and ask_is_ok(texts[0], spot)
            fallback += 0 if ok else 1
            if attempt in asked:
                continue
            issue_id, q_id = f"issue-{n_issue:04d}", f"q-{n_q:04d}"
            words = texts[0] if ok else FIXED_ASK[spot["kind"]]
            review_doc["issues"].append(
                {
                    "id": issue_id,
                    "role": "asker",
                    "kind": "ask_performer",
                    "anchor": {"scene_id": spot["scene_id"]},
                    "attempt": attempt,
                    "what_is_missing": words,
                    "round": 1,
                    "resolved": False,
                }
            )
            questions["questions"].append(
                {
                    "id": q_id,
                    "issue_id": issue_id,
                    "scene_id": spot["scene_id"],
                    "text": words,
                    "spot_id": spot["id"],
                }
            )
            spot["question_id"] = q_id
            n_issue, n_q, added = n_issue + 1, n_q + 1, added + 1
        save_review(ctx, review_doc, questions)
    store.write(kdir / "compare.json", doc, "compare")
    ctx.usage.extra["compare"] = {
        "share": doc["share"],
        "spots": len(doc["spots"]),
        "questions": added,
        "fallback": fallback,
    }
    store.write(kdir / "run.json", ctx.usage.to_json(), "run")
    return doc


SCRIBE_CHUNK = 120  # 1 回に渡す区間の数。40 分の高座でも切れない長さ。暫定


def scribe_content(ctx: Context, segments: list[dict], title: str | None = None) -> list[dict]:
    """速記役に見せる物：文字起こし、演目の題、人物の名前。反応は見せない。

    `koza.neta` はフォルダの名前で、演目の題とは限らない。題が分かると聞き違いを直せる。
    """
    head = {"演目": title or ctx.koza["neta"], "人物": [c["name"] for c in ctx.hanashi["characters"]]}
    lines = [{"seg_id": s["id"], "text": s["text"]} for s in segments if s["text"].strip()]
    return [
        {"type": "text", "text": json.dumps(head, ensure_ascii=False, indent=1)},
        {"type": "text", "text": json.dumps(lines, ensure_ascii=False)},
    ]


def clean_copy(
    performer: str,
    neta_name: str,
    koza_id: str,
    *,
    client,
    budget: Budget | None = None,
    reuse: bool = False,
    root: Path | None = None,
    title: str | None = None,
) -> dict:
    """清書を作る。返すのは run.json の中身。正本には触らない。"""
    ctx = _context(performer, neta_name, koza_id, client=client, budget=budget, reuse=reuse, root=root)
    kdir = ctx.neta.koza_dir(koza_id)
    run_path = kdir / "run.json"
    segs = ctx.transcript["segments"]
    lines: list[dict] = []
    try:
        # 1 回の出力が max_tokens で切れないように、区間を分けて呼ぶ
        for i in range(0, len(segs), SCRIBE_CHUNK):
            chunk = segs[i : i + SCRIBE_CHUNK]
            if not any(s["text"].strip() for s in chunk):
                continue
            content = scribe_content(ctx, chunk, title)
            part = call_role(ctx.client, "scribe", content, "scribe", ctx.usage, ctx.budget, ctx.cache)
            lines += part["lines"]
    except Exception:
        store.write(run_path, ctx.usage.to_json(), "run")  # 費用は実測として残す
        raise
    draft = {"schema_version": 1, "lines": lines}
    store.write(ctx.neta.drafts / "scribe.json", draft, "scribe")
    reactions_path = kdir / "reactions.json"
    reactions = store.read(reactions_path, "reactions") if reactions_path.exists() else None
    names = {c["name"] for c in ctx.hanashi["characters"]}
    doc, stats = seisho.build(draft, ctx.transcript, reactions, names)
    store.write(kdir / "seisho.json", doc, "seisho")
    (kdir / "seisho.md").write_text(seisho.markdown(ctx.koza, doc, stats), encoding="utf-8")
    ctx.usage.extra["seisho"] = {**stats, "title": title} if title else stats
    result = ctx.usage.to_json()
    store.write(run_path, result, "run")
    return result
