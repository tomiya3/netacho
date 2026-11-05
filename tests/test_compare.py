"""2 本の比較のテスト。素材は自作の短い文だけ。"""

import json
import shutil
from pathlib import Path

import pytest
from fakes import ScriptedClient, text_of

from netacho import compare, pipeline, store, view
from netacho.budget import Budget

LINES = [
    "雨の日に傘を借りに行った男の話でございます",
    "ご隠居さん傘を貸してくださいなと頼みました",
    "おまえさんこの傘は骨が三本しかないよと言う",
    "骨が三本でも雨はしのげますと男は笑いました",
    "ところが傘をさしたとたんに風が吹いてきまして",
    "傘はひっくり返って空へ飛んでいってしまいました",
    "ご隠居が言うにはあれは貸した傘ではなくて",
    "飛ぶ傘だったかと申しまして大きなお世話でございます",
]


def side(koza, texts, humps, *, step=10.0, pauses=None):
    rows = [
        {
            "seg_id": f"seg-{n:04d}",
            "start": n * step,
            "end": (n + 1) * step,
            "text": text,
            "scene_id": "scene-01",
        }
        for n, text in enumerate(texts)
    ]
    silences = [[(n + 1) * step - v, (n + 1) * step] for n, v in (pauses or {}).items()]
    return compare.Side(koza, rows, humps, silences)


def spots(a, b):
    return compare.compare(a, b, {"scene-01": "傘を借りる"})["spots"]


def test_same_line_and_same_words_is_kept():
    found = spots(side("a", LINES, [15.0]), side("b", LINES, [15.0]))
    assert [s["kind"] for s in found] == ["kept"]


def test_timing_is_compared_by_the_line_not_the_clock():
    """2 本目は枕が長く、同じ台詞が 100 秒遅い。時刻では違うが同じ所。"""
    b = side("b", LINES, [15.0 + 100.0])
    for row in b.lines:
        row["start"] += 100.0
        row["end"] += 100.0
    assert [s["kind"] for s in spots(side("a", LINES, [15.0]), b)] == ["kept"]


def test_laugh_that_moved_to_another_line():
    found = spots(side("a", LINES, [15.0]), side("b", LINES, [28.0]))
    assert found[0]["kind"] == "moved" and found[0]["laugh_b"] == 28.0


def test_laugh_only_in_one_recording():
    found = spots(side("a", LINES, [15.0]), side("b", LINES, [75.0]))
    kinds = {s["kind"] for s in found}
    assert kinds == {"one_side"}
    assert any(s["laugh_b"] is None for s in found) and any(s["laugh_a"] is None for s in found)


def test_pause_before_the_line_differs():
    a = side("a", LINES, [15.0], pauses={0: 0.3})
    b = side("b", LINES, [15.0], pauses={0: 1.5})
    found = spots(a, b)
    assert found[0]["kind"] == "ma" and (found[0]["ma_a"], found[0]["ma_b"]) == (0.3, 1.5)


def test_small_pause_difference_is_within_error():
    a = side("a", LINES, [15.0], pauses={0: 0.3})
    b = side("b", LINES, [15.0], pauses={0: 0.9})
    assert spots(a, b)[0]["kind"] == "kept"


def test_pause_ignores_where_the_transcript_cut_the_line():
    """同じ無音なら、区間の切れ目が少しずれても同じ間と見る。"""
    a = side("a", LINES, [15.0], pauses={0: 1.5})
    b = side("b", LINES, [15.0], pauses={0: 1.5})
    b.lines[1]["start"] += 0.5
    assert spots(a, b)[0]["kind"] == "kept"


def test_vad_gives_the_silences():
    vad = [{"start": 0.0, "end": 4.0}, {"start": 5.5, "end": 9.0}]
    assert compare.silences_of(vad) == [[4.0, 5.5]]


def test_no_laughs_on_one_side_compares_only_words():
    """片方に山が無いのは検出の問題かもしれない。全部を「片方だけ」にしない。"""
    doc = compare.compare(side("a", LINES, [15.0, 45.0]), side("b", LINES, []), {})
    assert doc["laughs_compared"] is False and doc["spots"] == []


