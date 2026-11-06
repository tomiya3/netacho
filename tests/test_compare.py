"""2 本の比較のテスト。素材は自作の短い文だけ。"""

import json
import shutil
from pathlib import Path

import pytest
from fakes import ScriptedClient, text_of

from netacho import compare, performer, pipeline, store, view
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


NO_MAKURA = {"a_last": None, "b_last": None, "topics": []}


def seg(n):
    return f"seg-{n:04d}"


def row(relation, a=None, b=None):
    a = a or (None, None)
    b = b or (None, None)
    return {
        "relation": relation,
        "a_first": None if a[0] is None else seg(a[0]),
        "a_last": None if a[1] is None else seg(a[1]),
        "b_first": None if b[0] is None else seg(b[0]),
        "b_last": None if b[1] is None else seg(b[1]),
    }


def draft(*rows):
    return {"schema_version": 1, "passages": list(rows)}


ALL_SAME = draft(row("same", (0, 3), (0, 3)), row("same", (4, 7), (4, 7)))


def spots(a, b, d=ALL_SAME):
    return compare.compare(a, b, d, {"scene-01": "傘を借りる"})["spots"]


MANY = [15.0, 45.0, 55.0, 62.0, 75.0, 79.0]


def test_same_line_and_same_words_is_kept():
    found = spots(
        side("a", LINES, [15.0, 45.0, 75.0, 79.0, 62.0, 55.0]),
        side("b", LINES, [15.0, 45.0, 75.0, 79.0, 62.0, 55.0]),
    )
    assert {s["kind"] for s in found} == {"kept"}


def test_timing_is_compared_by_the_passage_not_the_clock():
    """2 本目は枕が長く、同じ台詞が 100 秒遅い。時刻では違うが同じ所。"""
    b = side("b", LINES, [h + 100.0 for h in MANY])
    for line in b.lines:
        line["start"] += 100.0
        line["end"] += 100.0
    assert {s["kind"] for s in spots(side("a", LINES, MANY), b)} == {"kept"}


def test_laugh_that_moved_inside_the_passage():
    found = spots(side("a", LINES, [15.0, *MANY[1:]]), side("b", LINES, [28.0, *MANY[1:]]))
    assert any(s["kind"] == "moved" and s["laugh_b"] == 28.0 for s in found)


def test_laugh_only_in_one_recording():
    found = spots(side("a", LINES, [15.0, *MANY[1:]]), side("b", LINES, MANY[1:]))
    assert any(s["kind"] == "one_side" and s["laugh_a"] == 15.0 and s["laugh_b"] is None for s in found)


def test_silence_after_the_line_begins_is_not_a_pause():
    b = side("b", LINES, [15.0])
    b.silences = [[10.5, 12.0]]
    assert compare._pause_before(b, 1) == 0.0


def test_a_long_blank_is_not_a_pause():
    b = side("b", LINES, [15.0])
    b.silences = [[3.0, 10.0]]
    assert compare._pause_before(b, 1) == 0.0


def test_vad_gives_the_silences():
    assert compare.silences_of([{"start": 0.0, "end": 4.0}, {"start": 5.5, "end": 9.0}]) == [[4.0, 5.5]]


def test_no_laughs_on_one_side_compares_only_words():
    """両方とも山が無ければ、言葉の違いだけを出す。"""
    d = draft(row("same", (0, 3), (0, 3)), row("changed", (4, 7), (4, 7)))
    doc = compare.compare(side("a", LINES, []), side("b", LINES, []), d, {})
    assert doc["laughs_compared"] is False and [s["kind"] for s in doc["spots"]] == ["changed"]


def test_practice_recording_does_not_make_everything_one_sided():
    """稽古に客はいない。笑いが無いことを「片方だけ」と呼ばない。"""
    doc = compare.compare(side("a", LINES, MANY), side("b", LINES, [15.0]), ALL_SAME, {})
    assert doc["practice"] is True
    assert {s["kind"] for s in doc["spots"]} <= {"kept", "words"}


def test_missing_scores_are_not_called_practice():
    """スコアが無いのは未検出。客のいない稽古とは言えない。"""
    b = side("b", LINES, [])
    b.scored = False
    doc = compare.compare(side("a", LINES, MANY), b, ALL_SAME, {})
    assert doc["practice"] is False and doc["laughs_compared"] is False


def test_a_laugh_at_the_edge_of_two_passages_is_told_once():
    found = spots(side("a", LINES, [41.0, *MANY[2:]]), side("b", LINES, [41.0, *MANY[2:]]))
    assert sum(s["laugh_a"] == 41.0 for s in found) == 1


def test_laugh_on_words_only_in_the_first_is_added():
    """本番で来た言葉が、もう 1 本に無い。"""
    d = draft(row("same", (0, 3), (0, 3)), row("only_a", (4, 5)), row("same", (6, 7), (4, 5)))
    b = side("b", LINES[:4] + LINES[6:], [15.0])
    found = compare.compare(side("a", LINES, [*MANY]), b, d, {})["spots"]
    added = [s for s in found if s["kind"] == "added"]
    assert added and added[0]["b"] is None and added[0]["laugh_a"] == 45.0


