"""「薄い」候補の選び方。決めるのはスクリプトで、LLM は使わない。

未検出を「反応なし」と扱わない。ここで選ぶのは、演者に見てもらう候補であって、判定ではない。
順位は同じ高座の回収の中でだけ比べる。耳で確かめる前の順位は当てにならない（台詞の山が混ざる）。
"""

from __future__ import annotations

WINDOW_SEC = 6.0  # 回収の終わりから見る長さ。暫定
LOW_FRACTION = 4  # 下位 4 分の 1


def audio_end(reactions: dict) -> float | None:
    wave = reactions.get("wave")
    return wave["hop"] * len(wave["db"]) if wave else None


def payoff_items(items: list[dict], koza_id: str) -> list[dict]:
    """この高座の回収。保留は除く。回収の順に。"""
    rows = [i for i in items if "payoff" in i and not i.get("held") and i["source"].get("koza_id") == koza_id]
    return sorted(rows, key=lambda i: i["payoff"]["t1"])


def _overlapping(reactions: list[dict], t0: float, t1: float) -> list[dict]:
    return [r for r in reactions if r["t0"] < t1 and r["t1"] > t0]


def select(items: list[dict], reactions: dict) -> dict:
    """note_candidates の中身を作る。

    - 窓に反応の候補が無ければ no_candidate
    - 窓に候補があれば、その中で最も強い順位をその回収の強さとし、下位 4 分の 1 を low_rank
    - 音声の末尾で窓が切れていて、候補も無ければ判定しない（録音が足りないだけかもしれない）
    - 順位の無い手の反応は、窓にあっても順位の計算に入れない
    """
    koza_id = reactions["koza_id"]
    end = audio_end(reactions)
    silent: list[tuple[dict, list[float]]] = []
    ranked: list[tuple[int, dict, list[float], list[str]]] = []
    for it in payoff_items(items, koza_id):
        t0 = it["payoff"]["t1"]
        t1 = t0 + WINDOW_SEC
        clipped = end is not None and t1 > end
        window = [t0, round(min(t1, end), 3) if clipped else round(t1, 3)]
        hits = _overlapping(reactions["reactions"], window[0], window[1])
        if not hits:
            if not clipped:
                silent.append((it, window))
            continue
        ranks = [r["strength_rank"] for r in hits if r["strength_rank"] is not None]
        if ranks:
            ranked.append((min(ranks), it, window, [r["id"] for r in hits]))
    low_count = len(ranked) // LOW_FRACTION
    weakest = sorted(ranked, key=lambda x: x[0], reverse=True)[:low_count]
    rows = [(it, w, [], "no_candidate") for it, w in silent]
    rows += [(it, w, ids, "low_rank") for _, it, w, ids in weakest]
    rows.sort(key=lambda r: r[0]["payoff"]["t1"])
    return {
        "schema_version": 1,
        "koza_id": koza_id,
        "candidates": [
            {
                "id": f"cand-{n:04d}",
                "item_id": it["id"],
                "payoff_t": it["payoff"]["t1"],
                "window": window,
                "reaction_ids": ids,
                "why": why,
            }
            for n, (it, window, ids, why) in enumerate(rows, start=1)
        ],
    }
