"""流れ。順番を決めるのはスクリプトで、Claude は役として 1 つずつ呼ばれるだけ。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import candidates, evidence, merge, render, store
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
    verdicts = {v["item_id"]: v["verdict"] for v in doc["verdicts"]}
    held = set()
    for it in heard:
        verdict = verdicts.get(it["id"], "unsupported")
        ctx.checker[verdict] += 1
        if verdict == "unsupported":
            held.add(it["id"])
    return held


def write_pass(ctx: Context, *, rework: list[dict] | None = None, suffix: str = "") -> None:
    """構成作家役 → 根拠の検証 → 照合役 → マージ → .md。"""
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
    ctx.kept += len(items)
    merge.assign_ids(items, ctx.hanashi, ctx.kata)
    held = _check_items(ctx, items, f"checker{suffix}.json")
    merge.merge(
        ctx.hanashi, ctx.kata, items=items, scenes=scenes, characters=draft["characters"], held_ids=held
    )
    save_books(ctx)


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
    if run_path.exists():
        previous = store.read(run_path, "run")
        if "prep" in previous:  # prep の実測は作り直しても残す
            usage.extra["prep"] = previous["prep"]
        if reuse:
            usage.restore(previous)
    cache = RoleCache((root or store.data_root()) / ".cache" / "roles", reuse=reuse)
    return Context(neta, koza, transcript, client, budget or Budget(), usage, cache, hanashi, kata)


def _finish(ctx: Context) -> dict:
    """run.json を書いて、数字を返す。"""
    ctx.usage.extra |= {
        "evidence": {"kept": ctx.kept, "dropped": dict(ctx.dropped)},
        "checker": {k: ctx.checker[k] for k in ("supported", "weak", "unsupported", "unchecked_knowledge")},
    }
    doc = ctx.usage.to_json()
    store.write(ctx.neta.koza_dir(ctx.koza["id"]) / "run.json", doc, "run")
    return doc


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
    """噺・型を作る。返すのは run.json の中身。"""
    ctx = _context(performer, neta_name, koza_id, client=client, budget=budget, reuse=reuse, root=root)
    write_pass(ctx)
    return _finish(ctx)


# ノート役の文に出してはいけない語（断定）
NOTE_BANNED = (
    "受けなかった",
    "ウケなかった",
    "受けていない",
    "反応なし",
    "反応がなかった",
    "反応が無かった",
    "滑",
    "スベ",
)
NOTE_FALLBACK = {
    "no_candidate": "回収の後に、反応候補が少ない所です。",
    "low_rank": "回収の後の反応候補が、この高座の中では小さい方です。",
}


def noter_rows(cands: dict, items: dict, transcript: dict, hanashi: dict, tl: dict) -> list[dict]:
    """ノート役に見せる物：選ばれた候補、前後の引用、場面の尺だけ。"""
    segs = evidence.Segments(transcript)
    scenes = {s["id"]: s for s in hanashi["scenes"]}
    scene_of = {s["seg_id"]: s["scene_id"] for s in tl["segments"]}
    rows = []
    for c in cands["candidates"]:
        it = items[c["item_id"]]
        t0, t1 = c["window"]
        after = "".join(s["text"] for s in transcript["segments"] if t0 <= s["start"] < t1)
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
        scene = scenes.get(scene_of.get(it["payoff"]["segs"][0]))
        if scene is not None and scene["koza_id"] == transcript["koza_id"] and segs.ok(scene["segs"]):
            part = segs.between(scene["segs"])
            row["scene"] = scene["title"]
            row["scene_seconds"] = round(part[-1]["end"] - part[0]["start"])
        rows.append(row)
    return rows


def checked_notes(doc: dict | None, cands: dict) -> tuple[dict[str, str], int]:
    """候補ごとに 1 件の文を返す。無い、重なる、断定の語があるものは決まった文に置き換えて数える。"""
    written: dict[str, list[str]] = {}
    for n in (doc or {"notes": []})["notes"]:
        written.setdefault(n["candidate_id"], []).append(n["text"].strip())
    out, fallback = {}, 0
    for c in cands["candidates"]:
        texts = written.get(c["id"], [])
        if len(texts) == 1 and texts[0] and not any(w in texts[0] for w in NOTE_BANNED):
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
    reactions = store.read(kdir / "reactions.json", "reactions")
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
        doc = call_role(
            ctx.client, "noter", [{"type": "text", "text": text}], "noter", ctx.usage, ctx.budget, ctx.cache
        )
        store.write(ctx.neta.drafts / "noter.json", doc, "noter")
    notes, fallback = checked_notes(doc, cands)
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