def test_laugh_after_the_line_maps_from_the_end_of_the_line():
    """言い終えた 3 秒後の笑いは、行の長さが違っても同じ所。"""
    a = side("a", LINES, [13.0], step=10.0)
    b = side("b", LINES, [23.0], step=20.0)
    b.lines[0]["end"] = 20.0
    a.lines[1]["start"], a.lines[0]["end"] = 15.0, 10.0
    assert compare._expected(a, b, 0, 0, 13.0) == 23.0


def test_the_same_one_sided_place_is_not_counted_twice():
    found = spots(side("a", LINES, [15.0]), side("b", LINES, [18.0 + 40.0]))
    keys = [(s["kind"], s["a"]["t"], s["b"]["t"]) for s in found]
    assert len(keys) == len(set(keys))


def test_spot_id_comes_from_the_content():
    one = spots(side("a", LINES, [15.0]), side("b", LINES, [15.0]))
    two = spots(side("a", LINES, [15.0, 75.0]), side("b", LINES, [15.0, 75.0]))
    assert one[0]["id"] == two[0]["id"]


def test_pause_counts_only_when_the_line_before_also_aligns():
    changed = list(LINES)
    changed[0] = "全く別の始まりの台詞が入っておりまして長くなりますが"
    a = side("a", LINES, [15.0], pauses={0: 0.3})
    b = side("b", changed, [15.0], pauses={0: 1.5})
    assert all(s["kind"] != "ma" for s in spots(a, b))


def test_score_size_is_never_used():
    """山の高さの違いは Side に入らない。来ているかどうかだけ。"""
    assert not hasattr(side("a", LINES, [15.0]), "scores")


def test_changed_words_near_a_laugh():
    changed = list(LINES)
    changed[1] = "ご隠居さん雨がやむまで傘を一本貸しとくれと言いました"
    found = spots(side("a", LINES, [15.0]), side("b", changed, [15.0]))
    assert found[0]["kind"] == "words" and found[0]["laugh_a"] == 15.0


def test_changed_words_without_a_laugh():
    changed = list(LINES)
    changed[4] = "そのあと町内の連中が総出で大騒ぎになりましてなあ"
    changed[5] = "長屋の者も大家も一緒になって走り回ったわけでございます"
    found = spots(side("a", LINES, []), side("b", changed, []))
    assert [s["kind"] for s in found] == ["words"] and found[0]["laugh_a"] is None


def test_punctuation_is_not_a_change():
    marked = [text[:5] + "、" + text[5:] + "。" for text in LINES]
    assert spots(side("a", LINES, []), side("b", marked, [])) == []


def test_different_stories_are_not_compared():
    other = [
        "今日は相撲の話でして千鳥の親方が出てまいります",
        "番付の上から順に呼び出しが声を張り上げて",
        "土俵の真ん中で扇子を開いて見得を切るわけで",
    ]
    doc = compare.compare(side("a", LINES, [15.0]), side("b", other, [15.0]), {})
    assert doc["comparable"] is False and doc["spots"] == []


def test_scene_that_does_not_align_is_left_out_and_told():
    a = side("a", LINES, [15.0])
    for row in a.lines[:2]:
        row["scene_id"] = "scene-02"
    b = side(
        "b",
        ["全く別の枕でございまして今日は寒いですなと", "皆さんお変わりなくと申しまして", *LINES[2:]],
        [15.0],
    )
    doc = compare.compare(a, b, {})
    assert {s["scene_id"]: s["compared"] for s in doc["scenes"]} == {"scene-02": False, "scene-01": True}
    assert all(s["scene_id"] != "scene-02" for s in doc["spots"])


def make_spot(n, kind, t):
    return {"id": f"spot-{n:04d}", "kind": kind, "a": {"t": t}}


def test_select_mixes_kinds_and_keeps_distance():
    rows = [
        make_spot(1, "kept", 0.0),
        make_spot(2, "kept", 10.0),
        make_spot(3, "kept", 100.0),
        make_spot(4, "moved", 200.0),
    ]
    chosen = compare.select(rows, 3)
    assert [c["id"] for c in chosen] == ["spot-0001", "spot-0003", "spot-0004"]


