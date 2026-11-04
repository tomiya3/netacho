"""反応の候補。波から機械的に拾うだけで、受けた・受けないは判定しない。

笑いは台詞に重なるので、発話区間を除外しない。強さは同じ高座の中の順位にする
（スマホの自動音量調整があるので絶対値は使わない）。
数値は暫定。上位と下位を耳で確かめて調整する。
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
HOP = 0.1  # RMS を取る間隔（秒）
BASE_WINDOW = 10.0  # 基準の移動中央値の片側（秒）
THRESHOLD_DB = 6.0  # 基準をこれだけ超えたら候補
MIN_DURATION = 0.4  # つないだ後の長さの下限（秒）
MERGE_GAP = 0.5  # これより短い隙間はつなぐ（秒）
FLOOR_DB = -60.0  # これより小さい音は候補にしない（無音の部分の基準が下がるため）
LONG_PAUSE = 1.5  # 手がかりとして残す間（秒）
WAVE_HOP = 0.5  # 画面の波の間隔（秒）


def rms_db(audio: np.ndarray, *, sr: int = SAMPLE_RATE, hop: float = HOP) -> np.ndarray:
    n = round(sr * hop)
    frames = len(audio) // n
    if frames == 0:
        return np.zeros(0)
    power = (audio[: frames * n].astype(np.float64) ** 2).reshape(frames, n).mean(axis=1)
    return 10 * np.log10(np.maximum(power, 1e-10))


def baseline(db: np.ndarray, *, hop: float = HOP, window: float = BASE_WINDOW) -> np.ndarray:
    """前後 window 秒の移動中央値。両端は折り返して埋める（端が無音に引かれないように）。"""
    if len(db) == 0:
        return db
    w = min(round(window / hop), len(db) - 1)
    if w == 0:
        return db.copy()
    padded = np.pad(db, w, mode="reflect")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, 2 * w + 1), axis=1)


def find_spans(
    db: np.ndarray,
    base: np.ndarray,
    *,
    hop: float = HOP,
    threshold: float = THRESHOLD_DB,
    min_duration: float = MIN_DURATION,
    merge_gap: float = MERGE_GAP,
) -> list[tuple[int, int]]:
    """基準を超えたフレームの区間 [i, j)。隙間をつないでから長さで落とす（笑いは波が切れ切れになる）。"""
    idx = np.flatnonzero((db - base >= threshold) & (db >= FLOOR_DB))
    if len(idx) == 0:
        return []
    spans: list[list[int]] = [[int(idx[0]), int(idx[0]) + 1]]
    for i in idx[1:]:
        if (i - spans[-1][1]) * hop < merge_gap:
            spans[-1][1] = int(i) + 1
        else:
            spans.append([int(i), int(i) + 1])
    return [(a, b) for a, b in spans if (b - a) * hop >= min_duration - 1e-9]


def _reaction(n: int, t0: float, t1: float, rank: int | None, method: str) -> dict:
    return {
        "id": f"react-{n:04d}",
        "t0": round(t0, 2),
        "t1": round(t1, 2),
        "kind": "reaction",
        "strength_rank": rank,
        "method": method,
    }


def auto_reactions(db: np.ndarray, base: np.ndarray, *, hop: float = HOP) -> list[dict]:
    """時刻順の候補。強さは基準を超えた量の積分の順位（1 が最も強い。同じなら早い方）。"""
    spans = find_spans(db, base, hop=hop)
    excess = np.maximum(db - base, 0.0)
    strength = [float(excess[a:b].sum()) for a, b in spans]
    order = sorted(range(len(spans)), key=lambda k: (-strength[k], k))
    rank = {k: r for r, k in enumerate(order, start=1)}
    return [_reaction(k + 1, a * hop, b * hop, rank[k], "auto") for k, (a, b) in enumerate(spans)]


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
        if not any(a < v["end"] and v["start"] < b for a, b in spoken):
            out.append({"kind": "vad_no_text", "t0": v["start"], "t1": v["end"], "seg_id": None})
    return sorted(out, key=lambda h: h["t0"])


def wave(db: np.ndarray, base: np.ndarray, *, hop: float = HOP, wave_hop: float = WAVE_HOP) -> dict:
    """画面の波。間引くときは大きい方を残す（短い山が消えないように）。"""
    k = max(round(wave_hop / hop), 1)
    n = len(db) // k
    if n == 0:
        return {"hop": k * hop, "db": [], "baseline_db": []}
    return {
        "hop": round(k * hop, 3),
        "db": np.round(db[: n * k].reshape(n, k).max(axis=1), 1).tolist(),
        "baseline_db": np.round(np.median(base[: n * k].reshape(n, k), axis=1), 1).tolist(),
    }


def build(audio: np.ndarray, transcript: dict) -> dict:
    db = rms_db(audio)
    base = baseline(db)
    return {
        "schema_version": 1,
        "koza_id": transcript["koza_id"],
        "params": {
            "hop": HOP,
            "base_window": BASE_WINDOW,
            "threshold_db": THRESHOLD_DB,
            "min_duration": MIN_DURATION,
            "merge_gap": MERGE_GAP,
            "floor_db": FLOOR_DB,
            "long_pause": LONG_PAUSE,
        },
        "reactions": auto_reactions(db, base),
        "hints": hints(transcript),
        "wave": wave(db, base),
    }


def top(doc: dict, n: int = 10) -> list[dict]:
    """強い順に n 件（耳で確かめる一覧）。順位の無いものは時刻順で後ろに。"""
    rank = lambda r: (r["strength_rank"] is None, r["strength_rank"] or 0, r["t0"])  # noqa: E731
    return sorted(doc["reactions"], key=rank)[:n]


def clock(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def _seconds(text: str) -> float:
    """秒か m:ss(.f) か h:mm:ss。"""
    text = text.strip()
    if not re.fullmatch(r"\d+(\.\d+)?|(\d+:)?\d+:\d+(\.\d+)?", text):
        raise ValueError(f"時刻として読めない: {text!r}")
    value = 0.0
    for part in text.split(":"):
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
