"""笑いどころの予測と実際の比較のテスト。"""

import json

from fakes import ScriptedClient, text_of
from test_build import K, N, P, draft, draft_item, verdicts

from netacho import gaps, performer, pipeline, store
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


def compare(items, reactions):
    return gaps.compare(items, reactions, TRANSCRIPT, TIMELINE)


def test_three_groups():
    r = compare(
        [item(1, 10.0), item(2, 30.0)],
        doc(react(1, 11.0, 12.0, 2), react(2, 50.0, 52.0, 1)),
    )
    assert [h["item_id"] for h in r["hits"]] == ["item-0001"]
    assert [m["item_id"] for m in r["misses"]] == ["item-0002"]
    assert [s["reaction_id"] for s in r["surprises"]] == ["react-0002"]
    assert r["surprises"][0]["scene_id"] == "scene-02"
    assert gaps.counts(r) == {"hit": 1, "miss": 1, "surprise": 1, "hit_rate": 0.5}


def test_applause_is_not_a_hit_or_a_surprise():
    r = compare(
        [item(1, 10.0)],
        doc(react(1, 11.0, 12.0, 1, kind="applause"), react(2, 50.0, 52.0, 2, kind="applause")),
    )
    assert r["hits"] == [] and r["surprises"] == []
    assert [m["item_id"] for m in r["misses"]] == ["item-0001"]


def test_no_auto_reaction_is_not_judged():
    """未検出を空振りと呼ばない。"""
    r = compare([item(1, 10.0)], doc())
    assert r["judged"] is False and r["misses"] == []
    manual = compare([item(1, 10.0)], doc(react(1, 50.0, 52.0, None, kind="reaction", method="manual")))
    assert manual["judged"] is False and manual["misses"] == [] and manual["surprises"] == []


def test_clipped_window_is_not_a_miss():
    r = compare([item(1, 98.0)], doc(react(1, 50.0, 52.0, 1), seconds=100.0))
    assert r["misses"] == []


def test_surprises_are_strongest_first_and_capped():
    rows = [react(i, 10.0 * i, 10.0 * i + 1, 9 - i) for i in range(1, 9)]
    r = compare([], doc(*rows))
    assert len(r["surprises"]) == gaps.SURPRISE_MAX
    assert r["surprises"][0]["reaction_id"] == "react-0008"


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
    assert run["gaps"]["surprise"] == 2  # ダミーの笑いは 2 か所とも予測の外
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
    assert len(store.read(store.Neta(P, N, data).questions, "questions")["questions"]) == 2


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