def test_makura_is_not_called_added():
    """稽古に枕が無いのは当たり前。噺の中で無い言葉だけを足した言葉と呼ぶ。"""
    d = draft(
        row("only_a", (0, 1)), row("same", (2, 3), (0, 1)), row("only_a", (4, 5)), row("same", (6, 7), (2, 3))
    )
    a = side("a", LINES, [5.0, 45.0, 62.0, 75.0, 15.0, 25.0])
    b = side("b", LINES[2:4] + LINES[6:], [])
    found = compare.compare(a, b, d, {})["spots"]
    assert [s["laugh_a"] for s in found if s["kind"] == "added"] == [45.0]


def test_words_changed_with_a_laugh():
    d = draft(row("changed", (0, 3), (0, 3)), row("same", (4, 7), (4, 7)))
    found = spots(side("a", LINES, MANY), side("b", LINES, MANY), d)
    assert found[0]["kind"] == "words" and found[0]["laugh_a"] == 15.0


def test_words_changed_without_a_laugh_is_shown_not_asked():
    d = draft(row("same", (0, 3), (0, 3)), row("changed", (4, 7), (4, 7)))
    found = spots(side("a", LINES, MANY[:1]), side("b", LINES, MANY[:1]), d)
    assert [s["kind"] for s in found if s["laugh_a"] is None] == ["changed"]
    assert all(s["kind"] != "changed" for s in compare.candidates(found))


def test_stories_that_do_not_correspond_are_not_compared():
    d = draft(row("only_a", (0, 7)), row("only_b", b=(0, 7)))
    doc = compare.compare(side("a", LINES, [15.0]), side("b", LINES, [15.0]), d, {})
    assert doc["comparable"] is False and doc["spots"] == []


def test_a_long_makura_does_not_lower_the_share():
    """1 本目の長い枕は比べない。短いほうに対する割合で見る。"""
    long_a = ["今日はよう来てくださいました長い枕でございます"] * 40 + LINES
    a = side("a", long_a, [])
    d = draft(row("only_a", (0, 39)), row("same", (40, 47), (0, 7)))
    assert compare.compare(a, side("b", LINES, []), d, {})["share"] == 1.0


def test_unknown_ids_and_backward_order_are_dropped():
    d = draft(
        row("same", (0, 1), (0, 1)),
        row("same", (99, 99), (2, 2)),
        row("same", (0, 1), (2, 3)),
        row("same", (2, 7), (1, 1)),
        row("same", (2, 7), (2, 7)),
    )
    doc = compare.compare(side("a", LINES, []), side("b", LINES, []), d, {})
    assert doc["dropped"] == 3 and doc["passages"]["same"] == 2


def test_role_cannot_add_a_range_to_the_wrong_side():
    d = draft(row("only_a", b=(0, 1)))
    assert compare.compare(side("a", LINES, []), side("b", LINES, []), d, {})["dropped"] == 1


def test_the_same_place_is_not_counted_twice():
    found = spots(side("a", LINES, MANY), side("b", LINES, MANY))
    keys = [(s["kind"], s["a"]["t"], s["b"]["t"]) for s in found]
    assert len(keys) == len(set(keys))


def test_spot_id_comes_from_the_content():
    one = spots(side("a", LINES, MANY), side("b", LINES, MANY))
    two = spots(side("a", LINES, MANY), side("b", LINES, MANY))
    assert [s["id"] for s in one] == [s["id"] for s in two]


def test_score_size_is_never_used():
    """山の高さの違いは Side に入らない。来ているかどうかだけ。"""
    assert not hasattr(side("a", LINES, [15.0]), "scores")


def make_spot(n, laugh=True):
    t = n * 10.0
    return {
        "id": f"spot-{n:04d}",
        "kind": "kept",
        "a": {"t": t},
        "laugh_a": t if laugh else None,
        "laugh_b": None,
    }


def test_candidates_are_every_laugh_not_the_earliest_ones():
    rows = [make_spot(n) for n in range(38)] + [make_spot(99, laugh=False)]
    assert compare.candidates(rows) == rows[:38]


def test_many_candidates_are_all_read():
    rows = [{"laugh_a": float(n), "laugh_b": None} for n in range(100)]
    assert compare.candidates(rows) == rows


def test_slow_recording_does_not_make_everything_moved():
    """テンポの遅い 2 本目は頭からの秒が積もる。くだりの長さの比で写す。"""
    b = side("b", LINES, [h * 2 for h in MANY], step=20.0)
    a = side("a", LINES, MANY)
    found = spots(a, b, draft(row("same", (0, 7), (0, 7))))
    assert {s["kind"] for s in found} == {"kept"}


def test_a_very_short_passage_does_not_blow_up_the_scale():
    """1 行と 8 行の対応で、比をそのまま使うと 80 倍に写る。上限で止める。"""
    a = side("a", LINES, [5.0, *MANY[1:]])
    b = side("b", LINES, [8.0, *MANY[1:]], step=100.0)
    found = spots(a, b, draft(row("same", (0, 0), (0, 7))))
    assert [s["kind"] for s in found if s["laugh_a"] == 5.0] == ["kept"]  # 10 秒に写り、8 秒の山と組む


def test_passage_with_no_length_does_not_divide_by_zero():
    a = side("a", LINES, [5.0])
    for line in a.lines:
        line["end"] = line["start"]
    assert isinstance(spots(a, side("b", LINES, [5.0])), list)


@pytest.mark.parametrize("t", [5.0, 995.0])
def test_lines_shown_stay_full_at_both_ends_of_a_long_passage(t):
    texts = [f"長いくだりの{n}行目でございます" for n in range(100)]
    a, b = side("a", texts, [t]), side("b", texts, [])
    spot = {"laugh_a": t, "a": {"t": t}, "span_a": [0, 99], "span_b": [0, 99]}
    assert len(compare.context_rows(spot, a, b)["lines_b"]) == compare.LINES_B_MAX


