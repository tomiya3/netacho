"""笑いどころの予測と実際の比較のテスト。"""

import json
from pathlib import Path

from fakes import ScriptedClient, text_of
from test_build import K, N, P, draft, draft_item, verdicts

from netacho import cli, gaps, performer, pipeline, reactions, render, schema, store, view
from netacho.budget import Budget


def item(i, t1, koza="k"):
    return {
        "id": f"item-{i:04d}",
        "kind": "くすぐり",
        "text": "笑いどころ",
        "source": {"koza_id": koza},
        "payoff": {"t0": t1 - 1.0, "t1": t1},
    }


def react(i, t0, t1, rank, kind="laugh", method="auto"):
    return {"id": f"react-{i:04d}", "t0": t0, "t1": t1, "strength_rank": rank, "kind": kind, "method": method}


def doc(*rows, seconds=100.0):
    return {"koza_id": "k", "reactions": list(rows), "scores": {"seconds": seconds}}


TRANSCRIPT = {
    "segments": [
        {"id": "seg-0001", "start": 0.0, "end": 40.0, "text": "前の台詞"},
        {"id": "seg-0002", "start": 41.0, "end": 90.0, "text": "後の台詞"},
    ]
}
TIMELINE = {
    "segments": [
        {"seg_id": "seg-0001", "scene_id": "scene-01"},
        {"seg_id": "seg-0002", "scene_id": "scene-02"},
    ]
}


def compare(items, doc_):
    return gaps.compare(items, doc_, TRANSCRIPT, TIMELINE)


def test_three_groups():
    r = compare(
        [item(1, 10.0), item(2, 30.0)],
        doc(react(1, 11.0, 12.0, 2), react(2, 50.0, 52.0, 1)),
    )
    assert [h["item_id"] for h in r["hits"]] == ["item-0001"]
    assert [m["item_id"] for m in r["misses"]] == ["item-0002"]
    assert [s["reaction_id"] for s in r["surprises"]] == ["react-0002"]
    assert r["surprises"][0]["scene_id"] == "scene-02"
    c = gaps.counts(r)
    assert (c["hit"], c["miss"], c["surprise"], c["hit_rate"]) == (1, 1, 1, 0.5)
    assert (c["laughs_total"], c["laughs_hit"]) == (2, 1)
    assert c["window_share"] == 0.14  # 窓 [9, 16] と [29, 36] で 14 秒 / 100 秒


def test_applause_is_not_a_hit_or_a_surprise():
    r = compare(
        [item(1, 10.0)],
        doc(react(1, 11.0, 12.0, 1, kind="applause"), react(2, 50.0, 52.0, 2, kind="applause")),
    )
    assert r["hits"] == [] and r["surprises"] == []
    assert r["misses"] == []  # 自動の笑いが無いので比べない


def test_no_auto_reaction_is_not_judged():
    """未検出を空振りと呼ばない。"""
    r = compare([item(1, 10.0)], doc())
    assert r["judged"] is False and r["misses"] == []
    manual = compare([item(1, 10.0)], doc(react(1, 50.0, 52.0, None, kind="reaction", method="manual")))
    assert manual["judged"] is False and manual["misses"] == [] and manual["surprises"] == []


def test_clipped_window_is_not_a_miss():
    r = compare([item(1, 98.0)], doc(react(1, 50.0, 52.0, 1), seconds=100.0))
    assert r["misses"] == []


def test_surprises_are_capped_but_counted_in_full():
    rows = [react(i, 20.0 * i, 20.0 * i + 1, 9 - i) for i in range(1, 9)]
    r = compare([], doc(*rows, seconds=200.0))
    assert len(r["surprises"]) == gaps.SURPRISE_MAX
    assert r["surprise_total"] == 8 and gaps.counts(r)["surprise"] == 8
    assert r["surprises"][0]["reaction_id"] == "react-0008"