def test_select_stops_at_the_limit():
    assert len(compare.select([make_spot(n, "kept", n * 100.0) for n in range(9)], 5)) == 5


@pytest.mark.parametrize("value", ["", "。、 "])
def test_plain_drops_marks(value):
    assert compare.plain(value) == ""


DATA = Path(__file__).parent / "fixtures" / "dummy_koza"
P, N = "performer-dummy", "雨宿り勘定"
K1, K2 = "2026-01-01-dummy-hall", "2026-02-01-dummy-hall"
REPLY = {"schema_version": 1, "questions": []}


def two_books(tmp_path, *, change=None, other_neta=None):
    """ダミーの高座を 2 本にする。2 本目は別の日の同じ演目。"""
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    neta = store.Neta(P, N, root)
    (neta.koza_dir(K1) / "compare.json").unlink()
    home = neta.sibling(other_neta) if other_neta else neta
    src, dst = neta.koza_dir(K1), home.koza_dir(K2)
    shutil.copytree(src, dst)
    (dst / "run.json").unlink()
    for name, schema, key in (
        ("transcript.json", "transcript", "koza_id"),
        ("seisho.json", "seisho", "koza_id"),
        ("reactions.json", "reactions", "koza_id"),
        ("timeline.json", "timeline", "koza_id"),
    ):
        doc = store.read(dst / name, schema)
        doc[key] = K2
        if name == "seisho.json" and change:
            doc = change(doc)
        store.write(dst / name, doc, schema)
    return root, neta


def run(root, client, tmp_path, **kw):
    budget = Budget(tmp_path / "ledger.json")
    kw.setdefault("other_koza", K2)
    return pipeline.compare_koza(P, N, K1, client=client, budget=budget, root=root, **kw)


def test_same_recording_twice_asks_about_kept_places(tmp_path):
    root, neta = two_books(tmp_path)
    reply = lambda kw: json.dumps(  # noqa: E731
        {
            "schema_version": 1,
            "questions": [
                {"spot_id": r["spot_id"], "text": "ここは変えない所ですか？"} for r in json.loads(text_of(kw))
            ],
        },
        ensure_ascii=False,
    )
    client = ScriptedClient(asker=[reply])
    doc = run(root, client, tmp_path)
    assert doc["comparable"] is True and doc["share"] == 1.0
    assert {s["kind"] for s in doc["spots"]} <= {"kept"} and doc["counts"]["kept"] >= 1
    questions = store.read(neta.questions, "questions")["questions"]
    new = [q for q in questions if "spot_id" in q]
    assert new and new[0]["text"] == "ここは変えない所ですか？"
    issue = next(i for i in store.read(neta.review, "review")["issues"] if i["id"] == new[0]["issue_id"])
    assert issue["role"] == "asker" and issue["kind"] == "ask_performer" and issue["resolved"] is False


def test_asker_sees_only_kinds_and_lines(tmp_path):
    root, _ = two_books(tmp_path)
    client = ScriptedClient(asker=[REPLY])
    run(root, client, tmp_path)
    rows = json.loads(text_of(client.calls["asker"][0]))
    assert set(rows[0]) == {"spot_id", "kind", "quote_a", "quote_b"}


def test_bad_or_missing_question_becomes_the_fixed_one(tmp_path):
    root, neta = two_books(tmp_path)
    client = ScriptedClient(asker=[REPLY])
    run(root, client, tmp_path)
    q = [x for x in store.read(neta.questions, "questions")["questions"] if "spot_id" in x]
    assert q[0]["text"] == pipeline.FIXED_ASK["kept"]
    run_doc = store.read(neta.koza_dir(K1) / "run.json", "run")
    assert run_doc["compare"]["fallback"] >= 1


