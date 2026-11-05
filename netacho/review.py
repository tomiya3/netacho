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


def _key(issue: dict) -> tuple:
    return issue["role"], issue["kind"], tuple(sorted(issue["anchor"].items()))


def receive(doc: dict, review: dict, hanashi: dict, kata: dict, round_no: int) -> tuple[list[dict], int]:
    """役の出力を指摘にして review に足す。(今回の指摘, 落とした数)。

    同じ所への未解決の指摘が既にあれば足さず、それを今回の指摘として返す（作り直しで重ねない）。
    演者の項目を指す指摘は、構成作家役では直せないので ask_performer に回す。
    """
    items = {i["id"]: i for b in (hanashi, kata) for i in b["items"]}
    scene_ids = {s["id"] for s in hanashi["scenes"]}
    visible = {k for k, i in items.items() if not i.get("held")}
    open_issues = {_key(i): i for i in review["issues"] if not i["resolved"]}
    answered = {_key(i) for i in review["issues"] if i["resolved"] and i["kind"] == ASK}
    n = _next_number(review["issues"])
    current, dropped = [], 0
    for raw in doc["issues"]:
        anchor = _anchor(raw, scene_ids, visible)
        if anchor is None or not raw["attempt"].strip() or not raw["what_is_missing"].strip():
            dropped += 1
            continue
        kind = raw["kind"]
        if "item_id" in anchor and items[anchor["item_id"]]["edited_by"] == "performer":
            kind = ASK
        issue = {
            "id": f"issue-{n:04d}",
            "role": "deshi",
            "kind": kind,
            "anchor": anchor,
            "attempt": raw["attempt"],
            "what_is_missing": raw["what_is_missing"],
            "round": round_no,
            "resolved": False,
        }
        if _key(issue) in answered:  # 演者が答えた所を聞き直さない。足さなかった数は数える
            review["suppressed_answered"] = review.get("suppressed_answered", 0) + 1
            continue
        existing = open_issues.get(_key(issue))
        if existing is not None:
            if existing not in current:
                current.append(existing)
            continue
        open_issues[_key(issue)] = issue
        review["issues"].append(issue)
        current.append(issue)
        n += 1
    return current, dropped


def rework_of(issues: list[dict]) -> list[dict]:
    """構成作家役への差し戻し。ask_performer は渡さない。"""
    keys = ("kind", "anchor", "attempt", "what_is_missing")
    return [{k: i[k] for k in keys} for i in issues if i["kind"] != ASK]


def scene_of_anchor(anchor: dict, items: dict, lookup: dict) -> str | None:
    if "scene_id" in anchor:
        return anchor["scene_id"]
    return render.scene_of_item(items[anchor["item_id"]], lookup)


def add_questions(
    issues: list[dict], questions: dict, hanashi: dict, kata: dict, lookup: dict, *, skip_scenes: set[str]
) -> tuple[int, set[str], set[str]]:
    """ask_performer を質問にする。(場面が決まらず落とした数, 質問した場面, 聞かなかった指摘の id)。

    skip_scenes は同じ実行の 1 周目で聞いた場面。2 周目の言い直しで聞き直さない。
    聞かなかった指摘は呼び出し側が消す（質問の無い指摘は answer で解決できず、札を止め続けるため）。
    """
    items = {i["id"]: i for b in (hanashi, kata) for i in b["items"]}
    has_question = {q["issue_id"] for q in questions["questions"]}
    n = _next_number(questions["questions"])
    dropped = 0
    asked: set[str] = set()
    skipped: set[str] = set()
    for issue in issues:
        if issue["kind"] != ASK or issue["id"] in has_question:
            continue
        scene = scene_of_anchor(issue["anchor"], items, lookup)
        if scene is None:
            dropped += 1
            continue
        if scene in skip_scenes:
            skipped.add(issue["id"])
            continue
        text = f"{issue['attempt']}。{issue['what_is_missing']}"
        questions["questions"].append(
            {"id": f"q-{n:04d}", "issue_id": issue["id"], "scene_id": scene, "text": text}
        )
        n += 1
        asked.add(scene)
    return dropped, asked, skipped


def settle_rework(
    first: list[dict],
    second: list[dict],
    *,
    items: dict,
    lookup: dict,
    touched_scenes: set[str],
    second_dropped: int,
) -> None:
    """1 周目の差し戻しを解決にしてよいか決める。解決は札の許可につながるので、厳しく見る。

    解決にするのは、2 周目に同じ場面の指摘が無く（kind は言い直しで変わるので問わない）、落とした指摘も無く、
    再生成がその場面を実際に変えていて、指された項目が保留になっていないときだけ。
    """
    if second_dropped:
        return
    remaining = {scene_of_anchor(i["anchor"], items, lookup) for i in second}
    for issue in first:
        if issue["kind"] == ASK:
            continue
        scene = scene_of_anchor(issue["anchor"], items, lookup)
        target = items.get(issue["anchor"].get("item_id", ""))
        if scene in remaining or scene not in touched_scenes:
            continue
        if target is not None and target.get("held"):
            continue
        issue["resolved"] = True
