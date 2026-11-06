"""同じ問いを答えの前と後の帳面で読ませ、抜けが埋まったかを確かめる。判定は Claude、検査はスクリプト。

役には、どちらの帳面かを教えない。引用が帳面に無い判定は落として数える。落とした所は null のまま残し、
「書いていない」と扱わない。
"""

from __future__ import annotations

WRITTEN = "written"
MIN_QUOTE = 4  # 短い引用は、たまたま答えの中にある語と区別できない


def answered(questions: dict, kata: dict) -> list[tuple[dict, dict]]:
    """答えのある問いと、その答えの項目の組。項目が無い問いは前後を作れないので外す。"""
    items = {i["source"].get("question_id"): i for i in kata["items"]}
    return [(q, items[q["id"]]) for q in questions["questions"] if "answer" in q and q["id"] in items]


def without_answers(kata: dict, pairs: list[tuple[dict, dict]]) -> dict:
    """答えの項目を外した型。保存しない。"""
    gone = {item["id"] for _, item in pairs}
    return {**kata, "items": [i for i in kata["items"] if i["id"] not in gone]}


def content(text: str, pairs: list[tuple[dict, dict]]) -> list[dict]:
    """役に見せる物：帳面と問いの文だけ。"""
    asked = "\n".join(f"- `{q['id']}`：{q['text']}" for q, _ in pairs)
    return [{"type": "text", "text": f"{text}\n# 師匠に聞いた問い\n\n{asked}\n"}]


def receive(raw: dict, pairs: list[tuple[dict, dict]], text: str) -> tuple[dict[str, dict], int]:
    """役の出力を検査する。(問いの id → 判定, 落とした数)。"""
    asked = {q["id"] for q, _ in pairs}
    sides: dict[str, dict] = {}
    dropped = 0
    for row in raw["answers"]:
        qid, quote = row["question_id"], row["quote"].strip()
        written = row["verdict"] == WRITTEN
        if qid not in asked or qid in sides or (written and (not quote or quote not in text)):
            dropped += 1
            continue
        sides[qid] = {"verdict": row["verdict"], "quote": quote if written else "", "reply": row["reply"]}
    return sides, dropped


def _cites(side: dict | None, words: str) -> bool:
    if side is None or side["verdict"] != WRITTEN:
        return False
    quote = side["quote"]
    return words in quote or (len(quote) >= MIN_QUOTE and quote in words)


def merge_sides(
    pairs: list[tuple[dict, dict]], before: dict[str, dict], after: dict[str, dict]
) -> list[dict]:
    return [
        {
            "question_id": q["id"],
            "before": before.get(q["id"]),
            "after": after.get(q["id"]),
            "cites_answer": _cites(after.get(q["id"]), item["source"]["words"]),
        }
        for q, item in pairs
    ]


def filled(row: dict) -> bool:
    """前は書いていないと読み、後は演者の答えを引いて読めた。"""
    before = row["before"]
    return before is not None and before["verdict"] != WRITTEN and row["cites_answer"]


def stats(rows: list[dict], dropped: int) -> dict:
    def written(side: str) -> int:
        return sum(r[side] is not None and r[side]["verdict"] == WRITTEN for r in rows)

    return {
        "questions": len(rows),
        "before_written": written("before"),
        "after_written": written("after"),
        "filled": sum(filled(r) for r in rows),
        "dropped": dropped,
    }
