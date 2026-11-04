"""弟子役と仕分けのテスト。"""

import pytest
from fakes import NO_ISSUES, ScriptedClient, text_of
from test_build import K, N, P, books, draft, draft_item, verdicts

from netacho import pipeline, review, store
from netacho.budget import Budget


def issue(
    kind="missing_step", scene=None, item=None, attempt="場面を演ろうとした", missing="運びが書いていない"
):
    return {"kind": kind, "scene_id": scene, "item_id": item, "attempt": attempt, "what_is_missing": missing}


def deshi(*issues):
    return {"schema_version": 1, "issues": list(issues)}


def client(*deshi_replies, writers=None, checkers=None):
    return ScriptedClient(
        writer=writers or [draft(draft_item()), draft(draft_item(text="足した項目"))],
        checker=checkers or [verdicts(("item-0001", "supported")), verdicts(("item-0002", "supported"))],
        deshi=list(deshi_replies),
    )


def run(c, data, tmp_path):
    return pipeline.build(P, N, K, client=c, budget=Budget(tmp_path / "ledger.json"), root=data)


def saved(data):
    neta = store.Neta(P, N, data)
    return store.read(neta.review, "review"), store.read(neta.questions, "questions")


def test_no_issues_means_no_regeneration(data, tmp_path):
    c = client(NO_ISSUES)
    doc = run(c, data, tmp_path)
    assert len(c.calls["writer"]) == 1 and len(c.calls["deshi"]) == 1
    assert doc["deshi"] == {"round1": 0, "round2": None, "dropped_no_anchor": 0}
    assert doc["questions"] == 0


def test_rework_goes_to_writer_once_and_second_round_measures(data, tmp_path):
    c = client(
        deshi(issue(scene="scene-02"), issue(kind="no_sage", scene="scene-03")),
        deshi(issue(kind="no_sage", scene="scene-03")),
    )
    doc = run(c, data, tmp_path)
    assert len(c.calls["writer"]) == 2 and len(c.calls["deshi"]) == 2
    assert doc["deshi"] == {"round1": 2, "round2": 1, "dropped_no_anchor": 0}
    text = text_of(c.calls["writer"][1])
    assert "運びが書いていない" in text
    issues = saved(data)[0]["issues"]
    assert [(i["round"], i["kind"], i["resolved"]) for i in issues] == [
        (1, "missing_step", True),  # 2 周目に無いので解決
        (1, "no_sage", False),  # 残った
        (2, "no_sage", False),
    ]


def test_ask_performer_never_reaches_writer(data, tmp_path):
    ask = issue(
        kind="ask_performer", scene="scene-02", attempt="行列を演ろうとした", missing="なぜ間を詰めるか不明"
    )
    c = client(deshi(ask))
    doc = run(c, data, tmp_path)
    assert len(c.calls["writer"]) == 1  # 差し戻しが無いので再生成しない
    assert doc["questions"] == 1
    _, questions = saved(data)
    q = questions["questions"][0]
    assert q["scene_id"] == "scene-02" and q["text"] == "行列を演ろうとした。なぜ間を詰めるか不明"
    assert "answer" not in q


def test_ask_is_kept_out_of_rework_when_mixed(data, tmp_path):
    ask = issue(kind="ask_performer", scene="scene-02", missing="本人に聞きたいこと")
    c = client(deshi(ask, issue(scene="scene-03")), NO_ISSUES)
    run(c, data, tmp_path)
    assert "本人に聞きたいこと" not in text_of(c.calls["writer"][1])
    ask_issue = saved(data)[0]["issues"][0]
    assert ask_issue["kind"] == "ask_performer" and ask_issue["resolved"] is False  # 答えが来るまで未解決


def test_regeneration_is_at_most_once(data, tmp_path):
    c = client(deshi(issue(scene="scene-02")), deshi(issue(scene="scene-02")))
    run(c, data, tmp_path)
    assert len(c.calls["writer"]) == 2


