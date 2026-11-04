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
    - 自動の反応が 1 件も無い高座では no_candidate を出さない（手の印は聞いた所だけで、
      拾えなかった高座は回収が全部「候補が少ない」になってしまう）
    """
    koza_id = reactions["koza_id"]
    end = audio_end(reactions)
    has_auto = any(r["method"] == "auto" for r in reactions["reactions"])
    has_manual = any(r["method"] == "manual" for r in reactions["reactions"])
    silent: list[tuple[dict, list[float]]] = []
    ranked: list[tuple[int, dict, list[float], list[str]]] = []
    for it in payoff_items(items, koza_id):
        t0 = it["payoff"]["t0"]  # 笑いは台詞に重なり、Whisper の区間は長いので、回収の区間の中から見る
        t1 = it["payoff"]["t1"] + WINDOW_SEC
        clipped = end is not None and t1 > end
        window = [t0, round(min(t1, end), 3) if clipped else round(t1, 3)]
        hits = _overlapping(reactions["reactions"], window[0], window[1])
        if not hits:
            if not clipped and has_auto:
                silent.append((it, window))
            continue
        ranks = [r["strength_rank"] for r in hits if r["strength_rank"] is not None]
        if ranks and not clipped:  # 窓が短いと弱く出るので、切れた窓は比べない
            ranked.append((min(ranks), it, window, [r["id"] for r in hits]))
    low_count = len(ranked) // LOW_FRACTION
    weakest = sorted(ranked, key=lambda x: x[0], reverse=True)
    if low_count:
        line = weakest[low_count - 1][0]
        tied = low_count < len(weakest) and weakest[low_count][0] == line
        # 境目の同順位（近い回収が同じ反応を指す）は、時刻で片方だけ選ばず両方外す
        weakest = [x for x in weakest[:low_count] if not (tied and x[0] == line)]
    else:
        weakest = []
    rows = [(it, w, [], "no_candidate") for it, w in silent]
    rows += [(it, w, ids, "low_rank") for _, it, w, ids in weakest]
    rows.sort(key=lambda r: r[0]["payoff"]["t1"])
    notices = []
    if has_manual:
        notices.append("この高座の反応は手で付けたものです。")
    if not has_auto:
        notices.append("自動で拾った反応の候補が無いので、回収の後に候補が無いかどうかは判定していません。")
    if ranked and low_count == 0:
        notices.append("順位のある回収が 4 件未満なので、強さは比べていません。")
    return {
        "schema_version": 1,
        "koza_id": koza_id,
        "notices": notices,
        "candidates": [
            {
                "id": "cand-"
                + it["id"].split("-")[1],  # 回収から決める（note をやり直しても指す物が変わらない）
                "item_id": it["id"],
                "payoff_t": it["payoff"]["t1"],
                "window": window,
                "reaction_ids": ids,
                "why": why,
            }
            for it, window, ids, why in rows
        ],
    }
