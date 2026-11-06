"""同じ演目の 2 本を比べる。LLM は使わない。

どの台詞とどの台詞が同じくだりかは、対応づけ役が読む。話し言葉は毎回変わるので、文字の一致では揃わない。
ここでは、その対応が実在して順番どおりかを確かめ、笑い、間、尺を録音の実測から付ける。
役に笑いの位置は見せない。結果から対応を逆算させないため。

比べるのは笑いの量ではなくタイミング。スコアの大きさは使わず、各高座の中で山があるかだけを見る。
枕は会ごとに変わるので、行どうしは比べない。役の読んだ話題に、位置と笑いの山の数を付ける。
未検出を「受けなかった」と扱わない。片方に笑いがほぼ無ければ稽古と見て、本番の笑いを軸に比べる。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from .evidence import MAX_MA_SEC
from .reactions import laugh_humps

MIN_SHARE = 0.3  # 枕を除いた短いほうの文字のうち、対応の付いた割合。これ未満なら比べない
SAME_SEC = 6.0  # くだりの頭からの秒の差がこの範囲なら、同じ所の笑い。窓の幅と刻みから
AFTER_SEC = 4.0  # くだりの言い終わりの後、この秒までの笑いは、そのくだりの笑い
MA_DIFF = 0.8  # 一言の前の間の差。秒
MA_NEAR = 1.0  # 行の始まりのこの範囲で終わる無音を、一言の前の間と見る。秒
PRACTICE_HUMPS = 5  # 笑いの山がこれ未満で、もう 1 本より十分少なければ、客のいない稽古と見る
PRACTICE_RATIO = 0.2
SPOT_GAP = 30.0  # 問う所をこれより近づけない。秒
PAIR_SEC = 30.0  # もう 1 本の山と組む距離の上限。これより遠い山は同じ笑いと見ない。秒
LINES_B_MAX = 40  # 聞き役に見せる、もう 1 本の行の上限。長いくだりで費用が膨らまないように
CONTEXT_BEFORE, CONTEXT_AFTER = 8, 2  # 笑いの前後に聞き役へ見せる行の数
CONTEXT_EDGE = 3  # もう 1 本の同じくだりの前後に足す行の数
KINDS = ("added", "moved", "words", "kept", "one_side", "ma", "changed", "makura")
# 間（ま）ではない語。「間」の字だけでは判定できない
NOT_PAUSE = re.compile(
    r"時間|人間|仲間|合間|間違|瞬間|世間|期間|空間|間に合|昼間|夜間|年間|週間|月間|日間|民間|中間|区間|隙間|間隔|間際|間柄|間接|手間|居間|間もなく|その間"
)
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


def check_makura(draft: dict, a: Side, b: Side) -> tuple[int, int, list[dict], int]:
    """役の読んだ枕を確かめる。(1 本目の最後の行, 2 本目の, 話題, 落とした数)。枕が無ければ -1。"""
    head = draft.get("makura") or {"a_last": None, "b_last": None, "topics": []}
    end_a, end_b = a.index.get(head["a_last"], -1), b.index.get(head["b_last"], -1)
    dropped = sum(
        seg is not None and seg not in side.index for seg, side in ((head["a_last"], a), (head["b_last"], b))
    )
    topics = []
    for row in head["topics"]:
        span_a = _span(a, row["a_first"], row["a_last"])
        span_b = _span(b, row["b_first"], row["b_last"])
        span_a = span_a if span_a and span_a[1] <= end_a else None  # 枕の外の行は話題と呼ばない
        span_b = span_b if span_b and span_b[1] <= end_b else None
        if span_a is None and span_b is None:
            dropped += 1
            continue
        topics.append(
            {"topic": row["topic"].strip(), "prepares": row["prepares"], "span_a": span_a, "span_b": span_b}
        )
    return end_a, end_b, topics, dropped


def _topic_side(side: Side, span: tuple[int, int] | None) -> dict | None:
    if span is None:
        return None
    first, last = span
    return {
        "t": side.lines[first]["start"],
        "end": side.lines[last]["end"],
        "humps": len(side.laughs_in(first, last)) if side.scored else None,  # 無ければ測っていない
    }


def _makura_spots(end_a: int, topics: list[dict], a: Side, b: Side) -> list[dict]:
    """枕の笑いの所。もう 1 本に無いのは当たり前なので、言葉の差としては扱わない。"""
    spots = []
    first_scene = next((x["scene_id"] for x in a.lines if x["scene_id"]), None)
    for t in a.laughs_in(0, end_a):
        i = a.line_at(t)
        if i > end_a:  # 噺に入ってからの笑いは、噺の所で見る
            continue
        topic = next((x for x in topics if x["span_a"] and x["span_a"][0] <= i <= x["span_a"][1]), None)
        span_a = topic["span_a"] if topic else (0, end_a)
        span_b = topic["span_b"] if topic else None
        spans = {"span_a": list(span_a), "span_b": list(span_b) if span_b else None}
        spot = _spot("makura", a, i, b, None, t, None, spans)
        spots.append({**spot, "scene_id": spot["scene_id"] or first_scene})  # 枕に場面が無くても問える
    return spots


def check_passages(
    draft: dict, a: Side, b: Side, start: tuple[int, int] = (-1, -1)
) -> tuple[list[dict], int]:
    """役の対応を確かめる。(残ったくだり, 落とした数)。

    実在しない区間と、順番の前後した区間は落とす。`start` は枕の最後の行で、ここまでの行は対応づけない。
    """
    kept: list[dict] = []
    dropped = 0
    last_a, last_b = start
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
    return {"t": line["start"], "end": line["end"], "quote": line["text"]}


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


def _paired_spots(p: dict, a: Side, b: Side, practice: bool) -> list[dict]:
    """同じくだりの所を、候補として出す。どの行が当たるかは聞き役が読む。"""
    i0, i1 = p["a"]
    j0, j1 = p["b"]
    same_words = "kept" if p["relation"] == "same" else "words"
    head_a, head_b = a.lines[i0]["start"], b.lines[j0]["start"]
    laughs_a, laughs_b = a.laughs_in(i0, i1), b.laughs_in(j0, j1)
    span = {"span_a": [i0, i1], "span_b": [j0, j1]}
    if not laughs_a and not laughs_b:
        return [_spot("changed", a, i0, b, j0, None, None, span)] if p["relation"] == "changed" else []
    # テンポの違う 2 本は、頭からの秒が積もってずれる。くだりの長さの比で写してから比べる
    length_a = a.lines[i1]["end"] - head_a
    length_b = b.lines[j1]["end"] - head_b
    # 区間が極端に短いと比が暴れる。話す速さの差はこの範囲に収まる
    scale = min(max(length_b / length_a, 0.5), 2.0) if length_a > 0 else 1.0
    spots: list[dict] = []
    used: set[float] = set()
    for t in laughs_a:
        i = a.line_at(t)
        if practice:  # 稽古に笑いが無いのは当たり前。本番の笑いを軸に、言葉と間を見る
            spots.append(_spot(same_words, a, i, b, j0, t, None, span))
            continue
        expected = head_b + (t - head_a) * scale
        near = min(laughs_b, key=lambda h: abs(h - expected), default=None)
        if near is None or abs(near - expected) > PAIR_SEC:
            spots.append(_spot("one_side", a, i, b, j0, t, None, span))
            continue
        used.add(near)
        moved = abs(near - expected) > SAME_SEC
        spots.append(_spot("moved" if moved else same_words, a, i, b, b.line_at(near), t, near, span))
    if not practice:
        for t in laughs_b:
            if t not in used:
                spots.append(_spot("one_side", a, i0, b, b.line_at(t), None, t, span))
    return spots


def context_rows(spot: dict, a: Side, b: Side) -> dict:
    """聞き役に見せる台詞。笑いの前後と、もう 1 本の同じくだり全体。間は実測の秒。"""
    i = a.line_at(spot["laugh_a"] if spot["laugh_a"] is not None else spot["a"]["t"])
    i0, i1 = spot["span_a"]
    rows_a = [
        {
            "seg_id": a.lines[n]["seg_id"],
            "text": a.lines[n]["text"],
            "間": _pause_before(a, n),
            **({"笑いの山": True} if n == i and spot["laugh_a"] is not None else {}),
        }
        for n in range(
            max(i - CONTEXT_BEFORE, i0 - 2, 0), min(i + CONTEXT_AFTER, i1 + 2, len(a.lines) - 1) + 1
        )
    ]
    rows_b = []
    if spot["span_b"]:
        j0, j1 = spot["span_b"]
        first, last = max(j0 - CONTEXT_EDGE, 0), min(j1 + CONTEXT_EDGE, len(b.lines) - 1)
        if last - first >= LINES_B_MAX:  # 長いくだりは、同じ割合の位置の前後だけ見せる
            if spot.get("laugh_b") is not None:  # 笑いの場所が分かれば、そこを見せる
                center = b.line_at(spot["laugh_b"])
            else:
                center = first + round((i - i0) / max(i1 - i0, 1) * (last - first))
            first = max(first, min(center - LINES_B_MAX // 2, last - LINES_B_MAX + 1))
            last = first + LINES_B_MAX - 1
        rows_b = [
            {"seg_id": b.lines[n]["seg_id"], "text": b.lines[n]["text"], "間": _pause_before(b, n)}
            for n in range(first, last + 1)
        ]
    return {"lines_a": rows_a, "lines_b": rows_b}


def settle(spot: dict, row: dict, a: Side, b: Side, shown: dict) -> dict | None:
    """聞き役の決めた行を確かめて、新しい所を返す。元の所は書き換えない。

    行は見せた中の物だけを認める。間のことを聞くなら、その 2 行の前の無音の差を実測で確かめる。
    """
    ids_a = {r["seg_id"] for r in shown["lines_a"]}
    ids_b = {r["seg_id"] for r in shown["lines_b"]}
    i = a.index.get(row["a_seg"]) if row["a_seg"] in ids_a else None
    if i is None:
        return None
    j = b.index.get(row["b_seg"]) if row["b_seg"] in ids_b else None
    if (row["relation"] == "none") != (j is None):
        return None
    if row["relation"] == "none" and spot.get("laugh_b") is not None:
        return None  # もう 1 本にも笑いの山があるのに「無い」とは言えない
    makura = spot["kind"] == "makura"
    ma = None if j is None or makura else _ma(a, b, i, j)  # 枕は別の話なので、間を比べられない
    if speaks_of_pause(row["text"]) and ma is None:
        return None
    new = {**spot, "a": _quote(a, i), "b": None if j is None else _quote(b, j)}
    if not makura and (row["relation"] == "none" or spot["kind"] in ("kept", "words", "added")):
        new["kind"] = {"same": "kept", "changed": "words", "none": "added"}[row["relation"]]
    if speaks_of_pause(row["text"]):
        new.update(kind="ma", **ma)
    return new


def speaks_of_pause(text: str) -> bool:
    """問いが間（ま）の話か。引用の中と、「時間」のような語は見ない。"""
    return "間" in NOT_PAUSE.sub("", re.sub(r"[「『].*?[」』]", "", text))


def count(spots: list[dict]) -> dict:
    return {kind: sum(s["kind"] == kind for s in spots) for kind in KINDS}


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
    end_a, end_b, topics, lost = check_makura(draft, a, b)
    passages, dropped = check_passages(draft, a, b, (end_a, end_b))
    paired = [p for p in passages if p["a"] and p["b"]]
    covered = sum(b.size(*p["b"]) for p in paired)
    short = min(a.size(end_a + 1, len(a.lines) - 1), b.size(end_b + 1, len(b.lines) - 1))
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
        "dropped": dropped + lost,
        "makura": [
            {
                "topic": x["topic"],
                "prepares": x["prepares"],
                "a": _topic_side(a, x["span_a"]),
                "b": _topic_side(b, x["span_b"]),
            }
            for x in topics
        ],
    }
    if not head["comparable"]:
        return {**head, "scenes": [], "spots": [], "counts": {}}
    found: list[dict] = []
    if head["laughs_compared"] and end_a >= 0:
        found += _makura_spots(end_a, topics, a, b)
    # 最初と最後の対応の外は枕や結び。もう 1 本に無いのは当たり前なので、足した言葉と呼ばない
    rows = [n for n, p in enumerate(passages) if p["a"] and p["b"]]
    inside = range(rows[0], rows[-1] + 1) if rows else range(0)
    for n, p in enumerate(passages):
        if not head["laughs_compared"] and p["relation"] == "changed":
            span = {"span_a": list(p["a"]), "span_b": list(p["b"])}
            found.append(_spot("changed", a, p["a"][0], b, p["b"][0], None, None, span))
        elif not head["laughs_compared"]:
            continue
        elif p["a"] and p["b"]:
            found += _paired_spots(p, a, b, practice)
        elif p["a"] and n in inside:
            first, last = p["a"]
            for t in a.laughs_in(first, last)[:1]:
                found.append(
                    _spot(
                        "added", a, a.line_at(t), b, None, t, None, {"span_a": [first, last], "span_b": None}
                    )
                )
    found = [s for s in found if s["scene_id"] is not None]
    found.sort(key=lambda s: s["a"]["t"])
    spots = [{"id": _spot_id(s), **s} for s in _unique(found)]
    return {
        **head,
        "scenes": scene_rows(a, paired, titles, end_a),
        "spots": spots,
        "counts": count(spots),
    }


def scene_rows(a: Side, paired: list[dict], titles: dict, end_a: int = -1) -> list[dict]:
    """場面ごとに、どれだけ対応が付いたかと、1 本目の尺。枕は話題で比べるので入れない。"""
    shared = {n for p in paired for n in range(p["a"][0], p["a"][1] + 1)}
    rows: dict[str, dict] = {}
    for n, line in enumerate(a.lines):
        if line["scene_id"] is None or n <= end_a:
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


def candidates(spots: list[dict]) -> list[dict]:
    """聞き役に見せる所。笑いのあった所を全部。どれを聞くかは役に読ませる。"""
    return [s for s in spots if s["laugh_a"] is not None or s["laugh_b"] is not None]
