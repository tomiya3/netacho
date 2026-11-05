"""根拠の検証。構成作家役の下書きを、区間と kind の約束に照らす。LLM は使わない。

落ちた項目は理由ごとに数える。残った項目には、時刻と引用を区間から作って付ける。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .schema import HANASHI_KINDS, KATA_KINDS, PAYOFF_KINDS

MAX_SPAN_SEC = 180.0  # 1 項目の根拠の長さの上限。暫定
MAX_MA_SEC = 5.0  # これより長い空白は間と呼ばない。文字起こしの抜けか客の反応のことが多い。暫定
MA_SECONDS_TOLERANCE = 0.3  # 間の文に書いた秒数と pause_after の許容差
MIN_PAUSE_SEC = 0.5  # 間の根拠に使える pause_after の下限。構成作家役にもこの長さから見せる。暫定
INTERNAL_ID = re.compile(r"(?<![a-z])(?:item|scene|char|seg|issue|cand|gap|react)[-_]\d+", re.IGNORECASE)


class Segments:
    """transcript の区間を ID で引く。"""

    def __init__(self, transcript: dict) -> None:
        self.koza_id = transcript["koza_id"]
        self.segs = transcript["segments"]
        self.index = {s["id"]: i for i, s in enumerate(self.segs)}

    def ok(self, span: list[str]) -> bool:
        first, last = span
        return first in self.index and last in self.index and self.index[first] <= self.index[last]

    def between(self, span: list[str]) -> list[dict]:
        return self.segs[self.index[span[0]] : self.index[span[1]] + 1]

    def resolve(self, span: list[str]) -> dict | None:
        """時刻と引用。引用が空なら None（根拠にならない）。"""
        segs = self.between(span)
        quote = "".join(s["text"] for s in segs).strip()
        if not quote:
            return None
        return {"segs": list(span), "t0": segs[0]["start"], "t1": max(s["end"] for s in segs), "quote": quote}


def has_internal_id(text: str) -> bool:
    """演者の読む文に内部の番号が残っているか。全角も見る。"""
    return INTERNAL_ID.search(unicodedata.normalize("NFKC", text)) is not None


def is_blank(pause: float) -> bool:
    """間と呼ばない長い空白か。構成作家役への表示と検証で同じ判定を使う（表示は 0.1 秒単位）。"""
    return round(pause, 1) > MAX_MA_SEC


def _ma_problem(item: dict, pause: float) -> str | None:
    if pause < MIN_PAUSE_SEC:
        return "ma_no_pause"
    if is_blank(pause):
        return "ma_too_long"
    # 文に秒数を書くなら、実際の pause_after と合うこと（照合役には確かめられない）
    text = unicodedata.normalize("NFKC", item["text"])  # 全角の数字も見る
    written = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*秒", text)
    if written and not any(abs(float(x) - pause) <= MA_SECONDS_TOLERANCE for x in written):
        return "ma_wrong_seconds"
    return None


def _book(item: dict) -> str:
    if item["kind"] in HANASHI_KINDS:
        return "hanashi"
    if item["kind"] in KATA_KINDS:
        return "kata"
    return item["book"]


def _problem(item: dict, segs: Segments, tool_ids: set[str], performer_ids: set[str]) -> str | None:
    """落とす理由。問題なければ None。"""
    if item["id"] in performer_ids:
        return "performer_item"
    if item["id"] is not None and item["id"] not in tool_ids:
        return "unknown_id"
    if item["kind"] in {"上下", "staging"}:
        return "kind_not_allowed"
    span = item["source"]["segs"]
    if not segs.ok(span):
        return "bad_seg"
    resolved = segs.resolve(span)
    if resolved is None:
        return "empty_quote"
    if resolved["t1"] - resolved["t0"] > MAX_SPAN_SEC:
        return "span_too_long"
    if _book(item) == "kata" and item["basis"] != "heard":
        return "kata_not_heard"
    if item["kind"] == "間":  # 根拠の最後の区間の後に間があること（長い範囲のどこかでは足りない）
        problem = _ma_problem(item, segs.between(span)[-1]["pause_after"] or 0)
        if problem:
            return problem
    if item["kind"] in PAYOFF_KINDS:
        if item["payoff"] is None:
            return "no_payoff"
        pay = item["payoff"]["segs"]
        if not segs.ok(pay):
            return "bad_payoff_seg"
        payoff = segs.resolve(pay)
        if payoff is None:
            return "empty_payoff"
        if payoff["t1"] - payoff["t0"] > MAX_SPAN_SEC:
            return "payoff_too_long"
        if segs.index[pay[0]] < segs.index[span[0]]:
            return "payoff_before_source"
    return None


def verify_items(
    draft: dict, transcript: dict, *, tool_ids: set[str], performer_ids: set[str]
) -> tuple[list[dict], Counter]:
    """(残った項目, 理由ごとの落とした数)。残った項目は正本の形（id は既存か None）に book を添える。"""
    segs = Segments(transcript)
    kept = []
    dropped: Counter = Counter()
    seen: set[str] = set()
    for item in draft["items"]:
        problem = "duplicate_id" if item["id"] in seen else _problem(item, segs, tool_ids, performer_ids)
        if problem:
            dropped[problem] += 1
            continue
        if item["id"] is not None:
            seen.add(item["id"])
        out = {
            "id": item["id"],
            "book": _book(item),
            "kind": item["kind"],
            "priority": item["priority"],
            "text": item["text"],
            "basis": item["basis"],
            "source": {"koza_id": segs.koza_id, **segs.resolve(item["source"]["segs"])},
            "edited_by": "tool",
            "drafted_by": "writer",
            "history": [],
        }
        if item["kind"] in PAYOFF_KINDS:  # それ以外の kind に付いた回収は捨てる
            out["payoff"] = segs.resolve(item["payoff"]["segs"])
        kept.append(out)
    return kept, dropped


def verify_scenes(draft: dict, transcript: dict) -> tuple[list[dict], int]:
    """(残った場面, 落とした数)。区間が壊れた場面は使えない。"""
    segs = Segments(transcript)
    kept, dropped = [], 0
    for scene in draft["scenes"]:
        if segs.ok(scene["segs"]):
            kept.append({**scene, "koza_id": segs.koza_id})
        else:
            dropped += 1
    return sorted(kept, key=lambda s: s["order"]), dropped


def timeline(scenes: list[dict], transcript: dict) -> dict:
    """区間を場面に割り当てる。場面の外は null。話者は入れない（札は話者に依存しない）。"""
    segs = Segments(transcript)
    koza_id = transcript["koza_id"]
    scene_of: dict[str, str] = {}
    for scene in scenes:
        if scene["koza_id"] != koza_id or not segs.ok(scene["segs"]):
            continue
        for s in segs.between(scene["segs"]):
            scene_of.setdefault(s["id"], scene["id"])
    return {
        "schema_version": 1,
        "koza_id": koza_id,
        "segments": [
            {"seg_id": s["id"], "scene_id": scene_of.get(s["id"]), "speaker": None, "confidence": None}
            for s in segs.segs
        ],
    }