def test_close_laughs_are_one_question():
    """続く笑いが割れても、問いを同じ所の言い換えで埋めない。"""
    rows = [react(1, 50.0, 52.0, 1), react(2, 54.0, 55.0, 2), react(3, 80.0, 81.0, 3)]
    r = compare([], doc(*rows))
    assert [s["reaction_id"] for s in r["surprises"]] == ["react-0001", "react-0003"]
    assert r["surprise_total"] == 3


def test_manual_laugh_is_not_a_hit_when_not_judged():
    manual = compare([item(1, 10.0)], doc(react(1, 11.0, 12.0, None, kind="laugh", method="manual")))
    assert manual["judged"] is False and manual["hits"] == []
    assert gaps.counts(manual)["hit_rate"] is None


def test_window_share_merges_overlaps():
    r = compare([item(1, 10.0), item(2, 12.0)], doc(react(1, 80.0, 81.0, 1)))
    assert r["window_share"] == 0.09  # [9, 16] と [11, 18] の和集合は 9 秒


def test_same_clock_on_another_koza_is_asked():
    review = {"issues": []}
    questions = {"questions": []}
    s = {
        "t0": 60.0,
        "scene_id": "scene-01",
        "before_quote": "台詞",
        "question": "何をしていますか。",
    }
    assert gaps.add_questions({"koza_id": "a", "surprises": [s]}, review, questions) == 1
    assert gaps.add_questions({"koza_id": "a", "surprises": [s]}, review, questions) == 0
    assert gaps.add_questions({"koza_id": "b", "surprises": [s]}, review, questions) == 1
    assert "台詞" in questions["questions"][0]["text"]


def test_applause_only_koza_is_not_judged():
    r = compare([item(1, 10.0)], doc(react(1, 50.0, 52.0, 1, kind="applause")))
    assert r["judged"] is False and r["misses"] == []


def test_before_quote_stops_at_the_laugh():
    t = {
        "segments": [
            {"id": "seg-0001", "start": 0.0, "end": 9.0, "text": "前の台詞"},
            {"id": "seg-0002", "start": 10.0, "end": 12.0, "text": "笑いの後の台詞"},
        ]
    }
    scene = "scene-01"
    tl = {"segments": [{"seg_id": "seg-0001", "scene_id": scene}, {"seg_id": "seg-0002", "scene_id": scene}]}
    r = gaps.compare([], doc(react(1, 9.5, 11.0, 1)), t, tl)
    assert r["surprises"][0]["before_quote"] == "前の台詞"


def test_laugh_without_scene_does_not_use_a_slot():
    tl = {"segments": [{"seg_id": "seg-0002", "scene_id": "scene-02"}]}
    rows = [react(1, 20.0, 21.0, 1), react(2, 50.0, 51.0, 2)]
    r = gaps.compare([], doc(*rows), TRANSCRIPT, tl)
    assert [s["reaction_id"] for s in r["surprises"]] == ["react-0002"]
    assert r["surprise_total"] == 2


def scored(seconds, peaks):
    """{秒: スコア} の笑いのスコアを持つ doc。"""
    n = int((seconds - 5) / 2) + 1
    laugh = [0.0] * n
    for t, v in peaks.items():
        laugh[int((t - 2.5) / 2)] = v
    d = doc(react(1, 80.0, 82.0, 1), seconds=seconds)
    d["scores"].update(window=5.0, stride=2.0, laugh=laugh)
    return d


def test_weak_peak_is_not_a_miss():
    r = compare([item(1, 10.0), item(2, 40.0)], scored(100.0, {12.5: 0.1}))
    assert [w["item_id"] for w in r["weak"]] == ["item-0001"]
    assert [m["item_id"] for m in r["misses"]] == ["item-0002"]
    c = gaps.counts(r)
    assert (c["hit"], c["weak"], c["miss"], c["peak_rate"]) == (0, 1, 1, 0.5)