@pytest.mark.parametrize(
    "bad",
    [
        issue(scene="scene-99"),  # 場面が無い
        issue(item="item-9999"),
        issue(),  # どちらも無い
        issue(scene="scene-02", item="item-0001"),  # 両方ある
        issue(scene="scene-02", attempt=" "),
    ],
)
def test_bad_anchor_is_dropped_and_counted(data, tmp_path, bad):
    c = client(deshi(bad))
    doc = run(c, data, tmp_path)
    assert doc["deshi"] == {"round1": 0, "round2": None, "dropped_no_anchor": 1}


def test_held_item_cannot_be_pointed_at(data, tmp_path):
    c = client(deshi(issue(item="item-0001")), checkers=[verdicts(("item-0001", "unsupported"))])
    doc = run(c, data, tmp_path)
    assert doc["deshi"]["dropped_no_anchor"] == 1


def test_item_anchor_finds_scene_for_question(data, tmp_path):
    c = client(deshi(issue(kind="ask_performer", item="item-0001")))
    run(c, data, tmp_path)
    assert saved(data)[1]["questions"][0]["scene_id"] == "scene-02"


def test_deshi_sees_only_markdown(data, tmp_path):
    c = client(deshi(issue(scene="scene-02")), NO_ISSUES)
    run(c, data, tmp_path)
    for call in c.calls["deshi"]:
        text = text_of(call)
        assert "おう太助か" not in text  # 録音の引用は無い
        assert "react-" not in text and "seg-" not in text
        assert "運びが書いていない" not in text  # 差し戻しの履歴を見せない
        assert "# 噺" in text and "# 型" in text


def test_second_round_does_not_repeat_question_on_same_scene(data, tmp_path):
    ask1 = issue(kind="ask_performer", scene="scene-02", missing="なぜ")
    ask2 = issue(kind="ask_performer", scene="scene-02", missing="なぜ（言い直し）")
    c = client(deshi(ask1, issue(scene="scene-03")), deshi(ask2))
    doc = run(c, data, tmp_path)
    assert doc["questions"] == 1


def test_rebuild_keeps_answered_questions(data, tmp_path):
    ask = issue(kind="ask_performer", scene="scene-02")
    run(client(deshi(ask)), data, tmp_path)
    neta = store.Neta(P, N, data)
    doc, questions = saved(data)
    questions["questions"][0]["answer"] = {"speaker": P, "recorded_at": "t", "words": "演者の答え"}
    doc["issues"][0]["resolved"] = True
    store.write(neta.questions, questions, "questions")
    store.write(neta.review, doc, "review")

    c = ScriptedClient(
        writer=[draft(draft_item(id="item-0001"))], checker=[verdicts(("item-0001", "supported"))]
    )
    run(c, data, tmp_path)
    doc, questions = saved(data)
    assert questions["questions"][0]["answer"]["words"] == "演者の答え"
    assert doc["issues"][0]["resolved"] is True


def test_settle_rework_compares_kind_and_anchor():
    first = [
        {"kind": "missing_step", "anchor": {"scene_id": "scene-01"}, "resolved": False},
        {"kind": "missing_step", "anchor": {"scene_id": "scene-02"}, "resolved": False},
    ]
    second = [
        {"kind": "no_sage", "anchor": {"scene_id": "scene-01"}},
        {"kind": "missing_step", "anchor": {"scene_id": "scene-02"}},
    ]
    review.settle_rework(first, second)
    assert [i["resolved"] for i in first] == [True, False]


def test_deshi_role_has_the_promises():
    from netacho import runner

    text = runner.role_prompt("deshi")
    assert "明日この噺を高座でやる弟子" in text
    assert "「もっと詳しく」のような一般論は書きません" in text
    assert "見せないもの" not in text


def test_books_are_saved_before_deshi_reads(data, tmp_path):
    run(client(NO_ISSUES), data, tmp_path)
    hanashi, _ = books(data)
    assert hanashi["items"]
