"""流れ。順番を決めるのはスクリプトで、Claude は役として 1 つずつ呼ばれるだけ。"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import candidates, evidence, guide, merge, render, review, store
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
        pause = s["pause_after"]
        tail = f"（間 {pause:.1f} 秒）" if pause is not None and pause >= evidence.MIN_PAUSE_SEC else ""
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
    heard = [i for i in items if i["basis"] == "heard"]
    ctx.checker["unchecked_knowledge"] += len(items) - len(heard)
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
    return {render.scene_of_item(i, lookup) for i in changed if i["id"] not in held}


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
        "evidence": {"kept": ctx.kept, "dropped": dict(ctx.dropped)},
        "checker": {
            k: ctx.checker[k]
            for k in ("supported", "weak", "unsupported", "unchecked_knowledge", "no_verdict")
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
        rework = review.rework_of(first)
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
    ctx.asked_scenes |= {q["scene_id"] for q in questions["questions"] if "answer" in q}
    current, _ = deshi_pass(ctx, doc, questions, max((i["round"] for i in doc["issues"]), default=0) + 1)
    save_review(ctx, doc, questions)
    deshi_stats = ctx.usage.extra.get("deshi", {"round1": 0, "round2": None, "dropped_no_anchor": 0})
    ctx.usage.extra["deshi"] = {**deshi_stats, "reread": len(current)}
    ctx.usage.extra["questions"] = len(questions["questions"])
    store.write(run_path, ctx.usage.to_json(), "run")
    guide.refresh(ctx.neta)  # 指摘が増えて札から外れる項目があれば反映する（LLM 0 回）
    return current


# ノート役の文の検査。断定、客の気持ち、直し方の提案、数字の羅列を通さない。
# 「反応候補」の言い方は約束の 3 つだけ許し、それ以外の「反応…ない」は落とす
NOTE_ALLOWED = ("反応候補が少な", "反応候補が小さ", "反応候補が見つからな")
NOTE_BANNED = re.compile(
    r"(反応|候補|笑い|ウケ|受け|手応え)[^。]{0,8}"
    r"(なし|無し|なかった|無かった|ない|無い|ありません|ませんでした|起きな|起こらな|薄|弱|出な)"
    r"|無反応|沈黙|静か|シーン|滑っ|滑り|スベ|盛り上がらな|失敗|ダメ|だめ|客"
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


def note_is_ok(text: str) -> bool:
    plain = unicodedata.normalize("NFKC", text).strip()
    if not plain or len(re.findall(r"[。！？!?]", plain)) > NOTE_MAX_SENTENCES:
        return False
    for phrase in NOTE_ALLOWED:
        plain = plain.replace(phrase, "")
    return NOTE_BANNED.search(plain) is None


def checked_notes(doc: dict | None, cands: dict) -> tuple[dict[str, str], int]:
    """候補ごとに 1 件の文を返す。無い、重なる、断定の語があるものは決まった文に置き換えて数える。"""
    written: dict[str, list[str]] = {}
    for n in (doc or {"notes": []})["notes"]:
        written.setdefault(n["candidate_id"], []).append(n["text"].strip())
    out, fallback = {}, 0
    for c in cands["candidates"]:
        texts = written.get(c["id"], [])
        if len(texts) == 1 and note_is_ok(texts[0]):
            out[c["id"]] = texts[0]
        else:
            out[c["id"]] = NOTE_FALLBACK[c["why"]]
            fallback += 1
    return out, fallback


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
    for stale in (
        kdir / "note.md",
        kdir / "sokki.md",
        ctx.neta.drafts / "noter.json",
    ):  # 途中で落ちても古い文を残さない
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
    store.write(kdir / "note_candidates.json", cands, "note_candidates")
    items = {i["id"]: i for i in all_items}
    doc = None
    if cands["candidates"]:
        rows = noter_rows(cands, items, ctx.transcript, ctx.hanashi, tl)
        text = json.dumps(rows, ensure_ascii=False, indent=1)
        content = [{"type": "text", "text": text}]
        try:
            doc = call_role(ctx.client, "noter", content, "noter", ctx.usage, ctx.budget, ctx.cache)
        except Exception:
            store.write(run_path, ctx.usage.to_json(), "run")  # 費用は実測として残す
            raise
        store.write(ctx.neta.drafts / "noter.json", doc, "noter")
    notes, fallback = checked_notes(doc, cands)
    for c in cands["candidates"]:
        c["note"] = notes[c["id"]]
    store.write(kdir / "note_candidates.json", cands, "note_candidates")  # 視聴画面が読む
    scene_of = {s["seg_id"]: s["scene_id"] for s in tl["segments"]}
    titles = {s["id"]: s["title"] for s in ctx.hanashi["scenes"]}
    item_scene = {
        c["item_id"]: titles.get(scene_of.get(items[c["item_id"]]["payoff"]["segs"][0]), "場面不明")
        for c in cands["candidates"]
    }
    (kdir / "note.md").write_text(
        render.note_markdown(ctx.koza, cands, notes, items, item_scene), encoding="utf-8"
    )
    ctx.usage.extra["notes"] = {"candidates": len(cands["candidates"]), "fallback": fallback}
    result = ctx.usage.to_json()
    store.write(run_path, result, "run")
    return result