def test_baseline_counts_every_position_with_the_same_rule():
    """長さ 10 秒の窓を 2 秒刻みで全部の位置に置く。山は 12.5 秒に 1 つ。"""
    d = scored(50.0, {12.5: 0.1})
    # 開始 4〜12 秒の 5 位置が山を含む。位置は 0〜40 秒の 21 個
    assert reactions.peak_share(d, [10.0]) == round(5 / 21, 3)
    assert reactions.peak_share(d, [10.0, 10.0]) == round(5 / 21, 3)
    assert reactions.peak_share(d, []) is None
    assert reactions.peak_share({"scores": {"seconds": 10.0}}, [5.0]) is None


def test_peak_rate_uses_scores_only():
    """予測も基準もスコアだけで数える。的中の判定を混ぜない。"""
    d = doc(react(1, 11.0, 12.0, 1), seconds=100.0)
    d["scores"].update(window=5.0, stride=2.0, laugh=[0.0] * 48)
    r = compare([item(1, 10.0)], d)
    assert len(r["hits"]) == 1 and r["peak_rate"] == 0.0


def test_clipped_window_is_not_weak_either():
    r = compare([item(1, 98.0)], scored(100.0, {96.5: 0.1}))
    assert r["weak"] == [] and r["misses"] == [] and r["peak_n"] == 0


def test_long_audio_is_fast():
    """2 時間分のスコアでも、基準はすぐ出る。"""
    import time

    n = 3600
    d = {"scores": {"window": 5.0, "stride": 2.0, "seconds": 7200.0, "laugh": [0.0] * n}}
    t0 = time.time()
    assert reactions.peak_share(d, [12.0, 12.0, 15.0, 20.0] * 30) == 0.0
    assert time.time() - t0 < 2.0


def test_window_before_the_first_center_is_empty():
    d = scored(50.0, {12.5: 0.1})
    assert reactions.window_peak(d, 0.0, 1.0) == 0.0


def test_window_edges_are_half_open():
    d = scored(50.0, {12.5: 0.1})
    assert reactions.window_peak(d, 12.5, 14.0) == 0.1
    assert reactions.window_peak(d, 11.0, 12.5) == 0.0


def test_peak_rate_uses_the_same_rule_and_leaves_clipped_windows_out():
    items = [item(1, 10.0), item(2, 40.0), item(3, 98.0)]
    d = scored(100.0, {12.5: 0.1, 96.5: 0.1})
    r = compare(items, d)
    assert r["peak_n"] == 2 and r["peak_rate"] == 0.5  # 末尾で切れた item-0003 は分母に入らない


ADDED_LATER = ("weak", "peak_rate", "baseline_rate", "peak_n", "humps_total", "humps_hit", "humps_expected")


def to_previous_version(kdir):
    """run.json と gaps.json を、足した欄の無い 1 つ前の版の形にする。"""
    for name, kind, key in (("run.json", "run", "gaps"), ("gaps.json", "gaps", None)):
        doc_ = store.read(kdir / name, kind)
        target = doc_[key] if key else doc_
        for k in ADDED_LATER:
            target.pop(k)
        store.write(kdir / name, doc_, kind)


def test_previous_version_can_run_note_again(data, tmp_path):
    built(data, tmp_path)
    run_note(data, tmp_path, note_reply())
    kdir = store.Neta(P, N, data).koza_dir(K)
    to_previous_version(kdir)
    _, run = run_note(data, tmp_path, note_reply())
    assert set(ADDED_LATER) <= set(run["gaps"])
    assert set(ADDED_LATER) <= set(store.read(kdir / "gaps.json", "gaps"))


def test_previous_version_can_be_viewed(data, tmp_path):
    built(data, tmp_path)
    run_note(data, tmp_path, note_reply())
    neta = store.Neta(P, N, data)
    to_previous_version(neta.koza_dir(K))
    assert "weak" not in view.payload(neta, K)["gaps"]