def test_long_passage_shows_the_line_of_the_other_laugh():
    texts = [f"長いくだりの{n}行目でございます" for n in range(100)]
    a, b = side("a", texts, [100.0]), side("b", texts, [850.0])
    spot = {"laugh_a": 100.0, "laugh_b": 850.0, "a": {"t": 100.0}, "span_a": [0, 99], "span_b": [0, 99]}
    rows = compare.context_rows(spot, a, b)["lines_b"]
    assert any(r["seg_id"] == seg(85) for r in rows)


def test_a_far_laugh_is_not_paired():
    a = side("a", LINES, [5.0, *MANY[1:]])
    b = side("b", LINES, [75.0, *MANY[1:-1], 79.0])
    found = spots(a, b, draft(row("same", (0, 7), (0, 7))))
    assert any(s["laugh_a"] == 5.0 and s["kind"] == "one_side" for s in found)


def test_long_passage_shows_only_the_lines_around_the_place():
    texts = [f"長いくだりの{n}行目でございます" for n in range(100)]
    a, b = side("a", texts, [505.0]), side("b", texts, [])
    spot = {"laugh_a": 505.0, "a": {"t": 500.0}, "span_a": [0, 99], "span_b": [0, 99]}
    rows = compare.context_rows(spot, a, b)["lines_b"]
    assert len(rows) == compare.LINES_B_MAX
    assert any(r["seg_id"] == seg(50) for r in rows)


def makura_draft():
    """seg-0000 から 3 までが枕。1 本目の話題の片方は 2 本目にも同じ話題がある。"""
    topics = [
        {"topic": "近況", "prepares": False, **topic_row((0, 1), None)},
        {"topic": "噺の説明", "prepares": True, **topic_row((2, 3), (0, 1))},
    ]
    return {
        "schema_version": 1,
        "makura": {"a_last": seg(3), "b_last": seg(1), "topics": topics},
        "passages": [row("same", (4, 7), (2, 5))],
    }


def topic_row(a, b):
    return {
        "a_first": seg(a[0]) if a else None,
        "a_last": seg(a[1]) if a else None,
        "b_first": seg(b[0]) if b else None,
        "b_last": seg(b[1]) if b else None,
    }


def test_makura_is_read_by_topic_not_line_by_line():
    a = side("a", LINES, [12.0, 25.0, 45.0])
    b = side("b", LINES[2:], [15.0])
    doc = compare.compare(a, b, makura_draft(), {"scene-01": "場面"})
    assert doc["share"] == 1.0  # 枕の違いで割合を下げない
    near = [s for s in doc["spots"] if s["kind"] == "makura"]
    assert [s["laugh_a"] for s in near] == [12.0, 25.0]
    assert near[0]["span_b"] is None and near[1]["span_b"] == [0, 1]  # 同じ話題の時だけ 2 本目の行がある
    assert all(s["b"] is None for s in near)
    first, second = doc["makura"]
    assert (first["topic"], first["prepares"], first["a"]["humps"], first["b"]) == ("近況", False, 1, None)
    assert (second["prepares"], second["a"]["humps"], second["b"]["humps"]) == (True, 1, 1)


def test_passage_inside_makura_is_dropped():
    bad = makura_draft()
    bad["passages"].insert(0, row("same", (2, 3), (0, 1)))
    doc = compare.compare(side("a", LINES, []), side("b", LINES[2:], []), bad, {})
    assert doc["dropped"] == 1 and doc["passages"]["same"] == 1


def test_topic_outside_makura_or_missing_is_dropped():
    bad = makura_draft()
    bad["makura"]["topics"].append({"topic": "外", "prepares": False, **topic_row((5, 6), None)})
    bad["makura"]["topics"].append({"topic": "無い", "prepares": False, **topic_row((90, 91), None)})
    doc = compare.compare(side("a", LINES, []), side("b", LINES[2:], []), bad, {})
    assert doc["dropped"] == 2 and [x["topic"] for x in doc["makura"]] == ["近況", "噺の説明"]


def test_unknown_end_of_makura_counts_as_dropped_and_compares_all():
    bad = makura_draft()
    bad["makura"]["a_last"] = "seg-9999"
    doc = compare.compare(side("a", LINES, []), side("b", LINES[2:], []), bad, {})
    assert doc["dropped"] >= 1 and all(x["a"] is None for x in doc["makura"])
    assert 0.0 <= doc["share"] <= 1.0 and all(s["kind"] != "makura" for s in doc["spots"])


def test_makura_without_a_scene_still_reaches_the_asker():
    a = side("a", LINES, [12.0])
    for line in a.lines[:4]:
        line["scene_id"] = None
    doc = compare.compare(a, side("b", LINES[2:], []), makura_draft(), {"scene-01": "噺"})
    assert [s["scene_id"] for s in doc["spots"] if s["kind"] == "makura"] == ["scene-01"]


def test_laugh_after_the_story_starts_is_not_a_makura_laugh():
    a = side("a", LINES, [41.0])  # 枕は 40 秒まで。41 秒は噺の最初の行の中
    doc = compare.compare(a, side("b", LINES[2:], []), makura_draft(), {})
    assert all(s["kind"] != "makura" for s in doc["spots"])


