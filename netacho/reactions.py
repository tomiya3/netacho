"""反応の候補。AST のスコアの山から機械的に拾うだけで、受けた・受けないは判定しない。

- 笑い：絶対値では判定できない（最大でも約 0.3）ので、同じ高座の最大値に対する割合で山を見る
- 拍手：Applause、Clapping の最大。Music のスコアに関係なく採用する（中手に出囃子が重なっても拾う）
- 音楽：太鼓でも節回しでも 0.5 前後になるので、はめものの判定には使わない。候補として別に残すだけ

数値は暫定。耳で確かめて調整する。読めなければ netacho mark で手の時刻に置き換える。
"""

from __future__ import annotations

import re
from collections import Counter
from math import ceil
from pathlib import Path

LAUGH_PEAK_RATIO = 0.5  # 高座の笑いの最大値に対する割合
LAUGH_FLOOR = 0.08  # 抜粋に山が無いとき、弱い揺れを山と呼ばないための下限
APPLAUSE_MIN = 0.3
MUSIC_MIN = 0.5
MERGE_STEPS = 1  # これだけの窓の切れ目はつなぐ
LONG_PAUSE = 1.5  # 手がかりとして残す間（秒）
VAD_NO_TEXT_SEC = 1.0  # VAD の区間のうち、文字の無い長さがこれ以上なら手がかりにする


def find_spans(
    values: list[float], threshold: float, *, merge_steps: int = MERGE_STEPS
) -> list[tuple[int, int]]:
    """閾値以上の窓の区間 [i, j)。短い切れ目はつなぐ。"""
    idx = [i for i, v in enumerate(values) if v >= threshold]
    spans: list[list[int]] = []
    for i in idx:
        if spans and i - spans[-1][1] <= merge_steps:
            spans[-1][1] = i + 1
        else:
            spans.append([i, i + 1])
    return [(a, b) for a, b in spans]


def _reaction(n: int, t0: float, t1: float, rank: int | None, method: str, kind: str = "reaction") -> dict:
    return {
        "id": f"react-{n:04d}",
        "t0": round(t0, 2),
        "t1": round(t1, 2),
        "kind": kind,
        "strength_rank": rank,
        "method": method,
    }


def laugh_threshold(laugh: list[float]) -> float:
    return max(max(laugh, default=0.0) * LAUGH_PEAK_RATIO, LAUGH_FLOOR)


def _times(span: tuple[int, int], scores: dict) -> tuple[float, float]:
    """窓の中央の、刻み幅の分を区間にする。窓の端から端までだと、実際より最大 5 秒広く見える。"""
    half = scores["window"] / 2
    start = span[0] * scores["stride"] + half - scores["stride"] / 2
    end = (span[1] - 1) * scores["stride"] + half + scores["stride"] / 2
    return min(start, scores["seconds"]), min(end, scores["seconds"])  # 切れた窓で音声の長さを超えない


def _strength(value: float, kind: str, scores: dict) -> float:
    """種類ごとに高座の最大値で割る。笑いは最大でも約 0.3 なので、そのままだと拍手に必ず負ける。"""
    return value / (max(scores[kind]) or 1.0)


def auto_reactions(scores: dict) -> list[dict]:
    """時刻順の候補。強さは種類ごとの山の高さの順位（1 が最も強い。同じなら早い方）。"""
    rows = []
    for kind, threshold in (("laugh", laugh_threshold(scores["laugh"])), ("applause", APPLAUSE_MIN)):
        for span in find_spans(scores[kind], threshold):
            peak = max(scores[kind][span[0] : span[1]])
            rows.append((*_times(span, scores), kind, _strength(peak, kind, scores)))
    rows.sort(key=lambda r: r[0])
    order = sorted(range(len(rows)), key=lambda k: (-rows[k][3], k))
    rank = {k: r for r, k in enumerate(order, start=1)}
    return [_reaction(k + 1, t0, t1, rank[k], "auto", kind) for k, (t0, t1, kind, _) in enumerate(rows)]


def music_candidates(scores: dict) -> list[dict]:
    """音楽の候補。判定には使わず、別に残すだけ。"""
    out = []
    for span in find_spans(scores["music"], MUSIC_MIN):
        t0, t1 = _times(span, scores)
        out.append({"t0": round(t0, 2), "t1": round(t1, 2), "score": max(scores["music"][span[0] : span[1]])})
    return out


def hints(transcript: dict) -> list[dict]:
    """補助の手がかり：長い間と、VAD は発話なのに文字起こしが空の区間。"""
    out = []
    segs = transcript["segments"]
    for s in segs:
        if s["pause_after"] is not None and s["pause_after"] >= LONG_PAUSE:
            t1 = s["end"] + s["pause_after"]
            out.append({"kind": "long_pause", "t0": s["end"], "t1": round(t1, 3), "seg_id": s["id"]})
    spoken = [(s["start"], s["end"]) for s in segs if s["text"].strip()]
    for v in transcript["vad"]:
        covered = sum(max(0.0, min(b, v["end"]) - max(a, v["start"])) for a, b in spoken)
        if v["end"] - v["start"] - covered >= VAD_NO_TEXT_SEC or not covered:
            out.append({"kind": "vad_no_text", "t0": v["start"], "t1": v["end"], "seg_id": None})
    return sorted(out, key=lambda h: h["t0"])


