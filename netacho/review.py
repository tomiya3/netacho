"""弟子役の指摘の受け取りと仕分け。LLM は使わない。

指摘の行き先：
- missing_step、unclear_scene、no_makura、no_sage は構成作家役への差し戻し（再生成は 1 回まで）
- ask_performer は演者への質問。構成作家役には渡さない
- 場面が実在しない指摘は落として数える
"""

from __future__ import annotations

from . import render

ASK = "ask_performer"


def empty_review(performer: str, neta: str) -> dict:
    return {"schema_version": 1, "performer_id": performer, "neta": neta, "issues": []}


def empty_questions(performer: str, neta: str) -> dict:
    return {"schema_version": 1, "performer_id": performer, "neta": neta, "questions": []}


def _next_number(rows: list[dict]) -> int:
    return max((int(r["id"].split("-")[1]) for r in rows), default=0) + 1


def _anchor(raw: dict, scene_ids: set[str], item_ids: set[str]) -> dict | None:
    """場面か項目のどちらか一方で、実在すること。保留の項目は .md に無いので指せない。"""
    scene, item = raw["scene_id"], raw["item_id"]
    if (scene is None) == (item is None):
        return None
    if scene is not None:
        return {"scene_id": scene} if scene in scene_ids else None
    return {"item_id": item} if item in item_ids else None


def receive(doc: dict, review: dict, hanashi: dict, kata: dict, round_no: int) -> tuple[list[dict], int]:
    """役の出力を指摘にして review に足す。(足した指摘, 落とした数)。"""
    scene_ids = {s["id"] for s in hanashi["scenes"]}
    item_ids = {i["id"] for b in (hanashi, kata) for i in b["items"] if not i.get("held")}
    n = _next_number(review["issues"])
    added, dropped = [], 0
    for raw in doc["issues"]:
        anchor = _anchor(raw, scene_ids, item_ids)
        if anchor is None or not raw["attempt"].strip() or not raw["what_is_missing"].strip():
            dropped += 1
            continue
        added.append(
            {
                "id": f"issue-{n:04d}",
                "role": "deshi",
                "kind": raw["kind"],
                "anchor": anchor,
                "attempt": raw["attempt"],
                "what_is_missing": raw["what_is_missing"],
                "round": round_no,
                "resolved": False,
            }
        )
        n += 1
    review["issues"].extend(added)
    return added, dropped


def rework_of(issues: list[dict]) -> list[dict]:
    """構成作家役への差し戻し。ask_performer は渡さない。"""
    keys = ("kind", "anchor", "attempt", "what_is_missing")
    return [{k: i[k] for k in keys} for i in issues if i["kind"] != ASK]


def scene_of_anchor(anchor: dict, items: dict, lookup: dict) -> str | None:
    if "scene_id" in anchor:
        return anchor["scene_id"]
    return render.scene_of_item(items[anchor["item_id"]], lookup)


def add_questions(issues: list[dict], questions: dict, hanashi: dict, kata: dict, lookup: dict) -> int:
    """ask_performer を質問にする。同じ所への質問は重ねない。場面が決まらなければ落として数を返す。"""
    items = {i["id"]: i for b in (hanashi, kata) for i in b["items"]}
    asked = {(q["scene_id"], q["text"]) for q in questions["questions"]}
    scenes_asked = {q["scene_id"] for q in questions["questions"]}
    n = _next_number(questions["questions"])
    dropped = 0
    for issue in issues:
        if issue["kind"] != ASK:
            continue
        scene = scene_of_anchor(issue["anchor"], items, lookup)
        if scene is None:
            dropped += 1
            continue
        text = f"{issue['attempt']}。{issue['what_is_missing']}"
        if issue["round"] > 1 and scene in scenes_asked:  # 2 周目の言い直しで同じ場面を聞き直さない
            continue
        if (scene, text) in asked:
            continue
        questions["questions"].append(
            {"id": f"q-{n:04d}", "issue_id": issue["id"], "scene_id": scene, "text": text}
        )
        n += 1
    return dropped


def settle_rework(first: list[dict], second: list[dict]) -> None:
    """1 周目の差し戻しのうち、2 周目に同じ所へ同じ kind が無いものを解決にする。"""
    remaining = {(i["kind"], tuple(sorted(i["anchor"].items()))) for i in second}
    for issue in first:
        if issue["kind"] != ASK:
            issue["resolved"] = (issue["kind"], tuple(sorted(issue["anchor"].items()))) not in remaining
