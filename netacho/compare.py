"""同じ演目の 2 本を比べる。LLM は使わない。

どの台詞とどの台詞が同じくだりかは、対応づけ役が読む。話し言葉は毎回変わるので、文字の一致では揃わない。
ここでは、その対応が実在して順番どおりかを確かめ、笑い、間、尺を録音の実測から付ける。
役に笑いの位置は見せない。結果から対応を逆算させないため。

比べるのは笑いの量ではなくタイミング。スコアの大きさは使わず、各高座の中で山があるかだけを見る。
未検出を「受けなかった」と扱わない。片方に笑いがほぼ無ければ稽古と見て、本番の笑いを軸に比べる。
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field

from .evidence import MAX_MA_SEC
from .reactions import laugh_humps

MIN_SHARE = 0.3  # 短いほうの文字のうち、対応の付いた割合。これ未満なら比べない
SAME_SEC = 6.0  # くだりの頭からの秒の差がこの範囲なら、同じ所の笑い。窓の幅と刻みから
AFTER_SEC = 4.0  # くだりの言い終わりの後、この秒までの笑いは、そのくだりの笑い
MA_DIFF = 0.8  # 一言の前の間の差。秒
MA_NEAR = 1.0  # 行の始まりのこの範囲で終わる無音を、一言の前の間と見る。秒
PRACTICE_HUMPS = 5  # 笑いの山がこれ未満で、もう 1 本より十分少なければ、客のいない稽古と見る
PRACTICE_RATIO = 0.2
SPOT_GAP = 30.0  # 問う所をこれより近づけない。秒
QUOTE_MAX = 60
ROTATION = ("added", "moved", "ma", "words", "kept", "one_side")
RELATIONS = ("same", "changed", "only_a", "only_b")


def plain(text: str) -> str:
    """字数を数えるための文字。句読点と空白を除く。"""
    return "".join(c for c in unicodedata.normalize("NFKC", text) if c.isalnum())


@dataclass
class Side:
    """1 本の高座。行は文字起こしの区間で、本文は清書。"""

    koza_id: str
    lines: list[dict]
    humps: list[float]
    silences: list[list[float]] = field(default_factory=list)  # VAD の声の無い所
    scored: bool = True  # 笑いのスコアがあるか。無ければ未検出で、笑いが無いとは言えない
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {line["seg_id"]: n for n, line in enumerate(self.lines)}

    def size(self, first: int, last: int) -> int:
        return sum(len(plain(line["text"])) for line in self.lines[first : last + 1])

    @property
    def total(self) -> int:
        return self.size(0, len(self.lines) - 1)

    def laughs_in(self, first: int, last: int) -> list[float]:
        t0, t1 = self.lines[first]["start"], self.lines[last]["end"] + AFTER_SEC
        return [h for h in self.humps if t0 <= h < t1]

    def line_at(self, t: float) -> int:
        found = 0
        for n, line in enumerate(self.lines):
            if line["start"] > t:
                break
            found = n
        return found


def silences_of(vad: list[dict]) -> list[list[float]]:
    """声のある所の間。間は文字起こしの区間から取らない。同じ録音でも切れ目で数秒ずれるので。"""
    return [[left["end"], right["start"]] for left, right in zip(vad, vad[1:], strict=False)]


def side_of(koza_id: str, transcript: dict, lines: dict, reactions: dict, timeline: dict | None) -> Side:
    """`lines` は清書の行（`view.clean_lines`）。清書の無い区間は文字起こしのまま。"""
    scene_of = {s["seg_id"]: s["scene_id"] for s in (timeline or {"segments": []})["segments"]}
    rows = []
    for seg in transcript["segments"]:
        text = lines[seg["id"]]["text"] if seg["id"] in lines else seg["text"].strip()
        if plain(text):
            rows.append(
                {
                    "seg_id": seg["id"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": text,
                    "scene_id": scene_of.get(seg["id"]),
                }
            )
    found = laugh_humps(reactions)
    humps = [sum(h) / len(h) for h in found or []]
    return Side(koza_id, rows, humps, silences_of(transcript.get("vad") or []), found is not None)


def check_passages(draft: dict, a: Side, b: Side) -> tuple[list[dict], int]:
    """役の対応を確かめる。(残ったくだり, 落とした数)。実在しない区間と、順番の前後した区間は落とす。"""
    kept: list[dict] = []
    dropped = 0
    last_a = last_b = -1
    for row in draft["passages"]:
        rel = row["relation"]
        need_a, need_b = rel != "only_b", rel != "only_a"
        span_a = _span(a, row["a_first"], row["a_last"]) if need_a else None
        span_b = _span(b, row["b_first"], row["b_last"]) if need_b else None
        bad = (need_a and span_a is None) or (need_b and span_b is None)
        if bad or (span_a and span_a[0] <= last_a) or (span_b and span_b[0] <= last_b):
            dropped += 1
            continue
        last_a = span_a[1] if span_a else last_a
        last_b = span_b[1] if span_b else last_b
        kept.append({"relation": rel, "a": span_a, "b": span_b})
    return kept, dropped


def _span(side: Side, first: str | None, last: str | None) -> tuple[int, int] | None:
    if first not in side.index or last not in side.index or side.index[first] > side.index[last]:
        return None
    return side.index[first], side.index[last]


def _quote(side: Side, n: int) -> dict:
    line = side.lines[n]
    return {"t": line["start"], "end": line["end"], "quote": line["text"][-QUOTE_MAX:]}


def _pause_before(side: Side, n: int) -> float:
    """行の始まりの直前で終わる、一番長い無音。間と呼べない長い空白は入れない。"""
    t = side.lines[n]["start"]
    near = [t1 - t0 for t0, t1 in side.silences if abs(t1 - t) <= MA_NEAR and t1 - t0 <= MAX_MA_SEC]
    return round(max(near, default=0.0), 2)


def _laugh_before(side: Side, n: int) -> bool:
    """前の行の所に笑いの山があるか。あれば無音に笑いの長さが混ざるので、間と言えない。"""
    if n == 0:
        return False
    t0, t1 = side.lines[n - 1]["start"], side.lines[n]["start"]
    return any(t0 <= h < t1 for h in side.humps)


def _ma(a: Side, b: Side, i: int, j: int) -> dict | None:
    """くだりの頭の間の差。前に笑いがあれば、笑いの長さが混ざるので言わない。"""
    if _laugh_before(a, i) or _laugh_before(b, j):
        return None
    ma_a, ma_b = _pause_before(a, i), _pause_before(b, j)
    return {"ma_a": ma_a, "ma_b": ma_b} if abs(ma_a - ma_b) >= MA_DIFF else None


def _spot(
    kind: str, a: Side, i: int, b: Side, j: int | None, laugh_a, laugh_b, extra: dict | None = None
) -> dict:
    return {
        "kind": kind,
        "scene_id": a.lines[i]["scene_id"],
        "a": _quote(a, i),
        "b": None if j is None else _quote(b, j),
        "laugh_a": laugh_a,
        "laugh_b": laugh_b,
        **(extra or {}),
    }


def _same_place(i: int, i0: int, i1: int, j0: int, j1: int) -> int:
    """くだりの中の同じ割合の行。稽古の側の、本番の笑いに当たる台詞を見せる。"""
    return j0 + round((i - i0) / max(i1 - i0, 1) * (j1 - j0))


def _paired_spots(p: dict, a: Side, b: Side, practice: bool) -> list[dict]:
    """同じくだりの所。笑いがあればタイミングを見る。"""
    i0, i1 = p["a"]
    j0, j1 = p["b"]
    same_words = "kept" if p["relation"] == "same" else "words"
    head_a, head_b = a.lines[i0]["start"], b.lines[j0]["start"]
    laughs_a, laughs_b = a.laughs_in(i0, i1), b.laughs_in(j0, j1)
    if not laughs_a and not laughs_b:
        return [_spot("changed", a, i0, b, j0, None, None)] if p["relation"] == "changed" else []
    spots: list[dict] = []
    used: set[float] = set()
    ma = _ma(a, b, i0, j0)
    for n, t in enumerate(laughs_a):
        i = a.line_at(t)
        if practice:  # 稽古に笑いが無いのは当たり前。本番の笑いを軸に、言葉と間を見る
            kind = "ma" if ma and n == 0 else same_words  # 間はくだりの頭の話なので、1 くだり 1 件
            spots.append(
                _spot(kind, a, i, b, _same_place(i, i0, i1, j0, j1), t, None, ma if n == 0 else None)
            )
            continue
        near = min(laughs_b, key=lambda h: abs((h - head_b) - (t - head_a)), default=None)
        if near is None:
            spots.append(_spot("one_side", a, i, b, j0, t, None))
            continue
        used.add(near)
        if abs((near - head_b) - (t - head_a)) > SAME_SEC:
            spots.append(_spot("moved", a, i, b, b.line_at(near), t, near))
            continue
        kind = "ma" if ma and n == 0 else same_words
        spots.append(_spot(kind, a, i, b, b.line_at(near), t, near, ma if n == 0 else None))
    if not practice:
        for t in laughs_b:
            if t not in used:
                spots.append(_spot("one_side", a, i0, b, b.line_at(t), None, t))
    return spots


def _unique(spots: list[dict]) -> list[dict]:
    """同じ所は 1 件にする。くだりの境目の笑いは、前のくだりの後ろと次のくだりの頭で 2 度拾われる。"""
    seen: set[tuple] = set()
    laughs: set[float] = set()
    out = []
    for s in spots:
        key = (s["kind"], s["a"]["t"], (s["b"] or {}).get("t"))
        laugh = s["laugh_a"]
        if key in seen or (laugh is not None and laugh in laughs):
            continue
        seen.add(key)
        if laugh is not None:
            laughs.add(laugh)
        out.append(s)
    return out


def _spot_id(spot: dict) -> str:
    """中身から決める id。やり直しても、問いが別の所を指さないように。"""
    key = f"{spot['kind']}|{spot['a']['t']}|{(spot['b'] or {}).get('t')}"
    return f"spot-{int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)}"


def is_practice(a: Side, b: Side) -> bool:
    """2 本目に笑いがほぼ無いか。客のいない稽古なら、笑いの有無を比べても意味が無い。"""
    if not a.scored or not b.scored:  # スコアが無いのは未検出。客がいないとは言えない
        return False
    return len(b.humps) < PRACTICE_HUMPS and len(b.humps) < PRACTICE_RATIO * len(a.humps)


def compare(a: Side, b: Side, draft: dict, titles: dict) -> dict:
    """役の対応に、録音の実測を付ける。揃わなければ `comparable` を false にして、所は出さない。"""
    passages, dropped = check_passages(draft, a, b)
    paired = [p for p in passages if p["a"] and p["b"]]
    covered = sum(b.size(*p["b"]) for p in paired)
    short = min(a.total, b.total)
    share = round(min(covered / short, 1.0), 3) if short else 0.0
    laughs = bool(a.humps) and a.scored and b.scored
    practice = laughs and is_practice(a, b)
    head = {
        "schema_version": 1,
        "koza_a": a.koza_id,
        "koza_b": b.koza_id,
        "share": share,
        "comparable": share >= MIN_SHARE,
        "laughs_compared": laughs and (practice or bool(b.humps)),
        "practice": practice,
        "humps_a": len(a.humps),
        "humps_b": len(b.humps),
        "passages": {rel: sum(p["relation"] == rel for p in passages) for rel in RELATIONS},
        "dropped": dropped,
    }
    if not head["comparable"]:
        return {**head, "scenes": [], "spots": [], "counts": {}}
    found: list[dict] = []
    # 最初と最後の対応の外は枕や結び。もう 1 本に無いのは当たり前なので、足した言葉と呼ばない
    rows = [n for n, p in enumerate(passages) if p["a"] and p["b"]]
    inside = range(rows[0], rows[-1] + 1) if rows else range(0)
    for n, p in enumerate(passages):
        if not head["laughs_compared"] and p["relation"] == "changed":
            found.append(_spot("changed", a, p["a"][0], b, p["b"][0], None, None))
        elif not head["laughs_compared"]:
            continue
        elif p["a"] and p["b"]:
            found += _paired_spots(p, a, b, practice)
        elif p["a"] and n in inside:
            first, last = p["a"]
            for t in a.laughs_in(first, last)[:1]:
                found.append(_spot("added", a, a.line_at(t), b, None, t, None))
    found = [s for s in found if s["scene_id"] is not None]
    found.sort(key=lambda s: s["a"]["t"])
    spots = [{"id": _spot_id(s), **s} for s in _unique(found)]
    kinds = (*ROTATION, "changed")
    return {
        **head,
        "scenes": scene_rows(a, paired, titles),
        "spots": spots,
        "counts": {kind: sum(s["kind"] == kind for s in spots) for kind in kinds},
    }


def scene_rows(a: Side, paired: list[dict], titles: dict) -> list[dict]:
    """場面ごとに、どれだけ対応が付いたかと、1 本目の尺。"""
    shared = {n for p in paired for n in range(p["a"][0], p["a"][1] + 1)}
    rows: dict[str, dict] = {}
    for n, line in enumerate(a.lines):
        if line["scene_id"] is None:
            continue
        row = rows.setdefault(
            line["scene_id"], {"chars": 0, "shared": 0, "t0": line["start"], "t1": line["end"]}
        )
        size = len(plain(line["text"]))
        row["chars"] += size
        row["shared"] += size if n in shared else 0
        row["t1"] = max(row["t1"], line["end"])
    return [
        {
            "scene_id": sid,
            "title": titles.get(sid, ""),
            "seconds": round(row["t1"] - row["t0"], 1),
            "share": round(row["shared"] / row["chars"], 3) if row["chars"] else 0.0,
            "compared": row["shared"] > 0,
        }
        for sid, row in rows.items()
    ]


def select(spots: list[dict], limit: int) -> list[dict]:
    """問う所を選ぶ。種類を順に回して、1 種類で埋めない。近い所は重ねない。"""
    queues = {kind: [s for s in spots if s["kind"] == kind] for kind in ROTATION}
    chosen: list[dict] = []
    while len(chosen) < limit and any(queues.values()):
        for kind in ROTATION:
            while queues[kind]:
                spot = queues[kind].pop(0)
                if all(abs(spot["a"]["t"] - c["a"]["t"]) >= SPOT_GAP for c in chosen):
                    chosen.append(spot)
                    break
            if len(chosen) == limit:
                break
    return sorted(chosen, key=lambda s: s["a"]["t"])
