"""笑いどころの予測と実際の笑いを比べる。LLM は使わない。

構成作家役は反応を見ずに回収を書く。ここでその予測と AST の笑いを重ねて、3 つに分ける。

- 的中：予測した回収の窓に笑いの山がある
- 弱い山：強い山には届かないが、下限（LAUGH_FLOOR）を超える山がある
- 空振り：予測した回収の窓に、山が見つからない
- 予測なしで反応あり：どの回収の窓にも入らない笑い。文字に書けない芸の候補

未検出を反応なしと扱わない。自動の反応が無い高座は判定しない。録音の末尾で切れた窓は空振りにしない。
拍手は登場や中手に出るので、笑いどころの比較には使わない。
"""

from __future__ import annotations

from . import candidates
from .reactions import LAUGH_FLOOR, clock, covered_seconds, laugh_humps, peak_share, window_peak

SURPRISE_MAX = 5  # 演者に問う数の上限。強い順
BUNDLE_SEC = 15.0  # これより近い笑いは 1 つの山として、問いは強い 1 件だけにする
BEFORE_SEC = 12.0  # 笑いの前の台詞を見せる長さ
QUOTE_IN_QUESTION = 40  # 問いの文に添える直前の台詞の長さ
FALLBACK_QUESTION = "文字からは笑いどころと読めなかった所です。ここで何をしているのか、教えてください。"


def _before_quote(transcript: dict, t0: float) -> str:
    """笑いが始まる前の台詞。笑いの後の台詞を混ぜない（問いの引用がずれる）。"""
    rows = [
        s["text"].strip() for s in transcript["segments"] if s["start"] < t0 and s["end"] > t0 - BEFORE_SEC
    ]
    return "".join(rows)[-160:]


def _bundled(rows: list[dict]) -> list[dict]:
    """強い順に見て、既に選んだ笑いの近くの物を飛ばす（1 つの山の言い換えで問いを埋めない）。"""
    picked: list[dict] = []
    for r in rows:
        if all(abs(r["t0"] - p["t0"]) >= BUNDLE_SEC for p in picked):
            picked.append(r)
    return picked


def _window_share(windows: list[list[float]], seconds: float | None) -> float:
    """窓の和集合が音声を覆う割合。窓が高座の大半を覆うと的中率は意味を失うので、一緒に見る。"""
    if not seconds:
        return 0.0
    covered, end = 0.0, 0.0
    for t0, t1 in sorted(windows):
        covered += max(0.0, t1 - max(t0, end))
        end = max(end, t1)
    return round(covered / seconds, 3)


def _touches(hump: list[float], windows: list[list[float]]) -> bool:
    return any(w0 <= c < w1 for w0, w1 in windows for c in hump)


def _humps_hit(humps: list[list[float]], windows: list[list[float]]) -> int:
    """どれかの窓に入った山の数。同じ山に窓が 2 つ重なっても 1 つと数える。

    窓の判定は window_peak と同じ半開区間。スコアの窓の中央が入れば、その山に触れている。
    """
    return sum(_touches(hump, windows) for hump in humps)


def _merged(windows: list[list[float]]) -> list[list[float]]:
    """重なる窓を 1 つにまとめる。項目が重なっても、見込みが膨らまないように。"""
    out: list[list[float]] = []
    for w0, w1 in sorted(windows):
        if out and w0 <= out[-1][1]:
            out[-1][1] = max(out[-1][1], w1)
        else:
            out.append([w0, w1])
    return out


def _touch_chance(hump: list[float], width: float, seconds: float) -> float:
    """長さ width の窓を音声のどこかに一様に置いたとき、山に触れる確率。山の幅の分だけ高くなる。

    窓が刻み幅より長い前提（山のひと続きの間に、窓の中央が入らない所が無い）。
    """
    room = seconds - width
    if room <= 0:
        return 1.0
    return max(0.0, min(room, hump[-1]) - max(0.0, hump[0] - width)) / room


def _humps_expected(humps: list[list[float]], windows: list[list[float]], seconds: float) -> float | None:
    """同じ長さの窓を、音声全体に一様に置いたときに入る山の割合の見込み。山の幅も入れる。

    窓の割合と比べると、幅のある山に触れやすい分だけ予測に有利になるので、これと比べる。
    窓どうしは独立に置くとして計算する。乱数は使わない。
    """
    if not humps or not windows:
        return None
    missed = []
    for hump in humps:
        chance = 1.0
        for w0, w1 in _merged(windows):
            chance *= 1.0 - _touch_chance(hump, w1 - w0, seconds)
        missed.append(chance)
    return round(1.0 - sum(missed) / len(missed), 3)


