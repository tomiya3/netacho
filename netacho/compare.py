"""同じ演目の 2 本を、清書の台詞で揃えて比べる。LLM は使わない。

比べるのは笑いの量ではなくタイミング。録音の条件で量は変わるので、スコアの大きさは使わず、
各高座の中で山があるかだけを見る。時刻や場面の ID には頼らず、清書の文字で 2 本を揃える。
検出の窓は 5 秒、刻みは 2 秒なので、比べられるのは「どの台詞の後か」まで。
未検出を「受けなかった」と扱わない。
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .reactions import laugh_humps

MIN_BLOCK = 8  # これより短い一致は、偶然として使わない。字数
MIN_SHARE = 0.3  # 文字がこれしか揃わなければ、2 本を比べない
SAME_SEC = 6.0  # 笑いがこの範囲なら同じ所。窓の幅と刻みから
MOVED_SEC = 20.0  # この範囲なら、来る所がずれたと見る
KEPT_RATIO = 0.9  # 行の文字がこれだけ揃えば、言葉を変えていない
MA_DIFF = 0.8  # 一言の前の間の差。秒
MA_NEAR = 1.0  # 行の始まりのこの範囲の無音を、一言の前の間と見る。区間の切れ目が揺れる分。秒
MIN_WORDS = 30  # 言葉を変えた所と見る最小の字数。清書の揺れを拾わない長さ
SPOT_GAP = 30.0  # 問う所をこれより近づけない。秒
ROTATION = ("moved", "ma", "kept", "one_side", "words")
QUOTE_MAX = 60


def plain(text: str) -> str:
    """揃えるための文字。句読点と空白を除く。"""
    return "".join(c for c in unicodedata.normalize("NFKC", text) if c.isalnum())


@dataclass
class Side:
    """1 本の高座。行は文字起こしの区間で、本文は清書。"""

    koza_id: str
    lines: list[dict]
    humps: list[float]
    silences: list[list[float]] = field(default_factory=list)  # VAD の声の無い所
    chars: str = ""
    owner: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        for n, line in enumerate(self.lines):
            text = plain(line["text"])
            self.chars += text
            self.owner += [n] * len(text)

    def line_at(self, t: float) -> int | None:
        """t の時点で語られていた、または直前に語り終えた行。"""
        found = None
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
    humps = [sum(h) / len(h) for h in laugh_humps(reactions) or []]
    return Side(koza_id, rows, humps, silences_of(transcript.get("vad") or []))


@dataclass
class Alignment:
    share: float
    pairs: dict[int, int]  # a の行 → いちばん多く揃った b の行
    matched: dict[int, float]  # a の行のうち、揃った文字の割合
    gaps: list[tuple[int, int, int, int]]  # 揃わなかった所。a の始まりと終わり、b の始まりと終わり


def align(a: Side, b: Side) -> Alignment:
    blocks = [
        m
        for m in SequenceMatcher(None, a.chars, b.chars, autojunk=False).get_matching_blocks()
        if m.size >= MIN_BLOCK
    ]
    total = len(a.chars) + len(b.chars)
    share = 2 * sum(m.size for m in blocks) / total if total else 0.0
    votes: dict[int, dict[int, int]] = {}
    for m in blocks:
        for k in range(m.size):
            row = votes.setdefault(a.owner[m.a + k], {})
            j = b.owner[m.b + k]
            row[j] = row.get(j, 0) + 1
    sizes = {n: len(plain(line["text"])) for n, line in enumerate(a.lines)}
    pairs = {i: max(row, key=row.get) for i, row in votes.items()}
    matched = {i: sum(row.values()) / sizes[i] for i, row in votes.items()}
    gaps = []
    for left, right in zip(blocks, blocks[1:], strict=False):
        gaps.append((left.a + left.size, right.a, left.b + left.size, right.b))
    return Alignment(round(share, 3), pairs, matched, gaps)


def _quote(side: Side, n: int) -> dict:
    line = side.lines[n]
    return {"t": line["start"], "end": line["end"], "quote": line["text"][-QUOTE_MAX:]}


def _expected(a: Side, b: Side, i: int, j: int, t: float) -> float:
    """a の時刻 t に当たる、b の時刻。行の中は始まりから、行の後は終わりからの秒で写す。"""
    if t > a.lines[i]["end"]:  # 笑いは言い終えた後に来ることが多い
        return b.lines[j]["end"] + t - a.lines[i]["end"]
    return b.lines[j]["start"] + min(t - a.lines[i]["start"], b.lines[j]["end"] - b.lines[j]["start"])


def _pause_before(side: Side, n: int) -> float:
    """行の始まりの近くの、一番長い無音。"""
    t = side.lines[n]["start"]
    near = [t1 - t0 for t0, t1 in side.silences if t0 < t + MA_NEAR and t1 > t - MA_NEAR]
    return round(max(near, default=0.0), 2)


def _laugh_before(side: Side, n: int) -> bool:
    """前の行の所に笑いの山があるか。あれば無音に笑いの長さが混ざるので、間と言えない。"""
    if n == 0:
        return False
    t0, t1 = side.lines[n - 1]["start"], side.lines[n]["start"]
    return any(t0 <= h < t1 for h in side.humps)


def _laugh_spot(a: Side, b: Side, al: Alignment, t: float) -> dict | None:
    i = a.line_at(t)
    if i is None or i not in al.pairs:
        return None
    j = al.pairs[i]
    expected = _expected(a, b, i, j, t)
    near = min(b.humps, key=lambda h: abs(h - expected), default=None)
    gap = None if near is None else abs(near - expected)
    spot = {"scene_id": a.lines[i]["scene_id"], "a": _quote(a, i), "b": _quote(b, j)}
    if gap is None or gap > MOVED_SEC:
        return {**spot, "kind": "one_side", "laugh_a": t, "laugh_b": None}
    if gap > SAME_SEC:
        return {**spot, "kind": "moved", "laugh_a": t, "laugh_b": near}
    ma_a, ma_b = _pause_before(a, i), _pause_before(b, j)
    joined = al.pairs.get(i - 1) == j - 1  # 前の行も揃っているときだけ、同じ切れ目の間と言える
    quiet = not _laugh_before(a, i) and not _laugh_before(b, j)
    if joined and quiet and abs(ma_a - ma_b) >= MA_DIFF:
        return {**spot, "kind": "ma", "laugh_a": t, "laugh_b": near, "ma_a": ma_a, "ma_b": ma_b}
    if al.matched.get(i, 0.0) >= KEPT_RATIO:
        return {**spot, "kind": "kept", "laugh_a": t, "laugh_b": near}
    return {**spot, "kind": "words", "laugh_a": t, "laugh_b": near}


def _word_spots(a: Side, b: Side, al: Alignment) -> list[dict]:
    """笑いに関わらず、言葉を変えた所。笑いの近くの言葉の差は、笑いの比較の方で拾う。"""
    spots = []
    for a0, a1, b0, b1 in al.gaps:
        if a1 - a0 < MIN_WORDS or b1 - b0 < MIN_WORDS:
            continue
        i, j = a.owner[a0], b.owner[b0]
        spots.append(
            {
                "kind": "words",
                "scene_id": a.lines[i]["scene_id"],
                "a": _quote(a, i),
                "b": _quote(b, j),
                "laugh_a": None,
                "laugh_b": None,
            }
        )
    return spots


def _one_side_b(a: Side, b: Side, al: Alignment) -> list[dict]:
    """2 本目でだけ山がある所。1 本目の側の行を逆に引く。"""
    back: dict[int, int] = {}
    for i, j in al.pairs.items():
        back.setdefault(j, i)
    spots: list[dict] = []
    for t in b.humps:
        j = b.line_at(t)
        if j is None or j not in back:
            continue
        i = back[j]
        expected = _expected(b, a, j, i, t)
        if all(abs(h - expected) > MOVED_SEC for h in a.humps):
            spots.append(
                {
                    "kind": "one_side",
                    "scene_id": a.lines[i]["scene_id"],
                    "a": _quote(a, i),
                    "b": _quote(b, j),
                    "laugh_a": None,
                    "laugh_b": t,
                }
            )
    return spots


def scene_rows(a: Side, al: Alignment, titles: dict) -> list[dict]:
    """場面ごとに、どれだけ揃ったかと、1 本目の尺。"""
    rows: dict[str, dict] = {}
    for n, line in enumerate(a.lines):
        if line["scene_id"] is None:
            continue
        row = rows.setdefault(
            line["scene_id"], {"chars": 0, "matched": 0.0, "t0": line["start"], "t1": line["end"]}
        )
        size = len(plain(line["text"]))
        row["chars"] += size
        row["matched"] += size * al.matched.get(n, 0.0)
        row["t1"] = max(row["t1"], line["end"])
    return [
        {
            "scene_id": sid,
            "title": titles.get(sid, ""),
            "seconds": round(row["t1"] - row["t0"], 1),
            "share": round(row["matched"] / row["chars"], 3) if row["chars"] else 0.0,
            "compared": row["chars"] > 0 and row["matched"] / row["chars"] >= MIN_SHARE,
        }
        for sid, row in rows.items()
    ]


def _spot_id(spot: dict) -> str:
    """中身から決める id。やり直しても、問いが別の所を指さないように。"""
    key = f"{spot['kind']}|{spot['a']['t']}|{spot['b']['t']}"
    return f"spot-{int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)}"


def _unique(spots: list[dict]) -> list[dict]:
    """同じ行の組は 1 件にする。片方だけの笑いは、両側から同じ所として見つかる。"""
    seen: set[tuple] = set()
    out = []
    for s in spots:
        key = (s["kind"], s["a"]["t"], s["b"]["t"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def compare(a: Side, b: Side, titles: dict) -> dict:
    """2 本を比べる。揃わなければ `comparable` を false にして、所は出さない。"""
    al = align(a, b)
    laughs = bool(a.humps and b.humps)  # 片方に山が 1 つも無ければ、検出の問題かもしれないので比べない
    head = {
        "schema_version": 1,
        "koza_a": a.koza_id,
        "koza_b": b.koza_id,
        "share": al.share,
        "comparable": al.share >= MIN_SHARE,
        "laughs_compared": laughs,
        "humps_a": len(a.humps),
        "humps_b": len(b.humps),
    }
    if not head["comparable"]:
        return {**head, "scenes": [], "spots": [], "counts": {}}
    scenes = scene_rows(a, al, titles)
    shut = {s["scene_id"] for s in scenes if not s["compared"]}
    found = _word_spots(a, b, al)
    if laughs:
        found = [s for s in (_laugh_spot(a, b, al, t) for t in a.humps) if s is not None] + found
        found += _one_side_b(a, b, al)
    found = [s for s in found if s["scene_id"] is not None and s["scene_id"] not in shut]
    found.sort(key=lambda s: s["a"]["t"])
    spots = [{"id": _spot_id(s), **s} for s in _unique(found)]
    counts = {kind: sum(s["kind"] == kind for s in spots) for kind in ROTATION}
    return {**head, "scenes": scenes, "spots": spots, "counts": counts}


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