def test_what_was_not_measured_is_not_written_as_zero():
    found = compare([item(1, 10.0)], doc(react(1, 11.0, 12.0, 1)))
    assert "弱い山 0" in "\n".join(render.gaps_markdown(found, {}))
    for k in ADDED_LATER:
        found.pop(k)
    text = "\n".join(render.gaps_markdown(found, {}))
    assert "弱い山 " not in text and "山がある割合" not in text
    assert "前の版の数え方" in text  # 今の版の空振りと同じ数字に読めないように


def test_fixed_shape_of_the_previous_version_stays_valid():
    """e8b99e2 の形をそのまま持つ。次に欄を足すときも、これが通ること。"""
    old_gaps = {
        "schema_version": 1,
        "koza_id": "k",
        "judged": True,
        "hits": [],
        "misses": [{"item_id": "item-0001"}],
        "surprises": [],
        "surprise_total": 0,
        "laughs_total": 0,
        "laughs_hit": 0,
        "window_share": 0.1,
    }
    old_counts = dict.fromkeys(("hit", "miss", "surprise", "laughs_total", "laughs_hit"), 0)
    schema.validate(old_gaps, "gaps")
    old_run = {"schema_version": 1, "koza_id": "k", "roles": [], "total_cost_usd": 0.0, "total_seconds": 0.0}
    old_run["gaps"] = {**old_counts, "hit_rate": None, "window_share": 0.1}
    schema.validate(old_run, "run")


def test_new_output_always_has_the_optional_fields():
    """任意にしたのは前の版を読むため。新しい出力からは欠けない。"""
    found = compare([item(1, 10.0)], doc(react(1, 11.0, 12.0, 1)))
    root = Path(__file__).parents[1] / "schemas"
    run_schema = json.loads((root / "run.json").read_text())
    assert set(gaps.counts(found)) == set(run_schema["properties"]["gaps"]["properties"])
    gaps_schema = json.loads((root / "gaps.json").read_text())
    assert set(found) == set(gaps_schema["properties"])
    assert set(ADDED_LATER) <= set(found) and set(ADDED_LATER) <= set(compare([], doc()))


def test_fields_of_the_previous_version_stay_required():
    run = json.loads((Path(__file__).parents[1] / "schemas" / "run.json").read_text())
    assert {"laughs_total", "laughs_hit", "window_share"} <= set(run["properties"]["gaps"]["required"])


def test_humps_are_runs_of_scores_above_the_floor():
    d = scored(100.0, {12.5: 0.1, 14.5: 0.2, 40.5: 0.08, 60.5: 0.07})
    assert reactions.laugh_humps(d) == [[12.5, 14.5], [40.5]]


def test_humps_are_none_without_scores():
    assert reactions.laugh_humps(doc()) is None


def test_two_items_on_one_hump_count_once():
    d = scored(100.0, {12.5: 0.1, 14.5: 0.2, 60.5: 0.1})
    r = compare([item(1, 10.0), item(2, 12.0)], d)
    assert len(r["weak"]) == 2  # 項目ごとでは 2 件
    assert (r["humps_hit"], r["humps_total"]) == (1, 2)  # 山は 1 つ


def test_hump_edge_outside_every_window_is_not_hit():
    r = compare([item(1, 10.0)], scored(100.0, {16.5: 0.1}))  # 窓は [9, 16)
    assert r["humps_hit"] == 0 and r["humps_total"] == 1


def test_no_automatic_laugh_writes_no_hump_count():
    r = compare([item(1, 10.0)], doc())
    assert r["judged"] is False and r["humps_total"] is None and r["humps_hit"] is None
    assert r["humps_expected"] is None


def test_hump_hit_agrees_with_window_peak():
    d = scored(100.0, {12.5: 0.1, 14.5: 0.1, 40.5: 0.1})
    humps = reactions.laugh_humps(d)
    for t0, t1 in ([12.6, 14.4], [12.5, 13.0], [14.5, 20.0], [15.0, 40.0], [40.5, 41.0], [0.0, 12.5]):
        touched = gaps._humps_hit(humps, [[t0, t1]]) > 0
        assert touched == (reactions.window_peak(d, t0, t1) >= reactions.LAUGH_FLOOR), (t0, t1)


