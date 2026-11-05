"""笑いどころの予測と実際の笑いを比べる。LLM は使わない。

構成作家役は反応を見ずに回収を書く。ここでその予測と AST の笑いを重ねて、3 つに分ける。

- 的中：予測した回収の窓に笑いがある
- 空振り：予測した回収の窓に、反応の候補が無い
- 予測なしで反応あり：どの回収の窓にも入らない笑い。文字に書けない芸の候補

未検出を反応なしと扱わない。自動の反応が無い高座は判定しない。録音の末尾で切れた窓は空振りにしない。
拍手は登場や中手に出るので、笑いどころの比較には使わない。
"""

from __future__ import annotations

from . import candidates
from .reactions import clock, covered_seconds

SURPRISE_MAX = 5  # 演者に問う数の上限。強い順
BEFORE_SEC = 12.0  # 笑いの前の台詞を見せる長さ
FALLBACK_QUESTION = "文字からは笑いどころと読めなかった所です。ここで何をしているのか、教えてください。"


def _before_quote(transcript: dict, t0: float, t1: float) -> str:
    rows = [
        s["text"].strip() for s in transcript["segments"] if s["start"] < t1 and s["end"] > t0 - BEFORE_SEC
    ]
    return "".join(rows)[-160:]


def compare(items: list[dict], reactions: dict, transcript: dict, timeline: dict | None) -> dict:
    """3 区分を作る。surprises は強い順に上限まで。"""
    koza_id = reactions["koza_id"]
    judged = any(r["method"] == "auto" for r in reactions["reactions"])
    end = covered_seconds(reactions)
    laughs = [r for r in reactions["reactions"] if r["kind"] == "laugh"]
    hits, misses = [], []
    used: set[str] = set()
    for it in candidates.payoff_items(items, koza_id):
        window, clipped = candidates.window_of(it, end)
        found = candidates.overlapping(laughs, window[0], window[1])
        if found:
            hits.append({"item_id": it["id"], "reaction_ids": [r["id"] for r in found]})
            used |= {r["id"] for r in found}
        elif judged and not clipped:
            misses.append({"item_id": it["id"]})
    scene_of = {s["seg_id"]: s["scene_id"] for s in (timeline or {"segments": []})["segments"]}

    def scene_at(t: float) -> str | None:
        seg = max(
            (s for s in transcript["segments"] if s["start"] <= t), key=lambda s: s["start"], default=None
        )
        return scene_of.get(seg["id"]) if seg else None

    rest = [r for r in laughs if r["id"] not in used and r["method"] == "auto"]
    rest.sort(key=lambda r: (r["strength_rank"] is None, r["strength_rank"] or 0))
    surprises = [
        {
            "id": f"gap-{n:04d}",
            "reaction_id": r["id"],
            "t0": r["t0"],
            "t1": r["t1"],
            "scene_id": scene_at(r["t0"]),
            "before_quote": _before_quote(transcript, r["t0"], r["t1"]),
        }
        for n, r in enumerate(rest[:SURPRISE_MAX], start=1)
    ]
    return {
        "schema_version": 1,
        "koza_id": koza_id,
        "judged": judged,
        "hits": hits,
        "misses": misses,
        "surprises": surprises if judged else [],
    }


def counts(doc: dict, surprise_total: int | None = None) -> dict:
    hit, miss = len(doc["hits"]), len(doc["misses"])
    return {
        "hit": hit,
        "miss": miss,
        "surprise": len(doc["surprises"]) if surprise_total is None else surprise_total,
        "hit_rate": round(hit / (hit + miss), 3) if hit + miss else None,
    }


def add_questions(doc: dict, review: dict, questions: dict) -> int:
    """予測なしで反応ありの笑いを、演者への質問にする。同じ笑いを 2 度聞かない。足した数を返す。"""
    n_issue = max((int(i["id"].split("-")[1]) for i in review["issues"]), default=0) + 1
    n_q = max((int(q["id"].split("-")[1]) for q in questions["questions"]), default=0) + 1
    asked = {i["attempt"] for i in review["issues"]}
    added = 0
    for s in doc["surprises"]:
        attempt = f"{clock(s['t0'])} の笑いの所"
        if s["scene_id"] is None or attempt in asked:
            continue
        issue_id = f"issue-{n_issue:04d}"
        review["issues"].append(
            {
                "id": issue_id,
                "role": "noter",
                "kind": "ask_performer",
                "anchor": {"scene_id": s["scene_id"]},
                "attempt": attempt,
                "what_is_missing": s["question"],
                "round": 1,
                "resolved": False,
            }
        )
        questions["questions"].append(
            {
                "id": f"q-{n_q:04d}",
                "issue_id": issue_id,
                "scene_id": s["scene_id"],
                "text": f"{attempt}：{s['question']}",
            }
        )
        n_issue += 1
        n_q += 1
        added += 1
    return added