def test_makura_scene_is_not_listed_as_uncompared():
    a = side("a", LINES, [])
    for line in a.lines[:4]:
        line["scene_id"] = "scene-00"
    doc = compare.compare(a, side("b", LINES[2:], []), makura_draft(), {"scene-00": "枕", "scene-01": "噺"})
    assert [s["title"] for s in doc["scenes"]] == ["噺"]


def test_makura_spot_keeps_its_kind_and_cannot_ask_of_pauses():
    a, b = side("a", LINES, [12.0]), side("b", LINES[2:], [])
    spot = next(s for s in compare.compare(a, b, makura_draft(), {})["spots"] if s["kind"] == "makura")
    shown = compare.context_rows(spot, a, b)
    row = {"a_seg": seg(1), "b_seg": None, "relation": "none", "text": "どこでも使う話ですか？"}
    assert compare.settle(spot, row, a, b, shown)["kind"] == "makura"
    assert compare.settle(spot, {**row, "text": "この間はわざとですか？"}, a, b, shown) is None


def test_makura_gets_one_question_at_most(tmp_path):
    root, neta = two_books(tmp_path)
    head = {
        "schema_version": 1,
        "makura": {
            "a_last": "seg-0004",
            "b_last": "seg-0004",
            "topics": [
                {
                    "topic": "近況",
                    "prepares": False,
                    **{
                        k: f"seg-000{n}"
                        for k, n in zip(("a_first", "a_last", "b_first", "b_last"), (1, 4, 1, 4), strict=True)
                    },
                }
            ],
        },
        "passages": [dict(ALIGN["passages"][0], a_first="seg-0005", b_first="seg-0005")],
    }

    def reply(kw):
        rows = [r for r in json.loads(text_of(kw)) if r["kind"] == "makura"]
        assert rows, "枕の笑いが聞き役に届いていない"
        pick = rows[0]
        questions = [
            {
                "spot_id": pick["spot_id"],
                "a_seg": pick["lines_a"][-1]["seg_id"],
                "b_seg": None,
                "relation": "none",
                "text": f"近況の話で受けていました。なんでこの話題にしたんですか{'？' * n}",
            }
            for n in (1, 2)
        ]
        return json.dumps({"schema_version": 1, "questions": questions}, ensure_ascii=False)

    for kdir in (neta.koza_dir(K1), neta.koza_dir(K2)):
        doc = store.read(kdir / "reactions.json", "reactions")
        doc["scores"]["laugh"][8] = 0.0
        doc["scores"]["laugh"][1] = 0.2
        store.write(kdir / "reactions.json", doc, "reactions")
    run(root, client_of(reply, aligner=head), tmp_path)
    asked = [q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q]
    assert len(asked) == 1
    performer.answer(neta, question=asked[0]["id"], speaker="本人", words="どこでも使います")
    run(root, client_of(reply, aligner=head), tmp_path)  # 答えた枕があれば、やり直しても増やさない
    again = [q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q]
    assert [q["id"] for q in again] == [asked[0]["id"]]


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
    for kdir in (src, dst):  # 台詞の途中に笑いの山を置く
        doc = store.read(kdir / "reactions.json", "reactions")
        doc["scores"]["laugh"][8] = 0.2
        store.write(kdir / "reactions.json", doc, "reactions")
    return root, neta


ALIGN = {
    "schema_version": 1,
    "makura": NO_MAKURA,
    "passages": [
        {
            "relation": "same",
            "a_first": "seg-0001",
            "a_last": "seg-0012",
            "b_first": "seg-0001",
            "b_last": "seg-0012",
        }
    ],
}


def client_of(*asker, aligner=None):
    return ScriptedClient(aligner=[aligner or ALIGN] * 2, asker=list(asker))


def run(root, client, tmp_path, **kw):
    budget = Budget(tmp_path / "ledger.json")
    kw.setdefault("other_koza", K2)
    return pipeline.compare_koza(P, N, K1, client=client, budget=budget, root=root, **kw)


def answer_all(kw):
    """聞き役の返事の代わり。見せた行の最初を選んで問う。"""
    rows = json.loads(text_of(kw))
    return json.dumps(
        {
            "schema_version": 1,
            "questions": [
                {
                    "spot_id": r["spot_id"],
                    "a_seg": r["lines_a"][-1]["seg_id"],
                    "b_seg": r["lines_b"][-1]["seg_id"],
                    "relation": "same",
                    "text": "ここは変えない所ですか？",
                }
                for r in rows
            ],
        },
        ensure_ascii=False,
    )


def test_same_recording_twice_asks_about_kept_places(tmp_path):
    root, neta = two_books(tmp_path)
    reply = answer_all
    client = client_of(reply)
    doc = run(root, client, tmp_path)
    assert doc["comparable"] is True and doc["share"] == 1.0
    assert {s["kind"] for s in doc["spots"]} <= {"kept", "moved"} and doc["counts"]["kept"] >= 1
    questions = store.read(neta.questions, "questions")["questions"]
    new = [q for q in questions if "spot_id" in q]
    assert new and new[0]["text"] == "ここは変えない所ですか？"
    issue = next(i for i in store.read(neta.review, "review")["issues"] if i["id"] == new[0]["issue_id"])
    assert issue["role"] == "asker" and issue["kind"] == "ask_performer" and issue["resolved"] is False


def test_asker_sees_only_kinds_and_lines(tmp_path):
    root, _ = two_books(tmp_path)
    client = client_of(REPLY)
    run(root, client, tmp_path)
    rows = json.loads(text_of(client.calls["asker"][0]))
    assert set(rows[0]) == {"spot_id", "kind", "other", "lines_a", "lines_b"}
    assert set(rows[0]["lines_b"][0]) == {"seg_id", "text", "間"}  # 時刻もスコアも見せない


