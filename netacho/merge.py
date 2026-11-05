"""正本（hanashi.json、kata.json）に入れる。書くのはここだけ。

- `edited_by: performer` の項目は変えない
- item の id は作成時に振り、編集しても変えない
- 照合で unsupported の項目は held にする
- 文に内部の番号が残った項目も held にする。落とさない（噺が欠けるより、直すまで出さない）
"""

from __future__ import annotations

from datetime import UTC, datetime

from .evidence import has_internal_id


def empty_book(book: str, performer: str, neta: str) -> dict:
    return {
        "schema_version": 1,
        "book": book,
        "performer_id": performer,
        "neta": neta,
        "scenes": [],
        "characters": [],
        "items": [],
    }


def split_ids(hanashi: dict, kata: dict) -> tuple[set[str], set[str]]:
    """(道具の項目の id, 演者の項目の id)。"""
    items = hanashi["items"] + kata["items"]
    return (
        {i["id"] for i in items if i["edited_by"] == "tool"},
        {i["id"] for i in items if i["edited_by"] == "performer"},
    )


def next_item_number(hanashi: dict, kata: dict) -> int:
    used = [int(i["id"].split("-")[1]) for i in hanashi["items"] + kata["items"]]
    return max(used, default=0) + 1


def assign_ids(items: list[dict], hanashi: dict, kata: dict) -> None:
    """新しい項目に id を振る（照合役が id で判定を返すので、その前に振る）。"""
    n = next_item_number(hanashi, kata)
    for it in items:
        if it["id"] is None:
            it["id"] = f"item-{n:04d}"
            n += 1


def _union(old: list[dict], new: list[dict]) -> list[dict]:
    """id で重ねる。新しい方を優先し、演者の項目が指す古い場面は残す。"""
    merged = {x["id"]: x for x in old}
    merged.update({x["id"]: x for x in new})
    return list(merged.values())


def merge(
    hanashi: dict,
    kata: dict,
    *,
    items: list[dict],
    scenes: list[dict],
    characters: list[dict],
    held_ids: set[str],
    now: str | None = None,
) -> None:
    """hanashi と kata を書き換える。items は `verify_items` の結果で、id が振ってあること。"""
    at = now or datetime.now(UTC).isoformat(timespec="seconds")
    books = {"hanashi": hanashi, "kata": kata}
    current = {i["id"]: i for b in books.values() for i in b["items"]}
    for it in items:
        old = current.get(it["id"])
        if old is not None and old["edited_by"] == "performer":
            raise ValueError(f"{it['id']}: 演者の項目は上書きしない")
        row = {k: v for k, v in it.items() if k != "book"}
        if old is not None:
            row["history"] = old["history"]
            if old["text"] != row["text"]:
                row["history"] = [
                    *old["history"],
                    {"at": at, "before": old["text"], "after": row["text"], "by": "writer"},
                ]
        # knowledge は照合を通らないので、保留の項目を knowledge に書き換えても保留は外れない
        if it["id"] in held_ids or (old is not None and old.get("held") and row["basis"] == "knowledge"):
            row["held"] = True
        if has_internal_id(row["text"]):
            row["held"] = True
            row["held_reason"] = "internal_id"
        target = books[it["book"]]
        other = books["kata" if it["book"] == "hanashi" else "hanashi"]
        other["items"] = [i for i in other["items"] if i["id"] != it["id"]]  # 帳面を移ったとき
        for n, existing in enumerate(target["items"]):
            if existing["id"] == it["id"]:
                target["items"][n] = row  # 位置は保つ
                break
        else:
            target["items"].append(row)
    ordered = sorted(_union(hanashi["scenes"], scenes), key=lambda s: (s["order"], s["id"]))
    merged_scenes = [{**s, "order": n} for n, s in enumerate(ordered, start=1)]
    merged_chars = _union(hanashi["characters"], characters)
    for book in books.values():
        book["scenes"] = merged_scenes
        book["characters"] = merged_chars
