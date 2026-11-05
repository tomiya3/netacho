"""JSON の検証。形は `schemas/*.json`（JSON Schema draft-07）、参照の実在は `check_refs`。

役の出力は runner が受け取った直後、保存の前に `validate` を通す。役の schema は同じファイルを
Messages API の structured outputs にも渡す。
"""

from __future__ import annotations

import copy
import json
from functools import cache
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

# kind の振り分けの既定。見立て・地口・知らない物は構成作家役が決める
HANASHI_KINDS = frozenset({"場面", "人物", "サゲ"})
KATA_KINDS = frozenset({"枕", "間", "上下", "staging"})
PAYOFF_KINDS = frozenset({"見立て", "地口", "くすぐり", "サゲ"})
POSITION_ORDER = {"before": 0, "within": 1, "after": 2}


class SchemaError(ValueError):
    pass


class RefError(ValueError):
    """参照先が無い、または kind の約束に合わない。"""


@cache
def load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))


# structured outputs が受け付けないキーワード（400 になる）。検証は手元の validate で行う
_API_UNSUPPORTED = (
    "minimum",
    "maximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "maxItems",
    "minItems",
    "pattern",
)


@cache
def api_schema(name: str) -> dict:
    """Messages API の output_config.format に渡す形。未対応の制約を description に退避する。"""
    s = copy.deepcopy(load(name))
    s.pop("$schema", None)
    s.pop("title", None)
    _strip(s)
    return s


def _strip(node) -> None:
    if isinstance(node, dict):
        notes = [f"{k}={node.pop(k)}" for k in list(node.keys()) if k in _API_UNSUPPORTED]
        if notes:
            node["description"] = (node.get("description", "") + " (" + ", ".join(notes) + ")").strip()
        for v in node.values():
            _strip(v)
    elif isinstance(node, list):
        for v in node:
            _strip(v)


