"""「薄い」候補の選び方のテスト。"""

import pytest

from netacho import candidates


def item(i, t1, *, held=False, koza="k"):
    it = {
        "id": f"item-{i:04d}",
        "source": {"koza_id": koza},
        "payoff": {"t0": t1 - 1.0, "t1": t1},
    }
    if held:
        it["held"] = True
    return it


def react(i, t0, t1, rank, method="auto"):
    return {"id": f"react-{i:04d}", "t0": t0, "t1": t1, "strength_rank": rank, "method": method}


def doc(*rows, seconds=None):
    d = {"koza_id": "k", "reactions": list(rows)}
    if seconds is not None:
        d["wave"] = {"hop": 1.0, "db": [0.0] * seconds, "baseline_db": [0.0] * seconds}
    return d


def whys(result):
    return [(c["item_id"], c["why"]) for c in result["candidates"]]


def test_no_reaction_in_window_is_no_candidate():
    r = candidates.select([item(1, 10.0)], doc(react(1, 17.0, 18.0, 1)))
    assert whys(r) == [("item-0001", "no_candidate")]
    c = r["candidates"][0]
    assert c["window"] == [10.0, 16.0] and c["payoff_t"] == 10.0 and c["reaction_ids"] == []


def test_reaction_overlapping_window_counts():
    """笑いが回収の途中から始まっていても、窓にかかれば候補がある。"""
    r = candidates.select([item(1, 10.0)], doc(react(1, 9.0, 10.5, 1)))
    assert whys(r) == []


def test_lower_quarter_is_low_rank():
    items = [item(i, 10.0 * i) for i in range(1, 9)]
    rows = [react(i, 10.0 * i + 1, 10.0 * i + 2, rank=i) for i in range(1, 9)]
    r = candidates.select(items, doc(*rows))
    assert whys(r) == [("item-0007", "low_rank"), ("item-0008", "low_rank")]
    assert r["candidates"][0]["reaction_ids"] == ["react-0007"]


def test_fewer_than_four_payoffs_have_no_low_rank():
    """回収が少ないと 4 分の 1 は言えない。強い物を無理に薄いと呼ばない。"""
    items = [item(1, 10.0), item(2, 20.0), item(3, 30.0)]
    rows = [react(1, 11.0, 12.0, 1), react(2, 21.0, 22.0, 2), react(3, 31.0, 32.0, 3)]
    assert candidates.select(items, doc(*rows))["candidates"] == []


def test_uses_strongest_reaction_in_window():
    items = [item(i, 10.0 * i) for i in range(1, 5)]
    rows = [
        react(1, 11.0, 12.0, 1),
        react(2, 21.0, 22.0, 2),
        react(3, 31.0, 32.0, 3),
        react(4, 41.0, 42.0, 9),
        react(5, 43.0, 44.0, 4),  # 同じ窓の強い方が回収の強さ
    ]
    r = candidates.select(items, doc(*rows))
    assert whys(r) == [("item-0004", "low_rank")]
    assert r["candidates"][0]["reaction_ids"] == ["react-0004", "react-0005"]


def test_silent_payoffs_are_not_in_rank_population():
    """窓に候補の無い回収は、順位の母数に入れない。"""
    items = [item(i, 10.0 * i) for i in range(1, 5)] + [item(9, 90.0)]
    rows = [react(i, 10.0 * i + 1, 10.0 * i + 2, rank=i) for i in range(1, 5)]
    assert whys(candidates.select(items, doc(*rows))) == [
        ("item-0004", "low_rank"),
        ("item-0009", "no_candidate"),
    ]


def test_clipped_window_at_end_of_audio_is_not_judged():
    """録音の末尾で窓が切れるなら、候補が無くても判定しない。"""
    far = react(9, 1.0, 2.0, 1)
    r = candidates.select([item(1, 58.0)], doc(far, seconds=60))
    assert r["candidates"] == []
    r = candidates.select([item(1, 50.0)], doc(far, seconds=60))
    assert whys(r) == [("item-0001", "no_candidate")]


def test_nothing_detected_is_not_judged():
    """自動で 1 件も拾えなかった高座では、全部の回収を「候補が少ない」にしない。"""
    r = candidates.select([item(1, 10.0), item(2, 20.0)], doc())
    assert r["candidates"] == []
    assert any("判定していません" in n for n in r["notices"])


def test_manual_only_koza_does_not_judge_unmarked_payoffs():
    """手の印は聞いた所だけなので、印の無い回収を no_candidate にしない。手で付けたことも出す。"""
    r = candidates.select([item(1, 10.0), item(2, 20.0)], doc(react(1, 11.0, 12.0, None, "manual")))
    assert r["candidates"] == []
    assert "この高座の反応は手で付けたものです。" in r["notices"]


def test_few_payoffs_notice():
    r = candidates.select([item(1, 10.0)], doc(react(1, 11.0, 12.0, 1)))
    assert any("強さは比べていません" in n for n in r["notices"])


def test_clipped_window_still_finds_reaction():
    r = candidates.select([item(1, 58.0)], doc(react(1, 59.0, 59.5, 1), seconds=60))
    assert r["candidates"] == []


def test_manual_reaction_without_rank_is_presence_only():
    """順位の無い手の反応：候補がある扱いだが、順位の比較には入らない。"""
    items = [item(i, 10.0 * i) for i in range(1, 5)]
    rows = [react(i, 10.0 * i + 1, 10.0 * i + 2, rank=None) for i in range(1, 5)]
    assert candidates.select(items, doc(*rows))["candidates"] == []


def test_held_and_other_koza_are_skipped():
    items = [item(1, 10.0, held=True), item(2, 20.0, koza="other"), item(3, 30.0)]
    assert whys(candidates.select(items, doc(react(9, 1.0, 2.0, 1)))) == [("item-0003", "no_candidate")]


def test_ids_follow_time_order():
    items = [item(2, 50.0), item(1, 10.0)]
    r = candidates.select(items, doc(react(9, 1.0, 2.0, 1)))
    assert [(c["id"], c["item_id"]) for c in r["candidates"]] == [
        ("cand-0001", "item-0001"),
        ("cand-0002", "item-0002"),
    ]


@pytest.mark.parametrize("n", [0])
def test_no_payoffs(n):
    assert candidates.select([], doc())["candidates"] == []
