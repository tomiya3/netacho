"""答えの前後の読み直し。役には帳面と問いだけを見せ、引用はスクリプトが確かめる。"""

import pytest
from fakes import ScriptedClient, text_of
from test_build import K, N, P
from test_guide import _with_question as with_question
from test_guide import built, mitate  # noqa: F401

from netacho import cli, performer, pipeline, store, view
from netacho.budget import Budget
from netacho.schema import validate

WORDS = "ここで間を詰めると、雨の勢いが出るからや"


def side(qid, verdict="not_written", quote="", reply="理由が書いていない"):
    return {"question_id": qid, "verdict": verdict, "quote": quote, "reply": reply}


def replies(*rows):
    return {"schema_version": 1, "answers": list(rows)}


@pytest.fixture
def answered(built, data, tmp_path):  # noqa: F811
    q = with_question(built, data, tmp_path)
    performer.answer(built, question=q, speaker=P, words=WORDS)
    return q


def run(client, data, tmp_path):
    return pipeline.recheck_answers(
        P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data
    )


def test_no_answers_calls_nothing(built, data, tmp_path):  # noqa: F811
    client = ScriptedClient()
    assert run(client, data, tmp_path) is None
    assert client.calls["rechecker"] == []
    assert not built.recheck.exists()


def test_before_hides_the_answer_and_after_shows_it(built, answered, data, tmp_path):  # noqa: F811
    client = ScriptedClient(
        rechecker=[replies(side(answered)), replies(side(answered, "written", WORDS, "勢いのためです"))]
    )
    result = run(client, data, tmp_path)
    before, after = (text_of(c) for c in client.calls["rechecker"])
    assert WORDS not in before
    assert WORDS in after
    for shown in (before, after):
        assert answered in shown
        assert "transcript" not in shown
    assert result == {"questions": 1, "before_written": 0, "after_written": 1, "filled": 1, "dropped": 0}
    doc = store.read(built.recheck, "recheck")
    assert doc["rows"][0]["cites_answer"] is True
    assert store.read(built.koza_dir(K) / "run.json", "run")["recheck"] == result
    validate(store.read(built.drafts / "recheck-before.json", "rechecker"), "rechecker")
    validate(store.read(built.drafts / "recheck-after.json", "rechecker"), "rechecker")


def test_books_are_not_rewritten(built, answered, data, tmp_path):  # noqa: F811
    keep = built.kata.read_text(encoding="utf-8")
    run(ScriptedClient(rechecker=[replies(), replies()]), data, tmp_path)
    assert built.kata.read_text(encoding="utf-8") == keep


def test_quote_not_in_book_is_dropped_and_left_null(built, answered, data, tmp_path):  # noqa: F811
    client = ScriptedClient(
        rechecker=[replies(side(answered)), replies(side(answered, "written", "帳面に無い作り話", "作り話"))]
    )
    result = run(client, data, tmp_path)
    row = store.read(built.recheck, "recheck")["rows"][0]
    assert row["after"] is None
    assert row["cites_answer"] is False
    assert result["dropped"] == 1
    assert result["filled"] == 0


def test_answer_quoted_before_it_was_written_is_dropped(answered, data, tmp_path):
    client = ScriptedClient(rechecker=[replies(side(answered, "written", WORDS, "x")), replies()])
    assert run(client, data, tmp_path)["dropped"] == 1


@pytest.mark.parametrize("quote", ["雨を行列に見立てる", "雨"])
def test_after_that_cites_something_else_is_not_filled(built, answered, data, tmp_path, quote):  # noqa: F811
    client = ScriptedClient(
        rechecker=[replies(side(answered)), replies(side(answered, "written", quote, "別の所から"))]
    )
    result = run(client, data, tmp_path)
    assert result["after_written"] == 1
    assert result["filled"] == 0


def test_written_before_is_kept_as_it_is(built, answered, data, tmp_path):  # noqa: F811
    client = ScriptedClient(
        rechecker=[replies(side(answered, "written", "雨", "前から読める")), replies(side(answered))]
    )
    result = run(client, data, tmp_path)
    assert result == {"questions": 1, "before_written": 1, "after_written": 0, "filled": 0, "dropped": 0}
    assert store.read(built.recheck, "recheck")["rows"][0]["before"]["verdict"] == "written"


def test_unknown_and_repeated_questions_are_dropped(answered, data, tmp_path):
    client = ScriptedClient(
        rechecker=[replies(side("q-9999"), side(answered), side(answered)), replies(side(answered))]
    )
    assert run(client, data, tmp_path)["dropped"] == 2


def test_view_carries_the_result(built, answered, data, tmp_path):  # noqa: F811
    run(ScriptedClient(rechecker=[replies(side(answered)), replies(side(answered))]), data, tmp_path)
    assert view.payload(built, K)["recheck"]["rows"][0]["question_id"] == answered


def test_cli_says_when_nothing_is_answered(built, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr("netacho.runner.make_client", ScriptedClient)
    cli.main(["recheck", "--performer", P, "--neta", N, "--koza", K])
    assert "答えた問いはまだありません" in capsys.readouterr().out
