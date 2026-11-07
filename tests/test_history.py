"""問いを作る役に渡す、これまでのやり取り。"""

import json

from fakes import ScriptedClient
from test_build import K, N, P
from test_guide import _with_question, built  # noqa: F401

from netacho import gaps, performer, pipeline
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


def test_quoted_lines_do_not_count_as_a_leak():
    answers = ["傘を貸して勘定した"]
    line = "傘を貸して勘定した"
    assert not pipeline.leaks_answer(
        "「傘を貸して勘定した」の所で、間を延ばしたんですか？", answers, line
    )


def test_quoting_the_answer_itself_is_a_leak():
    answers = ["土地の名を言うと受ける"]
    assert pipeline.leaks_answer("「土地の名を言うと受ける」んですね？", answers, "軒先借りたんで")


def test_punctuation_does_not_hide_a_leak():
    assert pipeline.leaks_answer("土地の名を、言うと？", ["土地の名を言うと受ける"])


def test_only_shown_answers_are_checked():
    rows = [q(n, answer=f"古い答え{n:02d}です", at=f"2026-01-01T00:{n:02d}:00+09:00") for n in range(1, 31)]
    words = pipeline.answer_words({"questions": rows})
    assert len(words) == pipeline.HISTORY_MAX and "古い答え01です" not in words


def test_odd_times_do_not_stop_the_order():
    rows = [
        q(1, answer="時差なし", at="2026-01-02T10:00:00"),
        q(2, answer="読めない", at="きのう"),
        q(3, answer="時差あり", at="2026-01-02T09:00:00+09:00"),
    ]
    assert [h["答え"] for h in history(rows)] == ["時差なし", "時差あり", "読めない"]


def test_surprise_question_that_writes_the_answer_falls_back():
    found = {"surprises": [{"id": "sur-0001"}]}
    doc = {"notes": [{"candidate_id": "sur-0001", "text": "ここは間を詰めて勢いを出す所ですか。"}]}
    pipeline.checked_questions(doc, found, ["ここは間を詰めて勢いを出す"])
    assert found["surprises"][0]["question"] == gaps.FALLBACK_QUESTION
    assert found["surprises"][0]["question_source"] == "fixed"


def test_note_that_quotes_the_shown_line_is_kept():
    cands = {"candidates": [{"id": "cand-0001", "why": "no_candidate"}]}
    doc = {
        "notes": [
            {
                "candidate_id": "cand-0001",
                "text": "「行列なら頭を下げて待つ」の後に、反応候補が少ない所です。",
            }
        ]
    }
    stat: dict = {}
    notes, fixed = pipeline.checked_notes(
        doc,
        cands,
        ["行列なら頭を下げて待つしかない"],
        stat,
        {"cand-0001": "行列なら頭を下げて待つしかねえな。"},
    )
    assert fixed == set() and stat == {}


def test_only_the_cut_answer_is_checked():
    words = pipeline.answer_words({"questions": [q(1, answer="あ" * 200 + "いうえおかきくけこ")]})
    assert not pipeline.leaks_answer("いうえおかきくけこ", words)


def test_words_that_are_also_in_the_shown_lines_are_not_a_leak():
    answers = ["裏長屋の軒先借りたんで受けた"]
    line = "雨のたびに裏長屋の軒先借りたんで"
    assert not pipeline.leaks_answer("裏長屋の軒先借りたんで、の所はわざとですか？", answers, line)
    assert pipeline.leaks_answer("軒先借りたんで受けたんですね？", answers, line)  # 台詞に無い連続は混入


def test_full_and_half_width_are_the_same():
    assert pipeline.leaks_answer("ＩＣレコーダーで撮って", ["ICレコーダーで撮ってる"])


def test_note_and_question_leaks_are_counted_apart(built, data, tmp_path):  # noqa: F811
    qid = _with_question(built, data, tmp_path)
    words = "ここは間を詰めて勢いを出す"
    performer.answer(built, question=qid, speaker=P, words=words)

    def reply(kw):
        rows = json.loads(kw["messages"][0]["content"][0]["text"])
        return json.dumps(
            {
                "schema_version": 1,
                "notes": [{"candidate_id": r["candidate_id"], "text": f"{words}所です。"} for r in rows],
            },
            ensure_ascii=False,
        )

    client = ScriptedClient(noter=[reply])
    run = pipeline.note(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    rows = json.loads(client.calls["noter"][0]["messages"][0]["content"][0]["text"])
    notes = sum(r["kind"] != "予測なしで反応あり" for r in rows)
    asks = len(rows) - notes
    assert notes >= 1 and asks >= 1  # ノートと問いの両方の混入を実際に通している
    assert run["notes"]["leaked"] == notes and run["notes"]["leaked_questions"] == asks
    assert run["notes"]["fallback"] >= run["notes"]["leaked"]


def test_a_run_across_two_lines_is_not_taken_from_the_lines():
    answers = ["雨やむまで傘差して"]
    lines = ["軒を借りて雨やむまで", "傘差して軒を借りて"]
    assert pipeline.leaks_answer("雨やむまで傘差してですか", answers, lines)  # 境目の語は台詞に無い


def test_answer_made_only_of_the_line_is_not_a_leak():
    line = ["傘を一本ずつ貸して勘定して"]
    assert not pipeline.leaks_answer(
        "傘を一本ずつ貸して勘定して、ですか？", ["傘を一本ずつ貸して勘定して"], line
    )


def test_line_words_joined_to_the_performers_own_words_is_a_leak():
    line = ["裏長屋の軒先借りたんで"]
    answers = ["軒先借りたんでお客が沸く"]  # 演者の語は 5 字。6 字に届くのは接合だけ
    assert pipeline.leaks_answer(
        "軒先借りたんでお客が沸くんですね？", answers, line
    )  # 接合の 6 字は台詞に無い


def test_a_short_answer_alone_is_not_checked():
    line = ["裏長屋の軒先借りたんで"]
    assert pipeline.leaks_answer(
        "裏長屋の軒先借りたんで沸く？", ["裏長屋の軒先借りたんで沸く"], line
    )  # 接合は 2 字でも当たる
    assert not pipeline.leaks_answer(
        "客が沸く所ですか？", ["客が沸く"], line
    )  # 6 字に満たない答えは見ない（限界）
