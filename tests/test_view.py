"""視聴画面の書き出しのテスト。ダミーの一式を使う。"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from netacho import cli, gaps, store, view

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "fixtures" / "dummy_koza"
P, N, K = "performer-dummy", "雨宿り勘定", "2026-01-01-dummy-hall"
HTML = (ROOT / "viewer" / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def doc():
    return view.payload(store.Neta(P, N, DATA), K)


def test_payload_has_everything_for_four_screens(doc):
    for key in ("koza", "reactions", "candidates", "hanashi", "kata", "guide", "review", "questions", "run"):
        assert doc[key], key
    assert doc["reactions"]["scores"]["laugh"]
    assert doc["candidates"]["candidates"][0]["note"]


def test_items_know_their_scene(doc):
    by_id = {i["id"]: i for i in doc["hanashi"] + doc["kata"]}
    assert by_id["item-0001"]["scene_id"] == "scene-02"
    assert by_id["item-0009"]["scene_id"] == "scene-01"  # 演者の項目は anchor から


def test_write_goes_to_out_dir(tmp_path):
    path = view.write(store.Neta(P, N, DATA), K, tmp_path / "view")
    assert json.loads(path.read_text(encoding="utf-8"))["koza"]["id"] == K
    assert sorted(p.name for p in path.parent.iterdir()) == ["index.html", "view.json"]  # 録音は出さない


def _two_koza(tmp_path):
    """二席目に出所のある項目を持つ、ダミーの置き場。"""
    import shutil

    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    neta = store.Neta(P, N, root)
    k2 = "2026-03-03-dummy-hall"
    shutil.copytree(neta.koza_dir(K), neta.koza_dir(k2))
    tl = store.read(neta.koza_dir(k2) / "timeline.json", "timeline")
    tl["koza_id"] = k2
    store.write(neta.koza_dir(k2) / "timeline.json", tl, "timeline")
    hanashi = store.read(neta.hanashi, "book")
    hanashi["items"][0]["source"]["koza_id"] = k2
    store.write(neta.hanashi, hanashi, "book")
    return neta, k2


def test_items_of_another_koza_find_their_scene(tmp_path):
    neta, _ = _two_koza(tmp_path)
    doc = view.payload(neta, K)
    assert next(i for i in doc["hanashi"] if i["id"] == "item-0001")["scene_id"] == "scene-02"


def test_held_items_reach_the_screen(doc):
    assert any(i.get("held") for i in doc["kata"])


def test_broken_other_koza_does_not_stop_the_view(tmp_path):
    neta, k2 = _two_koza(tmp_path)
    for broken in ("{壊れた", '{"schema_version": 1}'):
        (neta.koza_dir(k2) / "timeline.json").write_text(broken, encoding="utf-8")
        assert view.payload(neta, K)["koza"]["id"] == K


def test_failed_write_removes_old_view(tmp_path):
    out = tmp_path / "view"
    view.write(store.Neta(P, N, DATA), K, out)
    with pytest.raises(FileNotFoundError):
        view.write(store.Neta(P, "無い噺", DATA), K, out)
    assert not (out / "view.json").exists()


def test_only_out_view_is_served():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "-d out/view" in make and "-d ." not in make


def test_missing_later_files_are_ok(tmp_path):
    src = DATA / P / N
    neta = store.Neta(P, N, tmp_path)
    for name in ("hanashi.json", "kata.json"):
        store.write(neta.base / name, store.read(src / name, "book"), "book")
    kdir = neta.koza_dir(K)
    for name, schema in (("koza.json", "koza"), ("reactions.json", "reactions")):
        store.write(kdir / name, store.read(src / "koza" / K / name, schema), schema)
    doc = view.payload(neta, K)
    assert doc["guide"] is None and doc["review"] is None and doc["candidates"] is None


def test_cli_view_needs_no_anthropic(tmp_path):
    args = ["view", "--performer", P, "--neta", N, "--koza", K, "--out", str(tmp_path)]
    code = f"import sys\nsys.modules['anthropic'] = None\nfrom netacho import cli\ncli.main({args!r})\n"
    env = {**os.environ, "NETACHO_DATA": str(DATA)}
    subprocess.run([sys.executable, "-c", code], check=True, env=env, capture_output=True)
    assert (tmp_path / "view.json").exists()


def test_view_before_build_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setenv("NETACHO_DATA", str(tmp_path))
    with pytest.raises(SystemExit, match="netacho prep"):
        cli.main(["view", "--performer", P, "--neta", N, "--koza", K, "--out", str(tmp_path / "o")])


def test_makefile_binds_to_localhost_only():
    assert "--bind 127.0.0.1" in (ROOT / "Makefile").read_text(encoding="utf-8")


def test_cli_parser_knows_view():
    args = cli.build_parser().parse_args(["view", "--performer", "p", "--neta", "n", "--koza", "k"])
    assert args.func is cli.cmd_view


def test_html_has_four_screens_and_three_labels():
    for word in ("高座の波", "帳面", "札", "弟子の問い"):
        assert word in HTML
    for word in ("Claude の候補", "演者の決定", "スクリプトの転記"):
        assert word in HTML
    assert "手で付けた" in HTML


def test_html_shows_only_the_clean_copy():
    """清書のある所に、原文と文字起こしを並べない。"""
    assert "quote_raw" not in HTML and "l.raw" not in HTML and "l.proposal" not in HTML


def test_html_keeps_tab_ids_for_the_screen_recording():
    """動画の撮影はタブの id と「答える」のボタンを押す。"""
    for key in ("deshi", "wave", "compare", "kata", "cards", "seisho"):
        assert f'<section id="{key}"' in HTML and f'["{key}", ' in HTML
    assert '"答える"' in HTML


def test_html_plays_only_items_of_this_koza():
    """型と噺は演目全体の物。別の高座の時刻で、この高座の録音を鳴らさない。"""
    assert "i?.source?.koza_id === KOZA" in HTML
    assert "here(i) ?" in HTML and "here(item) ?" in HTML


def test_html_asks_who_writes_before_saving():
    """代筆が「本人」で残らないように、書く人に既定を置かない。"""
    assert 'localStorage.getItem("netacho-speaker") || "";' in HTML
    assert "\"speaker\" in body && !body.speaker" in HTML


def test_html_resets_the_player_when_the_clip_ends():
    assert "audio.onended = stop" in HTML


def test_laugh_lines_join_clean_lines_before_the_laugh():
    transcript = {
        "segments": [
            {"id": "seg-0001", "start": 1.0, "end": 2.0, "text": "文字起こしの前"},
            {"id": "seg-0002", "start": 3.0, "end": 4.0, "text": "文字起こしの落ち"},
            {"id": "seg-0003", "start": 5.0, "end": 6.0, "text": "文字起こしの後"},
        ]
    }
    reactions = {
        "reactions": [
            {"id": "r-1", "t0": 4.5, "kind": "laugh"},
            {"id": "r-2", "t0": 4.5, "kind": "applause"},
            {"id": "r-3", "t0": 0.5, "kind": "laugh"},
        ]
    }
    lines = {
        "seg-0001": {"text": "清書の仕込み", "speaker": "女"},
        "seg-0002": {"text": "清書の落ち", "speaker": "男"},
    }
    assert view.laugh_lines(reactions, transcript, lines) == {
        "r-1": {"text": "清書の仕込み　清書の落ち", "speaker": "男"}
    }


def test_laugh_lines_never_show_the_raw_transcript():
    """清書の無い高座、清書の無い区間の文字起こしを、受けた所の一覧に出さない。"""
    transcript = {"segments": [{"id": "seg-0001", "start": 1.0, "end": 2.0, "text": "文字起こし"}]}
    reactions = {"reactions": [{"id": "r-1", "t0": 3.0, "kind": "laugh"}]}
    assert view.laugh_lines(reactions, transcript, {}) == {}
    assert view.laugh_lines(reactions, None, {"seg-0001": {"text": "清書"}}) == {}
    other = {"seg-0009": {"text": "別の区間の清書"}}
    assert view.laugh_lines(reactions, transcript, other) == {}


def test_laugh_lines_skip_a_short_backchannel():
    transcript = {
        "segments": [
            {"id": "seg-0001", "start": 1.0, "end": 2.0, "text": "仕込み"},
            {"id": "seg-0002", "start": 3.0, "end": 4.0, "text": "ん"},
        ]
    }
    reactions = {"reactions": [{"id": "r-1", "t0": 4.5, "kind": "laugh"}]}
    lines = {"seg-0001": {"text": "仕込みの台詞です"}, "seg-0002": {"text": "ん"}}
    assert view.laugh_lines(reactions, transcript, lines)["r-1"]["text"] == "仕込みの台詞です"


def _second_koza(root, neta="別の会", koza="2026-02-03-other-hall", when="2026-02-03"):
    """同じ演者の別の高座を、ダミーから作る。"""
    src = root / P / N
    dst = root / P / neta
    shutil.copytree(src, dst)
    (dst / "koza" / K).rename(dst / "koza" / koza)
    path = dst / "koza" / koza / "koza.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**meta, "id": koza, "date": when, "neta": neta}), encoding="utf-8")
    return store.Neta(P, neta, root)


def test_koza_list_is_newest_first_and_skips_broken(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    _second_koza(root)
    broken = root / P / "作りかけ" / "koza" / "2026-03-01-x"
    broken.mkdir(parents=True)
    (broken / "koza.json").write_text("{}", encoding="utf-8")
    rows = view.koza_list(store.Neta(P, N, root))
    assert [r["koza_id"] for r in rows] == ["2026-02-03-other-hall", K]
    assert rows[0]["neta"] == "別の会" and rows[0]["seconds"]


def test_payload_lists_the_kozas(doc):
    assert doc["neta"] == N
    assert [r["koza_id"] for r in doc["kozas"]] == [K]


def test_scene_times_come_from_this_koza(doc):
    timed = [s for s in doc["scenes"] if "t0" in s]
    assert timed
    assert all(s["t0"] < s["t1"] for s in timed)


def test_scene_times_leave_scenes_of_other_koza_alone():
    scenes = [{"id": "scene-01"}, {"id": "scene-02"}]
    transcript = {
        "segments": [
            {"id": "seg-0001", "start": 1.0, "end": 2.0},
            {"id": "seg-0002", "start": 2.0, "end": 5.0},
        ]
    }
    timeline = {
        "segments": [
            {"seg_id": "seg-0001", "scene_id": "scene-01"},
            {"seg_id": "seg-0002", "scene_id": "scene-01"},
        ]
    }
    assert view.scene_times(scenes, transcript, timeline) == [
        {"id": "scene-01", "t0": 1.0, "t1": 5.0},
        {"id": "scene-02"},
    ]
    assert view.scene_times(scenes, None, timeline) == scenes
    assert view.scene_times(scenes, transcript, None) == scenes
    assert view.scene_times(scenes, {"segments": []}, timeline) == scenes


def test_html_does_not_say_no_reaction():
    plain = HTML.replace("反応が無かったという意味ではありません", "")
    assert "受けなかった" not in plain and "反応なし" not in plain and "反応が無かった" not in plain


def test_html_never_builds_markup_from_data():
    """演者の言葉は textContent で入れる（文字列を HTML として解釈しない）。"""
    assert "innerHTML" not in HTML and not re.search(r"insertAdjacentHTML|document\.write", HTML)


def test_html_loads_nothing_from_outside():
    assert not re.search(r"(src|href)=\"https?://", HTML)


GAPS = {"koza_id": "k", "surprises": [{"t0": 100.0}]}
QUESTIONS = {
    "questions": [
        {"id": "q-0001", "text": "1:40.0 の笑いの所（k）「…」：問い"},
        {"id": "q-0002", "text": "別"},
    ]
}
TRANSCRIPT = {
    "segments": [
        {"id": "seg-0001", "start": 50.0, "end": 60.0, "text": "遠い"},
        {"id": "seg-0002", "start": 90.0, "end": 99.0, "text": " 生の台詞 "},
        {"id": "seg-0003", "start": 99.0, "end": 102.0, "text": "次の台詞"},
        {"id": "seg-0004", "start": 104.0, "end": 110.0, "text": "後の台詞"},
    ]
}


def test_place_uses_the_clean_copy_when_it_exists():
    clean = {
        "lines": [
            {"seg_id": "seg-0002", "speaker": "親方", "text": "清書の台詞", "raw": "生の台詞"},
            {"seg_id": "seg-0003", "speaker": None, "text": "次の清書", "raw": "次の台詞"},
        ]
    }
    place = view.question_places(GAPS, QUESTIONS, TRANSCRIPT, clean)["q-0001"]
    assert place["t0"] == 100.0 and place["clean"] is True
    assert place["lines"] == [
        {"speaker": "親方", "text": "清書の台詞"},
        {"speaker": None, "text": "次の清書"},
    ]


def test_a_missing_line_is_not_called_clean():
    clean = {"lines": [{"seg_id": "seg-0002", "speaker": "親方", "text": "清書", "raw": "生の台詞"}]}
    place = view.question_places(GAPS, QUESTIONS, TRANSCRIPT, clean)["q-0001"]
    assert place["clean"] is False and place["lines"][1] == {"speaker": None, "text": "次の台詞"}


def test_old_clean_copy_is_not_shown_for_a_changed_line():
    clean = {"lines": [{"seg_id": "seg-0002", "speaker": "親方", "text": "古い清書", "raw": "前の文字"}]}
    place = view.question_places(GAPS, QUESTIONS, TRANSCRIPT, clean)["q-0001"]
    assert place["lines"][0] == {"speaker": None, "text": "生の台詞"}


def test_place_falls_back_to_the_transcript_and_says_so():
    place = view.question_places(GAPS, QUESTIONS, TRANSCRIPT, None)["q-0001"]
    assert place["clean"] is False and place["lines"][0]["text"] == "生の台詞"


def test_only_laugh_questions_have_a_place():
    assert list(view.question_places(GAPS, QUESTIONS, TRANSCRIPT, None)) == ["q-0001"]
    assert view.question_places(None, QUESTIONS, TRANSCRIPT, None) == {}


def test_laugh_without_lines_is_not_called_clean():
    empty = {"segments": [{"id": "seg-0001", "start": 0.0, "end": 10.0, "text": "遠い"}]}
    place = view.question_places(GAPS, QUESTIONS, empty, None)["q-0001"]
    assert place["lines"] == [] and place["clean"] is False


CLEAN = {
    "lines": [
        {"seg_id": "seg-0002", "speaker": "親方", "text": "清書の台詞", "raw": "生の台詞"},
        {"seg_id": "seg-0003", "speaker": None, "text": "次の清書", "raw": "次の台詞"},
    ]
}


def item_at(first, last, koza="k"):
    return {
        "id": "item-0001",
        "source": {"koza_id": koza, "segs": [first, last], "quote": "元の引用"},
        "payoff": {"segs": [last, last], "quote": "元の回収"},
    }


def test_quote_becomes_the_clean_copy_and_keeps_the_original():
    lines = view.clean_lines(TRANSCRIPT, CLEAN)
    [row] = view.clean_items([item_at("seg-0002", "seg-0003")], "k", TRANSCRIPT, lines)
    assert row["source"]["quote"] == "清書の台詞次の清書" and row["source"]["quote_raw"] == "元の引用"
    assert row["payoff"]["quote"] == "次の清書" and row["payoff"]["quote_raw"] == "元の回収"


def test_quote_stays_when_a_line_has_no_clean_copy():
    lines = view.clean_lines(TRANSCRIPT, CLEAN)
    [row] = view.clean_items([item_at("seg-0003", "seg-0004")], "k", TRANSCRIPT, lines)
    assert row["source"]["quote"] == "元の引用" and "quote_raw" not in row["source"]


def test_old_clean_copy_is_not_used_for_quotes():
    old = {"lines": [{"seg_id": "seg-0002", "speaker": None, "text": "古い", "raw": "前の文字"}]}
    assert view.clean_lines(TRANSCRIPT, old) == {}


def test_items_of_another_koza_are_left_alone():
    lines = view.clean_lines(TRANSCRIPT, CLEAN)
    item = item_at("seg-0002", "seg-0003", koza="別の高座")
    assert view.clean_items([item], "k", TRANSCRIPT, lines) == [item]


def test_performer_item_without_segs_is_left_alone():
    item = {"id": "item-0002", "source": {"koza_id": "k", "words": "演者の言葉"}}
    assert view.clean_items([item], "k", TRANSCRIPT, view.clean_lines(TRANSCRIPT, CLEAN)) == [item]


def test_readable_question_is_kept_and_a_misheard_one_is_replaced():
    good = "「清書の台詞」の後は、文字からは読めませんでした"
    assert view.readable_question(good, "前の清書の台詞") == good
    assert view.readable_question("「聞き違い」の後は", "前の清書の台詞") == gaps.FALLBACK_QUESTION
    assert view.readable_question("引用の無い文", "前") == "引用の無い文"
    assert view.readable_question(None, "前") == gaps.FALLBACK_QUESTION


LAUGH_GAPS = {
    "koza_id": "k",
    "surprises": [{"t0": 100.0, "question": "「清書の台詞」の後は", "before_quote": "生"}],
}


def test_surprise_uses_clean_lines_before_the_laugh():
    [row] = view.clean_gaps(LAUGH_GAPS, TRANSCRIPT, view.clean_lines(TRANSCRIPT, CLEAN))["surprises"]
    assert row["before_quote"] == "清書の台詞次の清書" and row["question"] == "「清書の台詞」の後は"


def test_questions_show_the_clean_text_and_keep_the_answer():
    lines = view.clean_lines(TRANSCRIPT, CLEAN)
    cleaned = view.clean_gaps(LAUGH_GAPS, TRANSCRIPT, lines)
    doc = {
        "questions": [{**QUESTIONS["questions"][0], "answer": {"words": "言葉"}}, QUESTIONS["questions"][1]]
    }
    out = view.clean_questions(doc, cleaned)["questions"]
    assert out[0]["text"] == "1:40.0 の笑いの所：「清書の台詞」の後は"
    assert out[0]["text_raw"].startswith("1:40.0")
    assert out[0]["answer"] == {"words": "言葉"} and out[1]["text"] == "別"


def test_only_issues_of_laugh_questions_are_reworded():
    questions = {"questions": [{"id": "q-1", "issue_id": "issue-1", "text": "生"}]}
    shown = {"questions": [{"id": "q-1", "issue_id": "issue-1", "text": "読める問い", "text_raw": "生"}]}
    doc = {
        "issues": [
            {"id": "issue-1", "role": "noter", "what_is_missing": "生"},
            {"id": "issue-2", "role": "noter", "what_is_missing": "別のノート役の指摘"},
            {"id": "issue-3", "role": "deshi", "what_is_missing": "弟子の言葉"},
        ]
    }
    out = view.clean_review(doc, questions, shown)["issues"]
    assert [i["what_is_missing"] for i in out] == ["読める問い", "別のノート役の指摘", "弟子の言葉"]


def test_held_quote_stays_as_transcribed():
    item = {**item_at("seg-0002", "seg-0003"), "held": True}
    assert view.clean_items([item], "k", TRANSCRIPT, view.clean_lines(TRANSCRIPT, CLEAN)) == [item]


def test_without_a_clean_copy_nothing_changes():
    assert view.clean_gaps(GAPS, TRANSCRIPT, {}) is GAPS
    assert view.clean_questions(QUESTIONS, None) is QUESTIONS
    item = item_at("seg-0002", "seg-0003")
    assert view.clean_items([item], "k", TRANSCRIPT, {}) == [item]


def test_screen_data_does_not_change_the_saved_books(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    neta = store.Neta(P, N, root)
    before = (
        neta.hanashi.read_bytes(),
        neta.kata.read_bytes(),
        neta.review.read_bytes(),
        neta.questions.read_bytes(),
    )
    view.payload(neta, K)
    assert before == (
        neta.hanashi.read_bytes(),
        neta.kata.read_bytes(),
        neta.review.read_bytes(),
        neta.questions.read_bytes(),
    )


LINES = {
    "seg-0001": {"raw": "池に2匹、逃がしてしまうという話。", "text": "池に二匹、逃がしてしまうという話。"},
    "seg-0002": {"raw": "物星に屋根つけた", "text": "物干しに屋根付けた"},
}


def test_clean_quotes_shows_part_of_a_line_in_the_clean_copy():
    note = "「池に2匹、逃がしてしまう」の後は、反応候補が小さい方です。"
    assert view.clean_quotes(note, LINES) == "「池に二匹、逃がしてしまう」の後は、反応候補が小さい方です。"


def test_clean_quotes_shows_a_whole_line_in_the_clean_copy():
    assert view.clean_quotes("「物星に屋根つけた」の後", LINES) == "「物干しに屋根付けた」の後"


def test_clean_quotes_leaves_a_quote_found_in_lines_with_different_clean_text():
    lines = {
        "seg-0001": {"raw": "ええ行こか", "text": "ええ、行こか"},
        "seg-0002": {"raw": "ええわ帰ろ", "text": "いや、帰ろ"},
    }
    assert view.clean_quotes("「ええ」の後", lines) == "「ええ」の後"


def test_clean_quotes_uses_the_clean_text_when_every_line_agrees():
    lines = {
        "seg-0001": {"raw": "物星に屋根", "text": "物干しに屋根"},
        "seg-0002": {"raw": "また物星に屋根", "text": "また物干しに屋根"},
    }
    assert view.clean_quotes("「物星に屋根」の後", lines) == "「物干しに屋根」の後"


def test_clean_quotes_does_not_leave_empty_brackets():
    lines = {"seg-0001": {"raw": "あのーええ話や", "text": "ええ話や"}}
    assert view.clean_quotes("「あのー」の後", lines) == "「あのー」の後"


def test_clean_quotes_keeps_a_quote_gone_from_only_one_of_its_lines():
    lines = {
        "seg-0001": {"raw": "あのーええ話や", "text": "ええ話や"},
        "seg-0002": {"raw": "あのー傘の話", "text": "あのー傘の話"},
    }
    assert view.clean_quotes("「あのー」の後", lines) == "「あのー」の後"


def test_clean_quotes_does_not_leave_blank_brackets():
    lines = {"seg-0001": {"raw": "あのーええ話や", "text": "　ええ話や"}}
    assert view.clean_quotes("「あのー」の後", lines) == "「あのー」の後"


def test_clean_quotes_leaves_what_it_cannot_find():
    assert view.clean_quotes("「どこにも無い台詞」の後", LINES) == "「どこにも無い台詞」の後"
    assert view.clean_quotes("引用の無い文", LINES) == "引用の無い文"


def test_clean_notes_keeps_notes_without_text_and_without_clean_copy():
    doc = {"candidates": [{"id": "c-1"}, {"id": "c-2", "note": "「物星に屋根つけた」の後"}]}
    shown = view.clean_notes(doc, LINES)
    assert shown["candidates"][0] == {"id": "c-1"}
    assert shown["candidates"][1]["note"] == "「物干しに屋根付けた」の後"
    assert doc["candidates"][1]["note"] == "「物星に屋根つけた」の後"  # 正本は変えない
    assert view.clean_notes(doc, {}) is doc


def test_book_items_do_not_touch_the_words_of_the_performer():
    book = {
        "items": [
            {"id": "i-1", "text": "「物星に屋根つけた」が受ける", "edited_by": "tool", "source": {}},
            {"id": "i-2", "text": "「物星に屋根つけた」は演者の字", "edited_by": "performer", "source": {}},
        ]
    }
    rows = view.book_items(book, {}, K, {"segments": []}, LINES)
    assert rows[0]["text"] == "「物干しに屋根付けた」が受ける"
    assert rows[1]["text"] == "「物星に屋根つけた」は演者の字"


def test_koza_list_keeps_a_koza_whose_run_json_is_old(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    (root / P / N / "koza" / K / "run.json").write_text('{"old": true}', encoding="utf-8")
    rows = view.koza_list(store.Neta(P, N, root))
    assert [(r["koza_id"], r["title"]) for r in rows] == [(K, N)]


def test_compares_from_others_finds_the_pair_kept_on_the_other_side(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    other = _second_koza(root)
    koza = "2026-02-03-other-hall"
    path = other.koza_dir(koza) / "compare.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**doc, "koza_a": koza, "koza_b": K, "other_neta": N}), encoding="utf-8")
    rows = view.compares_from_others(store.Neta(P, N, root), K)
    assert [(r["neta_a"], r["koza_a"], r["neta_b"], r["koza_b"]) for r in rows] == [("別の会", koza, N, K)]
    assert rows[0]["other_reactions"]["reactions"]
    assert view.compares_from_others(other, koza) == []  # 自分の側の比較は compare に入る


def test_html_keeps_the_open_koza_on_the_left_of_a_compare():
    assert "[mineKey(c), otherKey(c)]" in HTML
    assert "shownPair = pair; renderCompare(d);" in HTML


def test_html_names_the_koza_of_every_clip():
    assert "&neta=${encodeURIComponent(src.neta)}&koza=${encodeURIComponent(src.koza)}" in HTML


@pytest.mark.parametrize(
    ("name", "text"),
    [("run.json", '{"seisho": "古い形"}'), ("reactions.json", "[]")],
)
def test_koza_list_survives_an_odd_shape(tmp_path, name, text):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    (root / P / N / "koza" / K / name).write_text(text, encoding="utf-8")
    view.koza_list(store.Neta(P, N, root))


def test_html_shows_one_row_for_laughs_after_the_same_line():
    assert "if (seen.has(line)) return false;" in HTML
    assert "const big = (scenes.length ? laughs.filter(" in HTML


def test_recheck_is_shown_in_the_clean_words():
    side = {"verdict": "written", "quote": "物星に屋根つけた", "reply": "「物星に屋根つけた」と書いてある"}
    rows = view.clean_recheck({"rows": [{"question_id": "q-1", "before": None, "after": side}]}, LINES)
    assert rows["rows"][0]["before"] is None
    assert rows["rows"][0]["after"]["quote"] == "物干しに屋根付けた"
    assert rows["rows"][0]["after"]["reply"] == "「物干しに屋根付けた」と書いてある"
    assert view.clean_recheck(None, LINES) is None


def test_html_shows_the_recheck_on_the_answered_question():
    assert "if (check) body.push(...recheckBody(check));" in HTML
    assert "tag(" not in HTML