def covered_seconds(doc: dict) -> float | None:
    """音声の長さ。スコアの無い物は None。"""
    scores = doc.get("scores")
    return scores["seconds"] if scores else None


def window_peak(doc: dict, t0: float, t1: float) -> float:
    """半開区間 [t0, t1) に中央が入る窓の、笑いのスコアの最大。スコアの無い物は 0。

    区間は刻み幅より長い前提（短いと中央が 1 つも入らない）。
    """
    scores = doc.get("scores")
    if not scores or not scores.get("laugh"):
        return 0.0
    half, stride = scores["window"] / 2, scores["stride"]
    lo = max(ceil((t0 - half) / stride), 0)  # 中央が t0 以上の最初の窓。全部を走査しない
    hi = ceil((t1 - half) / stride)
    return max(scores["laugh"][lo:hi], default=0.0)


def peak_share(doc: dict, widths: list[float], floor: float = LAUGH_FLOOR) -> float | None:
    """基準の割合。予測の窓と同じ長さの窓を、音声全体の全部の位置に置いたとき、floor 以上の山がある割合。

    長さごとの割合を、予測の窓の数で重みを付けて平均する。同じ長さは 1 度だけ数える。
    """
    seconds = covered_seconds(doc)
    scores = doc.get("scores")
    if not seconds or not scores or not scores.get("laugh") or not widths:
        return None
    stride = scores["stride"]
    total = weight = 0.0
    for width, n in Counter(round(w, 3) for w in widths).items():
        count = int((seconds - width) // stride) + 1
        if count <= 0:
            continue
        found = sum(window_peak(doc, i * stride, i * stride + width) >= floor for i in range(count))
        total += n * found / count
        weight += n
    return round(total / weight, 3) if weight else None


def build(scores: dict, transcript: dict) -> dict:
    return {
        "schema_version": 1,
        "koza_id": transcript["koza_id"],
        "params": {
            "model": scores["model"],
            "window": scores["window"],
            "stride": scores["stride"],
            "laugh_peak_ratio": LAUGH_PEAK_RATIO,
            "laugh_floor": LAUGH_FLOOR,
            "laugh_threshold": round(laugh_threshold(scores["laugh"]), 4),
            "applause_min": APPLAUSE_MIN,
            "music_min": MUSIC_MIN,
            "merge_steps": MERGE_STEPS,
            "long_pause": LONG_PAUSE,
        },
        "reactions": auto_reactions(scores),
        "music": music_candidates(scores),
        "hints": hints(transcript),
        "scores": {k: scores[k] for k in ("window", "stride", "seconds", "laugh", "applause", "music")},
    }


def top(doc: dict, n: int = 10) -> list[dict]:
    """強い順に n 件（耳で確かめる一覧）。順位の無いものは時刻順で後ろに。"""
    rank = lambda r: (r["strength_rank"] is None, r["strength_rank"] or 0, r["t0"])  # noqa: E731
    return sorted(doc["reactions"], key=rank)[:n]


def clock(t: float) -> str:
    tenths = round(t * 10)  # 先に丸める（59.96 が 0:60.0 にならないように）
    return f"{tenths // 600}:{(tenths % 600) / 10:04.1f}"


def _seconds(text: str) -> float:
    """秒か m:ss(.f) か h:mm:ss。"""
    text = text.strip()
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?|([0-9]+:)?[0-9]+:[0-5]?[0-9](\.[0-9]+)?", text):
        raise ValueError(f"時刻として読めない: {text!r}")
    parts = text.split(":")
    if len(parts) == 3 and int(parts[1]) >= 60:
        raise ValueError(f"時刻として読めない: {text!r}")
    value = 0.0
    for part in parts:
        value = value * 60 + float(part)
    return value


def parse_marks(path: Path) -> list[dict]:
    """耳で付けた時刻の TSV。列は 始まり、終わり、順位（省略可）。# は注釈。"""
    rows = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) not in (2, 3):
            raise ValueError(f"{path.name}:{no}: 列は 2 か 3（タブ区切り）")
        t0, t1 = _seconds(cols[0]), _seconds(cols[1])
        if t0 >= t1:
            raise ValueError(f"{path.name}:{no}: 始まりが終わりより後")
        rows.append((t0, t1, int(cols[2]) if len(cols) == 3 and cols[2].strip() else None))
    if not rows:
        raise ValueError(f"{path.name}: 時刻が 1 件も無い")
    return rows


def manual_reactions(rows: list) -> list[dict]:
    """時刻順に並べて ID を振る。順位は書いてあるときだけ入れる（無ければ null。偽の順位を作らない）。"""
    ranks = [r for _, _, r in rows if r is not None]
    if len(set(ranks)) != len(ranks) or any(r < 1 for r in ranks):
        raise ValueError("順位は 1 以上で重ならないこと")
    ordered = sorted(rows, key=lambda x: x[0])
    return [_reaction(n, t0, t1, rank, "manual") for n, (t0, t1, rank) in enumerate(ordered, start=1)]