def validate(obj, name: str) -> None:
    """schema に合わなければ SchemaError（最初の数件をまとめて）。"""
    errors = sorted(Draft7Validator(load(name)).iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        # e.message は値（演者の言葉や引用）を含むので、場所と規則の名前だけを出す
        msgs = [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.validator}" for e in errors[:5]]
        raise SchemaError(f"{name}.json に合わない: " + " | ".join(msgs))


def _duplicates(ids: list) -> set:
    seen: set = set()
    return {i for i in ids if i in seen or seen.add(i)}


def _check_span(segs: list[str], order: dict[str, int], where: str, errors: list[str]) -> None:
    first, last = segs
    if first not in order or last not in order:
        errors.append(f"{where}: 区間が無い {segs}")
    elif order[first] > order[last]:
        errors.append(f"{where}: 区間の順が逆 {segs}")


def _check_books(hanashi: dict, kata: dict, koza_ids: set[str] | None, errors: list[str]) -> None:
    if (hanashi["performer_id"], hanashi["neta"]) != (kata["performer_id"], kata["neta"]):
        errors.append("hanashi と kata で演者か演目が違う")
    if hanashi["book"] != "hanashi" or kata["book"] != "kata":
        errors.append("book の欄が hanashi / kata に合わない")
    ids = [i["id"] for b in (hanashi, kata) for i in b["items"]]
    if dup := _duplicates(ids):
        errors.append(f"item の id が重複: {sorted(dup)}")
    for book in (hanashi, kata):
        if dup := _duplicates([s["id"] for s in book["scenes"]]):
            errors.append(f"{book['book']}: 場面の id が重複: {sorted(dup)}")
        if dup := _duplicates([s["order"] for s in book["scenes"]]):
            errors.append(f"{book['book']}: 場面の order が重複: {sorted(dup)}")
        if dup := _duplicates([c["id"] for c in book["characters"]]):
            errors.append(f"{book['book']}: 人物の id が重複: {sorted(dup)}")
    if {s["id"] for s in hanashi["scenes"]} != {s["id"] for s in kata["scenes"]}:
        errors.append("hanashi と kata で場面が違う")
    scene_ids = {s["id"] for s in hanashi["scenes"]}
    for scene in hanashi["scenes"]:
        if koza_ids is not None and scene["koza_id"] not in koza_ids:
            errors.append(f"{scene['id']}: 高座が無い {scene['koza_id']}")
    for book, wrong in ((hanashi, KATA_KINDS), (kata, HANASHI_KINDS)):
        for it in book["items"]:
            where = f"{book['book']}/{it['id']}"
            if it["kind"] in wrong:
                errors.append(f"{where}: kind {it['kind']} はこの帳面に置かない")
            if book is kata and it["basis"] != "heard":
                errors.append(f"{where}: 型は heard だけ")
            recorded = "koza_id" in it["source"]
            if koza_ids is not None and recorded and it["source"]["koza_id"] not in koza_ids:
                errors.append(f"{where}: 高座が無い {it['source']['koza_id']}")
            if recorded and it["kind"] in PAYOFF_KINDS and "payoff" not in it:
                errors.append(f"{where}: {it['kind']} に回収の区間が無い")
            if "payoff" in it and it["kind"] not in PAYOFF_KINDS:
                errors.append(f"{where}: {it['kind']} に回収は付けない")
            if "anchor" in it and it["anchor"]["scene_id"] not in scene_ids:
                errors.append(f"{where}: 場面が無い {it['anchor']['scene_id']}")
            if it.get("approved") and not it["source"].get("approved"):
                errors.append(f"{where}: 演者が確かめていない言葉を札に出そうとしている")


def _check_transcript(transcript: dict, books: list[dict], errors: list[str]) -> None:
    koza_id = transcript["koza_id"]
    seg_ids = [s["id"] for s in transcript["segments"]]
    if dup := _duplicates(seg_ids):
        errors.append(f"transcript: 区間の id が重複: {sorted(dup)}")
    order = {s: i for i, s in enumerate(seg_ids)}
    for s in transcript["segments"]:
        if s["end"] < s["start"]:
            errors.append(f"transcript/{s['id']}: end が start より前")
    for book in books:
        for scene in book["scenes"]:
            if scene["koza_id"] == koza_id:
                _check_span(scene["segs"], order, f"{book['book']}/{scene['id']}", errors)
        for it in book["items"]:
            where = f"{book['book']}/{it['id']}"
            if it["source"].get("koza_id") == koza_id:
                _check_span(it["source"]["segs"], order, where, errors)
                if "payoff" in it:
                    pay = it["payoff"]["segs"]
                    _check_span(pay, order, f"{where}/payoff", errors)
                    first = it["source"]["segs"][0]
                    if order.get(pay[0], len(order)) < order.get(first, -1):
                        errors.append(f"{where}: 回収が仕込みより前にある")


def _check_koza_files(
    *,
    koza_id: str | None,
    scene_ids: set[str],
    items: dict[str, dict],
    seg_ids: set[str] | None,
    timeline: dict | None,
    reactions: dict | None,
    note_candidates: dict | None,
    errors: list[str],
) -> None:
    for name, doc in (("timeline", timeline), ("reactions", reactions), ("note_candidates", note_candidates)):
        if doc is not None and koza_id is not None and doc["koza_id"] != koza_id:
            errors.append(f"{name}: 高座が違う {doc['koza_id']}")
    if timeline is not None:
        if dup := _duplicates([s["seg_id"] for s in timeline["segments"]]):
            errors.append(f"timeline: 区間が重複: {sorted(dup)}")
        for s in timeline["segments"]:
            if seg_ids is not None and s["seg_id"] not in seg_ids:
                errors.append(f"timeline: 区間が無い {s['seg_id']}")
            if s["scene_id"] is not None and s["scene_id"] not in scene_ids:
                errors.append(f"timeline: 場面が無い {s['scene_id']}")
    reaction_ids: set[str] | None = None
    if reactions is not None:
        ids = [r["id"] for r in reactions["reactions"]]
        if dup := _duplicates(ids):
            errors.append(f"reactions: id が重複: {sorted(dup)}")
        reaction_ids = set(ids)
        for r in reactions["reactions"]:
            if r["t0"] > r["t1"]:
                errors.append(f"reactions/{r['id']}: t0 が t1 より後")
    if note_candidates is not None:
        cands = note_candidates["candidates"]
        if dup := _duplicates([c["id"] for c in cands]):
            errors.append(f"note_candidates: id が重複: {sorted(dup)}")
        for c in cands:
            where = f"note_candidates/{c['id']}"
            if c["item_id"] not in items:
                errors.append(f"{where}: item が無い {c['item_id']}")
            elif "payoff" not in items[c["item_id"]]:
                errors.append(f"{where}: 回収の無い item を指している")
            else:
                item = items[c["item_id"]]
                if item["source"].get("koza_id") != note_candidates["koza_id"]:
                    errors.append(f"{where}: 別の高座の item を指している")
                if not item["payoff"]["t0"] <= c["payoff_t"] <= item["payoff"]["t1"]:
                    errors.append(f"{where}: payoff_t が回収の区間の外")
            for r in c["reaction_ids"]:
                if reaction_ids is not None and r not in reaction_ids:
                    errors.append(f"{where}: 反応が無い {r}")
            # 「未検出」と「弱い」を混ぜない
            if (c["why"] == "no_candidate") != (not c["reaction_ids"]):
                errors.append(f"{where}: why と reaction_ids が合わない")
            if not c["window"][0] <= c["payoff_t"] <= c["window"][1]:
                errors.append(f"{where}: payoff_t が窓の外")


def _check_review(
    review: dict | None, questions: dict | None, scene_ids, item_ids, errors: list[str]
) -> None:
    issues = {}
    if review is not None:
        issues = {i["id"]: i for i in review["issues"]}
        if dup := _duplicates([i["id"] for i in review["issues"]]):
            errors.append(f"review: id が重複: {sorted(dup)}")
        for i in review["issues"]:
            a = i["anchor"]
            if "scene_id" in a and a["scene_id"] not in scene_ids:
                errors.append(f"review/{i['id']}: 場面が無い {a['scene_id']}")
            if "item_id" in a and a["item_id"] not in item_ids:
                errors.append(f"review/{i['id']}: item が無い {a['item_id']}")
    if questions is not None:
        if dup := _duplicates([q["id"] for q in questions["questions"]]):
            errors.append(f"questions: id が重複: {sorted(dup)}")
        for q in questions["questions"]:
            if q["scene_id"] not in scene_ids:
                errors.append(f"questions/{q['id']}: 場面が無い {q['scene_id']}")
            if review is not None:
                issue = issues.get(q["issue_id"])
                if issue is None:
                    errors.append(f"questions/{q['id']}: 指摘が無い {q['issue_id']}")
                elif issue["kind"] != "ask_performer":
                    errors.append(f"questions/{q['id']}: ask_performer でない指摘 {q['issue_id']}")
                elif issue["anchor"].get("scene_id", q["scene_id"]) != q["scene_id"]:
                    errors.append(f"questions/{q['id']}: 指摘と場面が違う")


def unresolved_item_ids(review: dict | None) -> set[str]:
    """解決していない弟子役の指摘が付いた item。札に出さない。"""
    if review is None:
        return set()
    return {
        i["anchor"]["item_id"] for i in review["issues"] if "item_id" in i["anchor"] and not i["resolved"]
    }


def card_items(kata: dict, review: dict | None) -> list[dict]:
    """札に出す item。承認済みの staging で、保留でなく、解決していない指摘が付いていないもの。"""
    blocked = unresolved_item_ids(review)
    return [
        i
        for i in kata["items"]
        if i["kind"] == "staging"
        and i["approved"]
        and i.get("card_text")
        and not i.get("held")
        and i["id"] not in blocked
    ]


def card_sort_key(scene_order: dict[str, int], item: dict) -> tuple[int, int]:
    """場面順。同じ場面の中は 前 → 途中 → 後。"""
    return scene_order[item["anchor"]["scene_id"]], POSITION_ORDER[item["anchor"]["position"]]


def _check_guide(guide: dict, hanashi: dict, kata: dict, review: dict | None, errors: list[str]) -> None:
    """札が item の写しで、出すべきものが場面順に過不足なく並んでいること。"""
    scenes = {s["id"]: s for s in hanashi["scenes"]}
    items = {i["id"]: i for i in kata["items"]}
    if (guide["performer_id"], guide["neta"]) != (kata["performer_id"], kata["neta"]):
        errors.append("guide と kata で演者か演目が違う")
    shown = [c["item_id"] for c in guide["cards"]]
    if dup := _duplicates(shown):
        errors.append(f"guide: 同じ item の札が重複: {sorted(dup)}")
    wanted = card_items(kata, review)
    if missing := {i["id"] for i in wanted} - set(shown):
        errors.append(f"guide: 札に出ていない: {sorted(missing)}")
    if extra := set(shown) & unresolved_item_ids(review):
        errors.append(f"guide: 解決していない指摘が付いた項目が札に出ている: {sorted(extra)}")
    scene_order = {s["id"]: s["order"] for s in hanashi["scenes"]}
    known = [items[i] for i in shown if i in items and "anchor" in items[i]]
    if all(i["anchor"]["scene_id"] in scene_order for i in known):
        keys = [card_sort_key(scene_order, i) for i in known]
        if keys != sorted(keys):
            errors.append("guide: 札が場面順でない")
    for n, card in enumerate(guide["cards"], start=1):
        where = f"guide/{card['item_id']}"
        if card["order"] != n:
            errors.append(f"{where}: order が順番でない")
        if card["scene_id"] not in scenes:
            errors.append(f"{where}: 場面が無い {card['scene_id']}")
            continue
        it = items.get(card["item_id"])
        if it is None or it["kind"] != "staging" or not it["approved"] or it.get("held"):
            errors.append(f"{where}: 承認済みの staging でない")
            continue
        scene = scenes.get(it["anchor"]["scene_id"])
        if scene is None:  # _check_books が数えている
            continue
        copied = card["copied_fields"]
        want = {
            "card_text": it["card_text"],
            "reason": it["reason"],
            "position": it["anchor"]["position"],
            "scene_title": scene["title"],
            "edited_by": it["edited_by"],
        }
        if copied != want or card["text"] != it["card_text"] or card["scene_id"] != it["anchor"]["scene_id"]:
            errors.append(f"{where}: item の写しになっていない")


def check_refs(
    hanashi: dict,
    kata: dict,
    *,
    koza: dict | None = None,
    koza_ids: set[str] | None = None,
    transcript: dict | None = None,
    timeline: dict | None = None,
    reactions: dict | None = None,
    note_candidates: dict | None = None,
    review: dict | None = None,
    questions: dict | None = None,
    guide: dict | None = None,
) -> None:
    """参照先の実在と kind の約束を確かめる。渡した物の間だけを見る。崩れていれば RefError。

    `koza_ids` は演目の全高座の ID。`koza` を渡すとその ID も含める。
    """
    errors: list[str] = []
    known = set(koza_ids) if koza_ids is not None else None
    koza_id = None
    if koza is not None:
        koza_id = koza["id"]
        known = (known or set()) | {koza_id}
        if (koza["performer_id"], koza["neta"]) != (hanashi["performer_id"], hanashi["neta"]):
            errors.append("koza と hanashi で演者か演目が違う")
    _check_books(hanashi, kata, known, errors)
    if transcript is not None:
        if koza_id is not None and transcript["koza_id"] != koza_id:
            errors.append(f"transcript: 高座が違う {transcript['koza_id']}")
        _check_transcript(transcript, [hanashi, kata], errors)
    scene_ids = {s["id"] for s in hanashi["scenes"]}
    items = {i["id"]: i for b in (hanashi, kata) for i in b["items"]}
    _check_koza_files(
        koza_id=koza_id,
        scene_ids=scene_ids,
        items=items,
        seg_ids={s["id"] for s in transcript["segments"]} if transcript is not None else None,
        timeline=timeline,
        reactions=reactions,
        note_candidates=note_candidates,
        errors=errors,
    )
    _check_review(review, questions, scene_ids, set(items), errors)
    if guide is not None:
        _check_guide(guide, hanashi, kata, review, errors)
    if errors:
        more = f"（ほか {len(errors) - 5} 件）" if len(errors) > 5 else ""
        raise RefError(" | ".join(errors[:5]) + more)
