"""マージのテスト。"""

import pytest

from netacho import merge


def book(name):
    return merge.empty_book(name, "p", "n")


def row(i, *, by="tool", kind="場面", text="文", where="hanashi"):
    it = {
        "id": i,
        "book": where,
        "kind": kind,
        "priority": 1,
        "text": text,
        "basis": "heard",
        "source": {"koza_id": "k", "segs": ["seg-0001", "seg-0001"], "t0": 0, "t1": 1, "quote": "q"},
        "edited_by": by,
        "history": [],
    }
    return it


def strip(it):
    return {k: v for k, v in it.items() if k != "book"}


def test_assign_ids_continues_after_existing():
    h, k = book("hanashi"), book("kata")
    k["items"] = [strip(row("item-0007"))]
    items = [row(None), row("item-0007"), row(None)]
    merge.assign_ids(items, h, k)
    assert [i["id"] for i in items] == ["item-0008", "item-0007", "item-0009"]


def test_merge_refuses_performer_item():
    h, k = book("hanashi"), book("kata")
    k["items"] = [strip(row("item-0001", by="performer"))]
    with pytest.raises(ValueError, match="上書きしない"):
        merge.merge(h, k, items=[row("item-0001")], scenes=[], characters=[], held_ids=set())


def test_merge_moves_item_between_books_and_keeps_history():
    h, k = book("hanashi"), book("kata")
    h["items"] = [strip(row("item-0001", kind="見立て", text="前"))]
    moved = row("item-0001", kind="見立て", text="後", where="kata")
    merge.merge(h, k, items=[moved], scenes=[], characters=[], held_ids=set(), now="t")
    assert h["items"] == []
    assert k["items"][0]["history"] == [{"at": "t", "before": "前", "after": "後", "by": "writer"}]


def test_merge_replaces_in_place_and_clears_held():
    h, k = book("hanashi"), book("kata")
    h["items"] = [{**strip(row("item-0001")), "held": True}, strip(row("item-0002"))]
    merge.merge(h, k, items=[row("item-0001")], scenes=[], characters=[], held_ids=set())
    assert [i["id"] for i in h["items"]] == ["item-0001", "item-0002"]
    assert "held" not in h["items"][0]
    assert h["items"][0]["history"] == []  # 文が同じなら履歴に残さない


def test_scene_order_is_renumbered_without_collision():
    h, k = book("hanashi"), book("kata")
    old = {"id": "scene-01", "title": "古い", "order": 1, "koza_id": "a", "segs": ["seg-0001", "seg-0001"]}
    new = {"id": "scene-02", "title": "新しい", "order": 1, "koza_id": "b", "segs": ["seg-0001", "seg-0001"]}
    h["scenes"] = [old]
    merge.merge(h, k, items=[], scenes=[new], characters=[], held_ids=set())
    assert [(s["id"], s["order"]) for s in k["scenes"]] == [("scene-01", 1), ("scene-02", 2)]


def test_item_with_internal_id_is_held_not_dropped():
    h, k = book("hanashi"), book("kata")
    text = "scene-07 の item-0048 の後"
    merge.merge(h, k, items=[row("item-0001", text=text)], scenes=[], characters=[], held_ids=set())
    assert h["items"][0]["text"] == text  # 消さない
    assert h["items"][0]["held"] is True and h["items"][0]["held_reason"] == "internal_id"


def test_fixed_text_is_released_from_hold():
    h, k = book("hanashi"), book("kata")
    h["items"] = [
        {**strip(row("item-0001", text="scene-07 の後")), "held": True, "held_reason": "internal_id"}
    ]
    merge.merge(
        h, k, items=[row("item-0001", text="結びの一番の後")], scenes=[], characters=[], held_ids=set()
    )
    assert "held" not in h["items"][0] and "held_reason" not in h["items"][0]


def test_checker_hold_has_no_reason_and_stays_in_book_shape():
    h, k = book("hanashi"), book("kata")
    merge.merge(h, k, items=[row("item-0001")], scenes=[], characters=[], held_ids={"item-0001"})
    assert h["items"][0]["held"] is True and "held_reason" not in h["items"][0]