def test_expected_share_includes_the_width_of_a_hump():
    thin = gaps._humps_expected([[50.0]], [[0.0, 10.0]], 110.0)
    wide = gaps._humps_expected([[50.0, 60.0, 70.0]], [[0.0, 10.0]], 110.0)
    assert thin == 0.1 and wide == 0.3  # 10 / 100 と 30 / 100


def test_expected_share_does_not_grow_with_overlapping_items():
    one = gaps._humps_expected([[50.0]], [[0.0, 10.0]], 110.0)
    assert gaps._humps_expected([[50.0]], [[0.0, 10.0], [0.0, 10.0]], 110.0) == one
    assert gaps._humps_expected([[50.0]], [[0.0, 5.0], [5.0, 10.0]], 110.0) == one


def test_no_window_has_no_expected_share():
    assert gaps._humps_expected([[50.0]], [], 110.0) is None


def test_expected_share_of_many_windows_does_not_exceed_one():
    assert gaps._humps_expected([[50.0]], [[0.0, 200.0]], 110.0) == 1.0
    assert gaps._humps_expected([], [[0.0, 10.0]], 110.0) is None


def test_clipped_window_is_left_out_of_humps_with_its_hump():
    items = [item(1, 10.0), item(2, 97.0)]  # 2 件目の窓は録音の末尾で切れる
    r = compare(items, scored(100.0, {12.5: 0.1, 40.5: 0.1, 96.5: 0.1}))
    assert (r["humps_hit"], r["humps_total"]) == (1, 2)


def test_only_clipped_windows_write_no_hump_count():
    r = compare([item(1, 97.0)], scored(100.0, {12.5: 0.1}))
    assert r["judged"] is True and r["humps_total"] is None and r["humps_hit"] is None


def test_a_gap_of_one_window_keeps_one_hump():
    d = scored(100.0, {12.5: 0.1, 16.5: 0.1, 22.5: 0.1})
    assert reactions.laugh_humps(d) == [[12.5, 16.5], [22.5]]  # 18.5 と 20.5 の 2 窓の切れ目では分ける


def test_markdown_keeps_the_share_of_windows():
    r = compare([item(1, 10.0)], scored(100.0, {12.5: 0.1}))
    assert "を覆っている" in "\n".join(render.gaps_markdown(r, {}))


def test_hump_line_does_not_print_none():
    assert "None" not in cli._hump_line({"humps_total": 0, "humps_hit": 0, "humps_expected": None})
    assert "見込み 0.25" in cli._hump_line({"humps_total": 4, "humps_hit": 2, "humps_expected": 0.25})


def test_zero_humps_is_written_as_measured():
    found = compare([item(1, 10.0)], scored(100.0, {}))
    assert (found["humps_total"], found["humps_hit"], found["humps_expected"]) == (0, 0, None)
    assert "笑いの山（弱い山を含む）は 0 個でした" in "\n".join(
        render.gaps_markdown(found, {"item-0001": item(1, 10.0)})
    )


def test_markdown_leads_with_laughs_and_humps():
    r = compare([item(1, 10.0), item(2, 12.0)], scored(100.0, {12.5: 0.1, 60.5: 0.1}))
    text = "\n".join(render.gaps_markdown(r, {}))
    assert "笑いの山（弱い山を含む）2 個のうち、予測の窓に入ったのは 1 個（50%）" in text
    assert "同じ長さの窓を音声全体に置くと、見込みは" in text
    assert text.index("笑いの山") < text.index("項目ごとの内訳")
    assert "1 つの笑いに項目が重なる" in text


def test_no_prediction_rate_is_none():
    assert gaps.counts(compare([], doc()))["hit_rate"] is None


