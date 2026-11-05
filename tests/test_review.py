"""弟子役と仕分けのテスト。"""

import pytest
from fakes import NO_ISSUES, ScriptedClient, text_of
from test_build import K, N, P, _performer_item, books, draft, draft_item, verdicts

from netacho import pipeline, store
from netacho.budget import Budget


def issue(kind="missing_step", scene=None, item=None, attempt="場面を演ろうとした", missing="運びが無い"):
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
    assert "運びが無い" in text
    issues = saved(data)[0]["issues"]
    assert [(i["round"], i["kind"], i["resolved"]) for i in issues] == [
        (1, "missing_step", True),  # 2 周目に無いので解決
        (1, "no_sage", False),  # 残った（2 周目の同じ指摘は重ねない）
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
        assert "運びが無い" not in text  # 差し戻しの履歴を見せない
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


def test_noter_question_does_not_swallow_deshi_ask():
    """ノート役の問いに答えても、同じ場面への弟子役の問いは別のもの。"""
    from netacho import review

    doc = {
        "issues": [
            {
                "id": "issue-0001",
                "role": "noter",
                "kind": "ask_performer",
                "anchor": {"scene_id": "scene-02"},
                "attempt": "1:00 の笑いの所",
                "what_is_missing": "何をしていますか",
                "round": 1,
                "resolved": True,
            }
        ]
    }
    hanashi = {"scenes": [{"id": "scene-02"}], "items": []}
    raw = {"issues": [issue("ask_performer", scene="scene-02")]}
    current, dropped = review.receive(raw, doc, hanashi, {"items": []}, 1)
    assert len(current) == 1 and dropped == 0
    assert "suppressed_answered" not in doc


def test_rephrased_kind_on_same_scene_is_not_resolved(data, tmp_path):
    """2 周目に同じ場面を別の kind で指されたら、直っていない。"""
    c = client(deshi(issue(scene="scene-02")), deshi(issue("unclear_scene", scene="scene-02")))
    run(c, data, tmp_path)
    assert saved(data)[0]["issues"][0]["resolved"] is False


def test_skipped_question_leaves_no_issue_behind(data, tmp_path):
    """聞かなかった 2 周目の ask は残さない。質問の無い指摘は answer で解決できない。"""
    ask1 = issue("ask_performer", scene="scene-02", missing="なぜ")
    ask2 = issue("ask_performer", item="item-0001", missing="なぜ（言い直し）")
    c = client(deshi(ask1, issue(scene="scene-03")), deshi(ask2))
    run(c, data, tmp_path)
    issues, questions = saved(data)
    has_question = {q["issue_id"] for q in questions["questions"]}
    asks = [i for i in issues["issues"] if i["kind"] == "ask_performer"]
    assert [i["id"] in has_question for i in asks] == [True]


def test_rework_without_real_change_is_not_resolved(data, tmp_path):
    """構成作家役が何も直さず、弟子役が 2 周目に言わなかっただけなら解決にしない。"""
    c = client(
        deshi(issue(scene="scene-02")),
        NO_ISSUES,
        writers=[draft(draft_item()), draft(draft_item(id="item-0001"))],
    )
    run(c, data, tmp_path)
    assert saved(data)[0]["issues"][0]["resolved"] is False


def test_change_in_another_scene_does_not_resolve(data, tmp_path):
    other = draft_item(text="別の場面の項目", source={"segs": ["seg-0010", "seg-0010"]})
    c = client(deshi(issue(scene="scene-02")), NO_ISSUES, writers=[draft(draft_item()), draft(other)])
    run(c, data, tmp_path)
    assert saved(data)[0]["issues"][0]["resolved"] is False


def test_dropped_second_round_issue_blocks_resolution(data, tmp_path):
    """2 周目の指摘が壊れて落ちたら、言わなかったのか分からないので解決にしない。"""
    c = client(deshi(issue(scene="scene-02")), deshi(issue(scene="scene-99")))
    run(c, data, tmp_path)
    assert saved(data)[0]["issues"][0]["resolved"] is False


def test_item_and_scene_anchors_are_compared_by_scene(data, tmp_path):
    """1 周目が項目、2 周目が同じ場面を指しても、同じ指摘が残っている。"""
    c = client(deshi(issue(item="item-0001")), deshi(issue(scene="scene-02")))
    run(c, data, tmp_path)
    assert saved(data)[0]["issues"][0]["resolved"] is False


def test_held_target_is_not_resolved(data, tmp_path):
    c = client(
        deshi(issue(item="item-0001")),
        NO_ISSUES,
        writers=[
            draft(draft_item()),
            draft(draft_item(id="item-0001", text="直した"), draft_item(text="足した")),
        ],
        checkers=[
            verdicts(("item-0001", "supported")),
            verdicts(("item-0001", "unsupported"), ("item-0002", "supported")),
        ],
    )
    run(c, data, tmp_path)
    assert saved(data)[0]["issues"][0]["resolved"] is False


def test_rebuild_does_not_duplicate_open_issues(data, tmp_path):
    run(client(deshi(issue(scene="scene-03")), deshi(issue(scene="scene-03"))), data, tmp_path)
    before = saved(data)[0]["issues"]
    assert len(before) == 1
    c = ScriptedClient(
        writer=[draft(draft_item(id="item-0001")), draft(draft_item(id="item-0002", text="また直した"))],
        checker=[verdicts(("item-0001", "supported")), verdicts(("item-0002", "supported"))],
        deshi=[deshi(issue(scene="scene-03")), NO_ISSUES],
    )
    doc = run(c, data, tmp_path)
    assert doc["deshi"]["round1"] == 1
    assert len(saved(data)[0]["issues"]) == 1  # 重ねない
    assert "運びが無い" in text_of(c.calls["writer"][1])  # 前回の未解決も差し戻しに入る


def test_rebuild_can_ask_new_question_on_asked_scene(data, tmp_path):
    run(client(deshi(issue("ask_performer", scene="scene-02", missing="一つ目"))), data, tmp_path)
    c = ScriptedClient(
        writer=[draft(draft_item(id="item-0001"))],
        checker=[verdicts(("item-0001", "supported"))],
        deshi=[
            deshi(
                issue("no_makura", scene="scene-02"),
                issue("ask_performer", item="item-0001", missing="二つ目"),
            )
        ],
    )
    c.replies["writer"].append(draft(draft_item(id="item-0001", text="直した")))
    c.replies["checker"].append(verdicts(("item-0001", "supported")))
    c.replies["deshi"].append(NO_ISSUES)
    run(c, data, tmp_path)
    assert len(saved(data)[1]["questions"]) == 2


def test_issue_on_performer_item_becomes_question(data, tmp_path):
    """演者の項目は構成作家役に直せないので、本人に聞く（札を止めたままにしない）。"""
    run(client(NO_ISSUES), data, tmp_path)
    neta = store.Neta(P, N, data)
    _, kata = books(data)
    item = {**_performer_item(), "id": "item-0009"}
    kata["items"].append(item)
    store.write(neta.kata, kata, "book")
    c = ScriptedClient(
        writer=[draft(draft_item(id="item-0001"))],
        checker=[verdicts(("item-0001", "supported"))],
        deshi=[deshi(issue(item=item["id"]))],
    )
    run(c, data, tmp_path)
    assert len(c.calls["writer"]) == 1  # 差し戻さない
    issues, questions = saved(data)
    assert issues["issues"][-1]["kind"] == "ask_performer"
    assert questions["questions"][-1]["scene_id"] == "scene-01"


def test_first_round_numbers_survive_a_crash_in_second_round(data, tmp_path):
    c = client(deshi(issue(scene="scene-02")))
    with pytest.raises(IndexError):  # 2 周目の返事が無い = 途中で落ちた
        run(c, data, tmp_path)
    doc = store.read(store.Neta(P, N, data).koza_dir(K) / "run.json", "run")
    assert doc["deshi"] == {"round1": 1, "round2": None, "dropped_no_anchor": 0}


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


def test_rebuild_does_not_ask_again_on_a_dismissed_scene(data, tmp_path):
    run(client(deshi(issue(kind="ask_performer", scene="scene-02", missing="なぜ"))), data, tmp_path)
    neta = store.Neta(P, N, data)
    doc, questions = saved(data)
    questions["questions"][0]["dismissed"] = {"speaker": P, "recorded_at": "t", "reason": "的外れ"}
    doc["issues"][0]["resolved"] = True
    store.write(neta.questions, questions, "questions")
    store.write(neta.review, doc, "review")

    c = ScriptedClient(
        writer=[draft(draft_item(id="item-0001"))],
        checker=[verdicts(("item-0001", "supported"))],
        deshi=[deshi(issue(kind="ask_performer", scene="scene-02", missing="なぜ（言い直し）"))],
    )
    run(c, data, tmp_path)
    assert len(saved(data)[1]["questions"]) == 1
