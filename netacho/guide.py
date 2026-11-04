"""開演前の案内の札。スクリプトが item から写すだけで、LLM は呼ばない。

このモジュールは役の呼び出しを import しない。札に Claude の文が混ざらないことを、経路で守る。
"""

from __future__ import annotations

from .schema import card_items, card_sort_key


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