def test_aligner_sees_only_the_title_and_the_lines(tmp_path):
    root, _ = two_books(tmp_path)
    client = client_of(REPLY)
    run(root, client, tmp_path, title="正しい題")
    sent = json.loads(text_of(client.calls["aligner"][0]))
    assert sent["演目"] == "正しい題" and set(sent) == {"演目", "1 本目", "2 本目"}
    assert set(sent["1 本目"][0]) == {"seg_id", "text"}  # 笑いの位置も時刻も見せない


def test_no_answer_asks_nothing_and_is_counted(tmp_path):
    """場所の合わない問いを出すより、問わない。決まった文で埋めない。"""
    root, neta = two_books(tmp_path)
    run(root, client_of(REPLY), tmp_path)
    assert not any("spot_id" in x for x in store.read(neta.questions, "questions")["questions"])
    assert store.read(neta.koza_dir(K1) / "run.json", "run")["compare"]["skipped"] >= 1


def shown_lines():
    a, b = side("a", LINES, [15.0]), side("b", LINES, [])
    spot = {"laugh_a": 15.0, "a": {"t": 10.0}, "span_a": [0, 3], "span_b": [0, 3]}
    return a, b, spot, compare.context_rows(spot, a, b)


def answer(**kw):
    return {"a_seg": seg(1), "b_seg": seg(2), "relation": "same", "text": "同じ言葉ですね。", **kw}


def test_context_shows_the_laugh_and_the_whole_passage_with_measured_pauses():
    _, _, _, rows = shown_lines()
    assert [r for r in rows["lines_a"] if r.get("笑いの山")][0]["seg_id"] == seg(1)
    assert {seg(n) for n in range(4)} <= {r["seg_id"] for r in rows["lines_b"]}
    assert "間" in rows["lines_b"][0] and "start" not in rows["lines_b"][0]


def test_asker_chooses_the_lines_and_they_replace_the_guess():
    a, b, spot, rows = shown_lines()
    spot["kind"] = "kept"
    new = compare.settle(spot, answer(), a, b, rows)
    assert new["a"]["quote"] == LINES[1] and new["b"]["quote"] == LINES[2]
    assert spot["a"] == {"t": 10.0}  # 元の所は書き換えない


def test_lines_that_were_not_shown_are_refused():
    a, b, spot, rows = shown_lines()
    spot["kind"] = "kept"
    assert compare.settle(spot, answer(a_seg=seg(99)), a, b, rows) is None
    assert compare.settle(spot, answer(b_seg=seg(99)), a, b, rows) is None


def test_relation_none_needs_no_line_and_makes_it_added():
    a, b, spot, rows = shown_lines()
    spot["kind"] = "kept"
    assert compare.settle(spot, answer(relation="none"), a, b, rows) is None
    new = compare.settle(spot, answer(relation="none", b_seg=None), a, b, rows)
    assert new["kind"] == "added" and new["b"] is None


def test_no_counterpart_is_called_added_only_when_the_other_has_no_laugh():
    a, b, spot, rows = shown_lines()
    spot.update(kind="one_side", laugh_b=None)
    assert compare.settle(spot, answer(relation="none", b_seg=None), a, b, rows)["kind"] == "added"
    spot.update(kind="moved", laugh_b=30.0)
    assert compare.settle(spot, answer(relation="none", b_seg=None), a, b, rows) is None


def test_speaking_of_a_pause_needs_a_measured_difference():
    a, b, spot, rows = shown_lines()
    spot["kind"] = "kept"
    assert compare.settle(spot, answer(text="ここ、間が違いますね。"), a, b, rows) is None
    b.silences = [[18.5, 20.0]]  # b の 3 行目の前の無音
    new = compare.settle(spot, answer(text="ここ、間が違いますね。"), a, b, rows)
    assert new["kind"] == "ma" and new["ma_b"] == 1.5


@pytest.mark.parametrize(
    "text",
    [
        "「人間」は同じですね。",
        "時間の話ですね。",
        "ここを間違えたのですか？",
        "仲間に教えるなら何て言います？",
    ],
)
def test_words_with_the_same_letter_are_not_a_pause(text):
    a, b, spot, rows = shown_lines()
    spot["kind"] = "kept"
    assert compare.settle(spot, answer(text=text), a, b, rows)["kind"] == "kept"


@pytest.mark.parametrize("text", ["ここの間は何て言います？", "長い間を取っています。", "一呼吸の間ですね。"])
def test_real_talk_of_a_pause_is_seen(text):
    assert compare.speaks_of_pause(text)


def test_pause_is_not_told_when_a_laugh_comes_just_before():
    a = side("a", LINES, [5.0], pauses={0: 0.3})
    b = side("b", LINES, [], pauses={0: 1.5})
    assert compare._ma(a, b, 1, 1) is None


def test_small_pause_difference_is_within_error():
    a = side("a", LINES, [], pauses={0: 0.3})
    b = side("b", LINES, [], pauses={0: 0.9})
    assert compare._ma(a, b, 1, 1) is None


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


def test_marks_and_numbers_inside_a_quote_are_not_checked():
    spot = {"a": {"quote": "第3位に入りました。"}, "b": None}
    assert pipeline.ask_is_ok(
        "本番の「第3位に入りました。」は、稽古には出てきませんでした。その日に足したんですか？", spot
    )


