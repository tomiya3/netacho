"""問いを作る役に渡す、これまでのやり取り。"""

import json

from fakes import ScriptedClient
from test_build import K, N, P
from test_guide import _with_question, built  # noqa: F401

from netacho import performer, pipeline
from netacho.budget import Budget


def q(n, *, answer=None, dismissed=None, at="2026-01-01T00:00:00+09:00"):
    row = {"id": f"q-{n:04d}", "issue_id": f"issue-{n:04d}", "scene_id": "scene-01", "text": f"問い{n}"}
    if answer:
        row["answer"] = {"speaker": P, "recorded_at": at, "words": answer}
    if dismissed:
        row["dismissed"] = {"speaker": P, "recorded_at": at, "reason": dismissed}
    return row


def history(questions):
    block = pipeline.history_block({"questions": questions})
    return json.loads(block[0]["text"].split("\n", 1)[1]) if block else None


def test_empty_history_adds_nothing():
    assert pipeline.history_block({"questions": [q(1)]}) == []


def test_answers_and_dismissals_newest_first():
    rows = [
        q(1, answer="古い答え", at="2026-01-01T00:00:00+09:00"),
        q(2),
        q(3, dismissed="的外れ", at="2026-01-03T00:00:00+09:00"),
    ]
    assert history(rows) == [{"問い": "問い3", "捨てた理由": "的外れ"}, {"問い": "問い1", "答え": "古い答え"}]


def test_history_is_capped():
    rows = [q(n, answer=f"答え{n}", at=f"2026-01-01T00:{n:02d}:00+09:00") for n in range(1, 31)]
    got = history(rows)
    assert len(got) == pipeline.HISTORY_MAX and got[0]["答え"] == "答え30"


def test_noter_reads_the_answer(built, data, tmp_path):  # noqa: F811
    qid = _with_question(built, data, tmp_path)
    performer.answer(built, question=qid, speaker=P, words="ここは間を詰めて勢いを出す")
    client = ScriptedClient(noter=[{"schema_version": 1, "notes": []}])
    pipeline.note(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    content = client.calls["noter"][0]["messages"][0]["content"]
    assert content[-1]["text"].startswith("# これまでのやり取り")
    assert "ここは間を詰めて勢いを出す" in content[-1]["text"]


def test_long_words_are_cut_with_a_mark():
    got = history([q(1, answer="あ" * 250)])
    assert got[0]["答え"] == "あ" * pipeline.HISTORY_CHARS + "…（以下略）"


def test_order_is_by_time_not_by_text():
    rows = [
        q(1, answer="先", at="2026-01-02T09:00:00+09:00"),
        q(2, answer="後", at="2026-01-02T01:00:00+00:00"),  # 日本時間の 10 時
    ]
    assert [h["答え"] for h in history(rows)] == ["後", "先"]


def test_leak_needs_a_run_of_words():
    answers = ["土地の名を言うと受ける"]
    assert pipeline.leaks_answer("土地の名を言うと、どうなりますか", answers)
    assert not pipeline.leaks_answer("土地の話ですか？", answers)
    assert pipeline.leaks_answer("土地の 名を言うと？", answers)  # 空白をはさんでも同じ


def test_note_that_writes_the_answer_as_a_fact_falls_back():
    cands = {"candidates": [{"id": "cand-0001", "why": "no_candidate"}]}
    doc = {"notes": [{"candidate_id": "cand-0001", "text": "ここは間を詰めて勢いを出す所です。"}]}
    notes, fixed = pipeline.checked_notes(doc, cands, ["ここは間を詰めて勢いを出す"])
    assert fixed == {"cand-0001"}


def test_noter_input_is_one_block_without_history(built, data, tmp_path):  # noqa: F811
    client = ScriptedClient(noter=[{"schema_version": 1, "notes": []}])
    pipeline.note(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    content = client.calls["noter"][0]["messages"][0]["content"]
    assert all(not b["text"].startswith("# これまでのやり取り") for b in content)
