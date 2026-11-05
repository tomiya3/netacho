"""開演前の案内の札。スクリプトが item から写すだけで、LLM は呼ばない。

このモジュールは役の呼び出しを import しない。札に Claude の文が混ざらないことを、経路で守る。
"""

from __future__ import annotations

from . import store
from .schema import card_items, card_sort_key, check_refs


def build(hanashi: dict, kata: dict, review: dict | None) -> dict:
    """承認済みの staging を場面順に写す。guide.json の中身を返す。"""
    scenes = {s["id"]: s for s in hanashi["scenes"]}
    scene_order = {s["id"]: s["order"] for s in hanashi["scenes"]}
    rows = [i for i in card_items(kata, review) if i["anchor"]["scene_id"] in scenes]
    rows.sort(key=lambda i: card_sort_key(scene_order, i))
    cards = []
    for n, it in enumerate(rows, start=1):
        scene = scenes[it["anchor"]["scene_id"]]
        cards.append(
            {
                "order": n,
                "scene_id": scene["id"],
                "item_id": it["id"],
                "text": it["card_text"],
                "copied_fields": {
                    "card_text": it["card_text"],
                    "reason": it["reason"],
                    "position": it["anchor"]["position"],
                    "scene_title": scene["title"],
                    "edited_by": it["edited_by"],
                },
                "llm_fields": {},
            }
        )
    return {
        "schema_version": 1,
        "performer_id": hanashi["performer_id"],
        "neta": hanashi["neta"],
        "cards": cards,
    }


def write(neta: store.Neta) -> dict:
    """正本から札を作り、参照を確かめて保存する。"""
    hanashi, kata = store.read(neta.hanashi, "book"), store.read(neta.kata, "book")
    review = store.read(neta.review, "review") if neta.review.exists() else None
    doc = build(hanashi, kata, review)
    check_refs(hanashi, kata, review=review, guide=doc)
    runs = {p: store.read(p, "run") for p in (neta.base / "koza").glob("*/run.json")}  # 先に全部確かめる
    store.write(neta.guide, doc, "guide")
    count = sum(1 for c in doc["cards"] if c["copied_fields"]["edited_by"] == "performer")
    for path, run in runs.items():
        run["guide_performer_cards"] = count
        store.write(path, run, "run")
    return doc


def refresh(neta: store.Neta) -> None:
    """札が既にあるときだけ作り直す（古い札が画面に残らないように）。"""
    if neta.guide.exists():
        write(neta)