def compare(items: list[dict], reactions: dict, transcript: dict, timeline: dict | None) -> dict:
    """3 区分を作る。surprises は演者に問う分だけで、全数は surprise_total に持つ。"""
    koza_id = reactions["koza_id"]
    end = covered_seconds(reactions)
    laughs = [r for r in reactions["reactions"] if r["kind"] == "laugh" and r["method"] == "auto"]
    judged = bool(laughs)  # 自動の笑いが 1 件も無ければ比べない（全部の予測を空振りにしない）
    hits, weak, misses, windows = [], [], [], []
    clipped_windows: list[list[float]] = []
    peaks: list[float] = []
    whole: list[float] = []
    used: set[str] = set()
    for it in candidates.payoff_items(items, koza_id) if judged else []:
        window, clipped = candidates.window_of(it, end)
        windows.append(window)
        if clipped:
            clipped_windows.append(window)
        found = candidates.overlapping(laughs, window[0], window[1])
        peak = window_peak(reactions, window[0], window[1])
        if not clipped:  # 予測も基準もスコアだけで数える（的中の判定を混ぜると予測に有利になる）
            peaks.append(peak)
            whole.append(window[1] - window[0])
        if found:
            hits.append({"item_id": it["id"], "reaction_ids": [r["id"] for r in found]})
            used |= {r["id"] for r in found}
        elif clipped:
            continue  # 録音の末尾で切れた窓は判定しない
        elif peak >= LAUGH_FLOOR:
            weak.append({"item_id": it["id"], "peak": peak})
        else:
            misses.append({"item_id": it["id"]})
    scene_of = {s["seg_id"]: s["scene_id"] for s in (timeline or {"segments": []})["segments"]}

    def scene_at(t: float) -> str | None:
        started = [s for s in transcript["segments"] if s["start"] <= t]
        seg = max(started, key=lambda s: s["start"], default=None)
        return scene_of.get(seg["id"]) if seg else None

    humps = laugh_humps(reactions) if judged else None  # 判定しない高座を 0 と書かない
    whole_windows = [w for w in windows if w not in clipped_windows]
    if humps is not None:  # 末尾で切れた窓は判定しない。その窓に触れる山も、母数から外す
        humps = [h for h in humps if not _touches(h, clipped_windows)]
    rest = [r for r in laughs if r["id"] not in used]
    rest.sort(key=lambda r: (r["strength_rank"] is None, r["strength_rank"] or 0))
    askable = [r for r in rest if scene_at(r["t0"]) is not None]  # 場面が決まらない笑いは問えない
    surprises = [
        {
            "id": f"gap-{n:04d}",
            "reaction_id": r["id"],
            "t0": r["t0"],
            "t1": r["t1"],
            "scene_id": scene_at(r["t0"]),
            "before_quote": _before_quote(transcript, r["t0"]),
        }
        for n, r in enumerate(_bundled(askable)[:SURPRISE_MAX], start=1)
    ]
    return {
        "schema_version": 1,
        "koza_id": koza_id,
        "judged": judged,
        "hits": hits,
        "weak": weak,
        "misses": misses,
        "surprises": surprises,
        "surprise_total": len(rest),
        "laughs_total": len(laughs),
        "laughs_hit": len(used),
        "window_share": _window_share(windows, end),
        "humps_total": None if humps is None else len(humps),
        "humps_hit": None if humps is None else _humps_hit(humps, whole_windows),
        "humps_expected": None if not humps or not end else _humps_expected(humps, whole_windows, end),
        # 予測と基準は同じ定義で数える。録音の末尾で切れた窓は両方から外す
        "peak_rate": _rate(sum(p >= LAUGH_FLOOR for p in peaks), sum(p < LAUGH_FLOOR for p in peaks)),
        "peak_n": len(peaks),
        "baseline_rate": peak_share(reactions, whole),
    }


def _rate(found: int, rest: int) -> float | None:
    return round(found / (found + rest), 3) if found + rest else None


def counts(doc: dict) -> dict:
    hit, weak, miss = len(doc["hits"]), len(doc["weak"]), len(doc["misses"])
    return {
        "hit": hit,
        "weak": weak,
        "miss": miss,
        "surprise": doc["surprise_total"],
        "hit_rate": _rate(hit, weak + miss),
        "peak_rate": doc["peak_rate"],
        "peak_n": doc["peak_n"],
        "baseline_rate": doc["baseline_rate"],
        "laughs_total": doc["laughs_total"],
        "laughs_hit": doc["laughs_hit"],
        "window_share": doc["window_share"],
        "humps_total": doc["humps_total"],
        "humps_hit": doc["humps_hit"],
        "humps_expected": doc["humps_expected"],
    }


def add_questions(doc: dict, review: dict, questions: dict) -> int:
    """予測なしで反応ありの笑いを、演者への質問にする。同じ笑いを 2 度聞かない。足した数を返す。"""
    n_issue = max((int(i["id"].split("-")[1]) for i in review["issues"]), default=0) + 1
    n_q = max((int(q["id"].split("-")[1]) for q in questions["questions"]), default=0) + 1
    asked = {i["attempt"] for i in review["issues"]}
    added = 0
    for s in doc["surprises"]:
        attempt = f"{clock(s['t0'])} の笑いの所（{doc['koza_id']}）"  # 別の高座の同じ時刻と区別する
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
                "text": f"{attempt}「…{s['before_quote'][-QUOTE_IN_QUESTION:]}」：{s['question']}",
            }
        )
        n_issue += 1
        n_q += 1
        added += 1
    return added