@pytest.mark.parametrize(
    "text",
    [
        "ここは受けなかったですね。",
        "客の反応が無いですね。",
        "3 秒も間があります。",
        "「作った語」の所ですね。わざとですか？",
    ],
)
def test_question_rules(text):
    spot = {"a": {"quote": "本物の台詞"}, "b": {"quote": "本物の台詞"}}
    assert not pipeline.ask_is_ok(text, spot)


def test_quote_from_the_lines_is_allowed():
    spot = {"a": {"quote": "本物の台詞"}, "b": {"quote": "別の台詞"}}
    assert pipeline.ask_is_ok("「本物の台詞」は 2 本とも同じですね。わざとですか？", spot)


def test_second_run_does_not_ask_the_same_place_twice(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, ScriptedClient(asker=[REPLY]), tmp_path)
    first = store.read(neta.questions, "questions")["questions"]
    doc = run(root, ScriptedClient(asker=[REPLY]), tmp_path)
    after = store.read(neta.questions, "questions")["questions"]
    assert len(after) == len(first)
    ids = {s["id"] for s in doc["spots"]}
    assert all(q["spot_id"] in ids for q in after if "spot_id" in q)  # 問いが別の所を指さない
    assert any("question_id" in s for s in doc["spots"])
    assert store.read(neta.koza_dir(K1) / "run.json", "run")["compare"]["fallback"] == 0


def test_failed_asker_keeps_the_cost_and_no_questions(tmp_path):
    root, neta = two_books(tmp_path)

    def broken(kw):
        raise RuntimeError("途中で落ちた")

    with pytest.raises(RuntimeError):
        run(root, ScriptedClient(asker=[broken]), tmp_path)
    assert "compare" in store.read(neta.koza_dir(K1) / "run.json", "run")
    assert not any("spot_id" in q for q in store.read(neta.questions, "questions")["questions"])


def test_answers_and_books_are_not_overwritten(tmp_path):
    root, neta = two_books(tmp_path)
    kata, hanashi = neta.kata.read_bytes(), neta.hanashi.read_bytes()
    answered = [q for q in store.read(neta.questions, "questions")["questions"] if "answer" in q]
    run(root, ScriptedClient(asker=[REPLY]), tmp_path)
    assert neta.kata.read_bytes() == kata and neta.hanashi.read_bytes() == hanashi
    after = store.read(neta.questions, "questions")["questions"]
    assert all(q in after for q in answered)


def test_second_koza_can_live_in_another_folder(tmp_path):
    root, neta = two_books(tmp_path, other_neta="雨宿り勘定-2")
    doc = run(root, ScriptedClient(asker=[REPLY]), tmp_path, other_neta="雨宿り勘定-2")
    assert doc["other_neta"] == "雨宿り勘定-2"
    assert view.compare_view(neta, neta.koza_dir(K1))["other_reactions"] is not None


def test_clean_copy_is_required(tmp_path):
    root, neta = two_books(tmp_path)
    (neta.koza_dir(K2) / "seisho.json").unlink()
    with pytest.raises(store.StoreError, match="清書"):
        run(root, ScriptedClient(asker=[REPLY]), tmp_path)


def test_stories_that_do_not_align_ask_nothing_and_call_no_llm(tmp_path):
    def other_story(doc):
        for n, row in enumerate(doc["lines"]):
            row["text"] = f"全く別の噺の台詞がここに続いていて番号は{n * 7919}です"
            row["changed"] = True
        return doc

    root, neta = two_books(tmp_path, change=other_story)
    transcript = store.read(neta.koza_dir(K2) / "transcript.json", "transcript")
    client = ScriptedClient(asker=[])
    doc = run(root, client, tmp_path)
    assert transcript and doc["comparable"] is False and client.calls["asker"] == []
    assert not any("spot_id" in q for q in store.read(neta.questions, "questions")["questions"])


def test_compare_is_written_for_the_screen(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, ScriptedClient(asker=[REPLY]), tmp_path)
    assert view.payload(neta, K1)["compare"]["koza_b"] == K2


def test_screen_without_a_comparison_is_fine(tmp_path):
    root, neta = two_books(tmp_path)
    assert view.payload(neta, K1)["compare"] is None