def test_quote_from_the_lines_is_allowed():
    spot = {"a": {"quote": "本物の台詞"}, "b": {"quote": "別の台詞"}}
    assert pipeline.ask_is_ok("「本物の台詞」は 2 本とも同じですね。わざとですか？", spot)


def test_second_run_does_not_ask_the_same_place_twice(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    first = store.read(neta.questions, "questions")["questions"]
    doc = run(root, client_of(answer_all), tmp_path)
    after = store.read(neta.questions, "questions")["questions"]
    assert len(after) == len(first)
    ids = {s["id"] for s in doc["spots"]}
    assert all(q["spot_id"] in ids for q in after if "spot_id" in q)  # 問いが別の所を指さない


def test_failed_asker_keeps_the_cost_and_no_questions(tmp_path):
    root, neta = two_books(tmp_path)

    def broken(kw):
        raise RuntimeError("途中で落ちた")

    with pytest.raises(RuntimeError):
        run(root, client_of(broken), tmp_path)
    assert "compare" in store.read(neta.koza_dir(K1) / "run.json", "run")
    assert not any("spot_id" in q for q in store.read(neta.questions, "questions")["questions"])


def test_a_place_with_an_answer_is_not_asked_again(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    first = next(q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q)
    performer.answer(neta, question=first["id"], speaker="本人", words="わざとです")
    doc = run(root, client_of(answer_all), tmp_path)
    assert any(s["id"] == first["spot_id"] for s in doc["spots"])
    after = store.read(neta.questions, "questions")["questions"]
    assert sum(q.get("spot_id") == first["spot_id"] for q in after) == 1


def test_an_answered_place_keeps_what_the_asker_decided(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    path = neta.koza_dir(K1) / "compare.json"
    first = next(q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q)
    doc = store.read(path, "compare")
    spot = next(s for s in doc["spots"] if s["id"] == first["spot_id"])
    spot["kind"] = "added"  # 前回、聞き役がそう決めた
    store.write(path, doc, "compare")
    performer.answer(neta, question=first["id"], speaker="本人", words="わざとです")
    after = run(root, client_of(REPLY), tmp_path)
    assert next(s for s in after["spots"] if s["id"] == first["spot_id"])["kind"] == "added"


def test_broken_previous_comparison_does_not_waste_the_call(tmp_path):
    root, neta = two_books(tmp_path)
    (neta.koza_dir(K1) / "compare.json").write_text("{", encoding="utf-8")
    assert run(root, client_of(answer_all), tmp_path)["comparable"] is True


def test_failed_comparison_keeps_what_the_answers_point_at(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    first = next(q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q)
    performer.answer(neta, question=first["id"], speaker="本人", words="わざとです")
    apart = {
        "schema_version": 1,
        "makura": NO_MAKURA,
        "passages": [
            {
                "relation": "only_a",
                "a_first": "seg-0001",
                "a_last": "seg-0012",
                "b_first": None,
                "b_last": None,
            }
        ],
    }
    doc = run(root, client_of(aligner=apart), tmp_path)
    assert doc["comparable"] is False and [s["id"] for s in doc["spots"]] == [first["spot_id"]]
    assert sum(doc["counts"].values()) == 1  # 残した所と数が合う


def test_an_answered_place_stays_when_a_rerun_no_longer_finds_it(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    first = next(q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q)
    performer.answer(neta, question=first["id"], speaker="本人", words="わざとです")
    other = {
        "schema_version": 1,
        "makura": NO_MAKURA,
        "passages": [
            {
                "relation": "same",
                "a_first": "seg-0001",
                "a_last": "seg-0002",
                "b_first": "seg-0001",
                "b_last": "seg-0012",
            },
        ],
    }
    doc = run(root, client_of(REPLY, aligner=other), tmp_path)
    assert doc["comparable"] is True and first["spot_id"] in {s["id"] for s in doc["spots"]}


def test_answered_places_are_not_shown_to_the_asker_again(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    first = next(q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q)
    performer.answer(neta, question=first["id"], speaker="本人", words="わざとです")
    client = client_of(REPLY)
    run(root, client, tmp_path)
    assert all(
        first["spot_id"] != r["spot_id"] for c in client.calls["asker"] for r in json.loads(text_of(c))
    )


def test_open_questions_of_another_pair_stay(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    rv = store.read(neta.review, "review")
    qs = store.read(neta.questions, "questions")
    for issue in rv["issues"]:
        if issue["role"] == "asker":
            issue["attempt"] = issue["attempt"].replace(K2, "別の高座")
    store.write(neta.review, rv, "review")
    pipeline.drop_open_asks(rv, qs, f"（{K1} と {K2}、")
    assert any("spot_id" in q for q in qs["questions"])
    pipeline.drop_open_asks(rv, qs, f"（{K1} と 別の高座、")
    assert not any("spot_id" in q for q in qs["questions"])


def test_a_refused_question_does_not_change_the_place(tmp_path):
    root, neta = two_books(tmp_path)

    def bad(kw):
        rows = json.loads(text_of(kw))
        return json.dumps(
            {
                "schema_version": 1,
                "questions": [
                    {
                        "spot_id": r["spot_id"],
                        "a_seg": r["lines_a"][0]["seg_id"],
                        "b_seg": r["lines_b"][0]["seg_id"],
                        "relation": "changed",
                        "text": "ここは受けなかったですね。",
                    }
                    for r in rows
                ],
            },
            ensure_ascii=False,
        )

    plain_doc = run(root, client_of(REPLY), tmp_path)
    doc = run(root, client_of(bad), tmp_path)
    assert store.read(neta.koza_dir(K1) / "run.json", "run")["compare"]["fallback"] >= 1  # 拒否が起きた
    assert doc["spots"] == plain_doc["spots"] and doc["counts"] == plain_doc["counts"]
    assert not any("spot_id" in q for q in store.read(neta.questions, "questions")["questions"])


def test_failed_aligner_records_no_share(tmp_path):
    root, neta = two_books(tmp_path)

    def broken(kw):
        raise RuntimeError("途中で落ちた")

    with pytest.raises(RuntimeError):
        run(root, ScriptedClient(aligner=[broken], asker=[]), tmp_path)
    assert store.read(neta.koza_dir(K1) / "run.json", "run")["compare"]["share"] is None


def test_second_run_rewrites_open_questions_and_keeps_touched_ones(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    first = [q for q in store.read(neta.questions, "questions")["questions"] if "spot_id" in q]
    performer.answer(neta, question=first[0]["id"], speaker="本人", words="わざとです")
    doc = store.read(neta.questions, "questions")["questions"]
    before = next(q for q in doc if q["id"] == first[0]["id"])
    run(root, client_of(answer_all), tmp_path)
    after = store.read(neta.questions, "questions")["questions"]
    assert before in after  # 答えた問いは残る


def test_answers_and_books_are_not_overwritten(tmp_path):
    root, neta = two_books(tmp_path)
    kata, hanashi = neta.kata.read_bytes(), neta.hanashi.read_bytes()
    answered = [q for q in store.read(neta.questions, "questions")["questions"] if "answer" in q]
    run(root, client_of(REPLY), tmp_path)
    assert neta.kata.read_bytes() == kata and neta.hanashi.read_bytes() == hanashi
    after = store.read(neta.questions, "questions")["questions"]
    assert all(q in after for q in answered)


def test_second_koza_can_live_in_another_folder(tmp_path):
    root, neta = two_books(tmp_path, other_neta="雨宿り勘定-2")
    doc = run(root, client_of(REPLY), tmp_path, other_neta="雨宿り勘定-2")
    assert doc["other_neta"] == "雨宿り勘定-2"
    assert view.compare_view(neta, neta.koza_dir(K1))["other_reactions"] is not None


def test_clean_copy_is_required(tmp_path):
    root, neta = two_books(tmp_path)
    (neta.koza_dir(K2) / "seisho.json").unlink()
    with pytest.raises(store.StoreError, match="清書"):
        run(root, client_of(REPLY), tmp_path)


def test_stories_that_do_not_correspond_ask_nothing(tmp_path):
    root, neta = two_books(tmp_path)
    apart = {
        "schema_version": 1,
        "makura": NO_MAKURA,
        "passages": [
            {
                "relation": "only_a",
                "a_first": "seg-0001",
                "a_last": "seg-0012",
                "b_first": None,
                "b_last": None,
            },
            {
                "relation": "only_b",
                "a_first": None,
                "a_last": None,
                "b_first": "seg-0001",
                "b_last": "seg-0012",
            },
        ],
    }
    client = client_of(aligner=apart)
    doc = run(root, client, tmp_path)
    assert doc["comparable"] is False and client.calls["asker"] == []
    assert not any("spot_id" in q for q in store.read(neta.questions, "questions")["questions"])


def test_failure_writing_the_comparison_leaves_no_question(tmp_path, monkeypatch):
    root, neta = two_books(tmp_path)
    before = neta.questions.read_bytes()
    real = store.write

    def stop(path, obj, schema):
        if schema == "compare":
            raise OSError("書けない")
        real(path, obj, schema)

    monkeypatch.setattr(store, "write", stop)
    with pytest.raises(OSError):
        run(root, client_of(REPLY), tmp_path)
    assert neta.questions.read_bytes() == before


def test_old_clean_copy_stops_the_comparison(tmp_path):
    root, neta = two_books(tmp_path)
    path = neta.koza_dir(K2) / "seisho.json"
    doc = store.read(path, "seisho")
    for row in doc["lines"]:
        row["raw"] = "作り直す前の文字起こし"
    store.write(path, doc, "seisho")
    with pytest.raises(store.StoreError, match="古い"):
        run(root, client_of(REPLY), tmp_path)


def test_practice_as_the_first_recording_stops_with_a_hint(tmp_path):
    root, neta = two_books(tmp_path)
    doc = store.read(neta.koza_dir(K1) / "reactions.json", "reactions")
    doc["scores"]["laugh"] = [0.0] * len(doc["scores"]["laugh"])
    store.write(neta.koza_dir(K1) / "reactions.json", doc, "reactions")
    other = store.read(neta.koza_dir(K2) / "reactions.json", "reactions")
    other["scores"]["laugh"] = [0.2 if n % 4 == 0 else 0.0 for n in range(len(other["scores"]["laugh"]))]
    store.write(neta.koza_dir(K2) / "reactions.json", other, "reactions")
    with pytest.raises(store.StoreError, match="本番"):
        run(root, client_of(REPLY), tmp_path)


def test_failed_comparison_drops_open_questions_from_before(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(answer_all), tmp_path)
    apart = {
        "schema_version": 1,
        "makura": NO_MAKURA,
        "passages": [
            {
                "relation": "only_a",
                "a_first": "seg-0001",
                "a_last": "seg-0012",
                "b_first": None,
                "b_last": None,
            }
        ],
    }
    run(root, client_of(aligner=apart), tmp_path)
    assert not any("spot_id" in q for q in store.read(neta.questions, "questions")["questions"])


def test_compare_is_written_for_the_screen(tmp_path):
    root, neta = two_books(tmp_path)
    run(root, client_of(REPLY), tmp_path)
    assert view.payload(neta, K1)["compare"]["koza_b"] == K2


def test_screen_without_a_comparison_is_fine(tmp_path):
    root, neta = two_books(tmp_path)
    assert view.payload(neta, K1)["compare"] is None


def test_makura_keeps_its_slot_after_the_tale_questions():
    """聞き役が枕を後ろに並べても、噺の問いが枠を埋めない。"""
    kinds = {f"t{n}": {"kind": "words"} for n in range(6)} | {
        "m1": {"kind": "makura"},
        "m2": {"kind": "makura"},
    }
    picks = [{"spot_id": x} for x in ("t0", "t1", "t2", "t3", "t4", "t5", "m1", "m2")]
    order = [r["spot_id"] for r in pipeline.keep_makura_slot(picks, kinds)]
    room = pipeline.COMPARE_QUESTIONS - pipeline.COMPARE_MAKURA
    assert order[: room + 1] == ["t0", "t1", "t2", "t3", "m1"]
    assert order[room + 1 :] == ["m2", "t4", "t5"]  # 枕が落ちれば、次の枕、残りの噺の順に埋まる


def test_makura_slot_keeps_the_order_without_makura():
    kinds = {f"t{n}": {"kind": "kept"} for n in range(3)}
    picks = [{"spot_id": x} for x in ("t0", "t1", "t2", "unknown")]
    assert pipeline.keep_makura_slot(picks, kinds) == picks  # 無い所は後で数えて落とす


MAKURA_AND_TALE = {
    "schema_version": 1,
    "makura": {
        "a_last": "seg-0004",
        "b_last": "seg-0004",
        "topics": [
            {
                "topic": "近況",
                "prepares": False,
                "a_first": "seg-0001",
                "a_last": "seg-0004",
                "b_first": "seg-0001",
                "b_last": "seg-0004",
            }
        ],
    },
    "passages": [dict(ALIGN["passages"][0], a_first="seg-0005", b_first="seg-0005")],
}


def makura_first(bad_makura=False):
    """聞き役の返事の代わり。枕を先に並べる。"""

    def reply(kw):
        rows = sorted(json.loads(text_of(kw)), key=lambda r: r["kind"] != "makura")
        questions = []
        for r in rows:
            makura = r["kind"] == "makura"
            text = "近況の話で受けていました。なんでこの話題にしたんですか？"
            questions.append(
                {
                    "spot_id": r["spot_id"],
                    "a_seg": r["lines_a"][-1]["seg_id"],
                    "b_seg": None if makura else r["lines_b"][-1]["seg_id"],
                    "relation": "none" if makura else "same",
                    # 客の字を入れて、枕の問いだけ確かめで落とす
                    "text": ("客の話です。" + text) if makura and bad_makura else text,
                }
            )
        return json.dumps({"schema_version": 1, "questions": questions}, ensure_ascii=False)

    return reply


def with_makura_laugh(tmp_path):
    root, neta = two_books(tmp_path)
    for kdir in (neta.koza_dir(K1), neta.koza_dir(K2)):
        doc = store.read(kdir / "reactions.json", "reactions")
        doc["scores"]["laugh"][8] = 0.0
        doc["scores"]["laugh"][1] = 0.2  # 枕の笑い
        doc["scores"]["laugh"][28] = 0.2  # 噺の笑い
        store.write(kdir / "reactions.json", doc, "reactions")
    return root, neta


def asked_kinds(neta):
    spots = {s["id"]: s for s in store.read(neta.koza_dir(K1) / "compare.json", "compare")["spots"]}
    qs = store.read(neta.questions, "questions")["questions"]
    return [spots[q["spot_id"]]["kind"] for q in qs if "spot_id" in q]


def test_makura_question_comes_last_even_if_the_role_puts_it_first(tmp_path):
    root, neta = with_makura_laugh(tmp_path)
    run(root, client_of(makura_first(), aligner=MAKURA_AND_TALE), tmp_path)
    kinds = asked_kinds(neta)
    assert kinds[-1] == "makura" and kinds.count("makura") == 1 and len(kinds) >= 2


def test_makura_dropped_by_the_check_leaves_only_the_tale(tmp_path):
    root, neta = with_makura_laugh(tmp_path)
    run(root, client_of(makura_first(bad_makura=True), aligner=MAKURA_AND_TALE), tmp_path)
    kinds = asked_kinds(neta)
    assert kinds and "makura" not in kinds


def test_answered_makura_gives_its_slot_to_the_tale(tmp_path):
    root, neta = with_makura_laugh(tmp_path)
    run(root, client_of(makura_first(), aligner=MAKURA_AND_TALE), tmp_path)
    qs = store.read(neta.questions, "questions")["questions"]
    performer.answer(neta, question=qs[-1]["id"], speaker="本人", words="今日の会に合わせました")
    run(root, client_of(makura_first(), aligner=MAKURA_AND_TALE), tmp_path)
    kinds = asked_kinds(neta)
    assert kinds.count("makura") == 1 and len(kinds) >= 2  # 答えた枕は残り、増やさない