def test_item_outside_koza_or_held_is_not_counted():
    held = {**item(1, 10.0), "held": True}
    other = item(2, 20.0, koza="other")
    assert compare([held, other], doc(react(1, 50.0, 52.0, 1)))["misses"] == []


def kusuguri(**kw):
    base = dict(
        kind="くすぐり",
        book="hanashi",
        text="利息の話が骨折り賃で返るので笑いどころと読む",
        source={"segs": ["seg-0005", "seg-0005"]},
        payoff={"segs": ["seg-0005", "seg-0005"]},
    )
    return draft_item(**{**base, **kw})


def note_reply(*pairs):
    return {"schema_version": 1, "notes": [{"candidate_id": c, "text": t} for c, t in pairs]}


def built(data, tmp_path):
    client = ScriptedClient(writer=[draft(kusuguri())], checker=[verdicts(("item-0001", "supported"))])
    pipeline.build(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)


def run_note(data, tmp_path, *replies):
    client = ScriptedClient(noter=list(replies))
    return client, pipeline.note(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)


def test_writer_can_predict_and_never_sees_reactions(data, tmp_path):
    built(data, tmp_path)
    neta = store.Neta(P, N, data)
    assert store.read(neta.hanashi, "book")["items"][0]["kind"] == "くすぐり"


def test_note_makes_a_question_from_a_surprise(data, tmp_path):
    built(data, tmp_path)
    q = "「かしこまりました」の後は、文字からは笑いどころと読めませんでした。ここでは何をしていますか。"
    client, run = run_note(data, tmp_path, note_reply(("gap-0001", q)))
    neta = store.Neta(P, N, data)
    kdir = neta.koza_dir(K)
    found = store.read(kdir / "gaps.json", "gaps")
    assert found["surprises"][0]["question"] == q and found["surprises"][0]["question_source"] == "claude"
    assert run["gaps"]["surprise"] == 2  # ダミーの笑いは 2 か所とも予測の外。近いので問いは 1 つ
    questions = store.read(neta.questions, "questions")["questions"]
    assert q in questions[0]["text"]
    assert "文字からは読めなかった所" in (kdir / "note.md").read_text(encoding="utf-8")
    rows = json.loads(text_of(client.calls["noter"][0]))
    surprise = next(r for r in rows if r["kind"] == "予測なしで反応あり")
    assert set(surprise) == {"candidate_id", "kind", "before_quote"}  # 反応の全体も順位も見せない


def test_broken_question_falls_back(data, tmp_path):
    built(data, tmp_path)
    run_note(data, tmp_path, note_reply(("gap-0001", "お客さんは受けなかった。")))
    found = store.read(store.Neta(P, N, data).koza_dir(K) / "gaps.json", "gaps")
    assert found["surprises"][0]["question"] == gaps.FALLBACK_QUESTION
    assert found["surprises"][0]["question_source"] == "fixed"


def test_note_twice_does_not_ask_twice(data, tmp_path):
    built(data, tmp_path)
    run_note(data, tmp_path, note_reply(("gap-0001", "何をしていますか。")))
    run_note(data, tmp_path, note_reply(("gap-0001", "何をしていますか。")))
    assert len(store.read(store.Neta(P, N, data).questions, "questions")["questions"]) == 1


def test_answer_to_a_surprise_enters_the_kata(data, tmp_path):
    """問いへの答えが型に残る（ズレ → 問い → 答え → 型）。"""
    built(data, tmp_path)
    run_note(data, tmp_path, note_reply(("gap-0001", "何をしていますか。")))
    neta = store.Neta(P, N, data)
    q = store.read(neta.questions, "questions")["questions"][-1]
    item_ = performer.answer(neta, question=q["id"], speaker="演者", words="ここは目線だけで間を取る")
    assert item_["source"]["words"] == "ここは目線だけで間を取る"
    assert item_["edited_by"] == "performer"
    issues = {i["id"]: i for i in store.read(neta.review, "review")["issues"]}
    assert issues[q["issue_id"]]["resolved"] is True
