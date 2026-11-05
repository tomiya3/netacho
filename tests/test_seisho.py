"""清書のテスト。"""

import json
from pathlib import Path

import pytest
from fakes import ScriptedClient, text_of
from test_build import K, N, P, draft, draft_item, verdicts

from netacho import pipeline, runner, seisho, store
from netacho.budget import Budget

DUMMY = Path(__file__).parent / "fixtures" / "dummy_koza" / P / N / "koza" / K


def transcript():
    return {
        "koza_id": "k",
        "segments": [
            {"id": "seg-0001", "start": 0.0, "end": 2.0, "text": "ゆあにーせかるたぎがーの"},
            {"id": "seg-0002", "start": 3.0, "end": 5.0, "text": "傘を貸しておくんなさい"},
            {"id": "seg-0003", "start": 6.0, "end": 7.0, "text": ""},
        ],
    }


def line(seg, text, speaker=None):
    return {"seg_id": seg, "speaker": speaker, "text": text}


def draft_lines(*rows):
    return {"schema_version": 1, "lines": list(rows)}


def test_keeps_raw_and_marks_changed():
    d = draft_lines(
        line("seg-0001", "ゆあにーせかるたぎがーの"), line("seg-0002", "傘を貸しておくんなさい。", "太助")
    )
    doc, stats = seisho.build(d, transcript(), None)
    assert doc["lines"][1] == {
        "seg_id": "seg-0002",
        "speaker": "太助",
        "text": "傘を貸しておくんなさい。",
        "raw": "傘を貸しておくんなさい",
        "changed": True,
    }
    assert (stats["lines"], stats["changed"], stats["kept_raw"], stats["missing"]) == (2, 1, 0, 0)


def one(raw, text, speaker=None, names=None):
    t = {"koza_id": "k", "segments": [{"id": "seg-0001", "start": 0.0, "end": 2.0, "text": raw}]}
    return seisho.build(draft_lines(line("seg-0001", text, speaker)), t, None, names)


def test_summary_and_padding_go_back_to_raw():
    """削りと足しは聞き違いの修正ではない。案は残して原文に戻す。"""
    raw = "ご隠居さん、傘を貸しておくんなさい。この雨はなかなか止みません。"
    doc, stats = one(raw, "傘を貸して。")
    assert doc["lines"][0]["text"] == raw and doc["lines"][0]["proposal"] == "傘を貸して。"
    assert stats["kept_raw"] == 1 and stats["changed"] == 0
    doc, stats = one("傘を貸して", "傘を貸してください、とご隠居に頭を下げて、長々と頼み込みました。")
    assert stats["kept_raw"] == 1


def test_song_fix_stays_with_a_mark():
    """歌の表記の修正は原文から遠いが、長さが近いので通す。原文は並ぶ。"""
    doc, stats = one("ゆあにーせかるたぎがーの", "岩にせかるる滝川の")
    assert doc["lines"][0]["text"] == "岩にせかるる滝川の" and doc["lines"][0]["heavy"] is True
    assert stats["heavy"] == 1 and stats["kept_raw"] == 0


def test_short_line_is_judged_by_absolute_length():
    doc, stats = one("はい", "はあ")
    assert doc["lines"][0]["text"] == "はあ" and stats["kept_raw"] == 0


def test_empty_answer_is_counted_apart():
    doc, stats = one("はい", "  ")
    assert doc["lines"][0]["text"] == "はい"
    assert stats["empty"] == 1 and stats["kept_raw"] == 0


def test_unknown_speaker_is_dropped_and_counted():
    doc, stats = one("はい", "はい", "知らない人", {"太助"})
    assert doc["lines"][0]["speaker"] is None and stats["unknown_speaker"] == 1


def test_missing_and_extra_lines():
    d = draft_lines(line("seg-0002", "傘を貸しておくんなさい"), line("seg-9999", "足した行"))
    doc, stats = seisho.build(d, transcript(), None)
    assert [x["seg_id"] for x in doc["lines"]] == ["seg-0001", "seg-0002"]
    assert doc["lines"][0]["text"] == "ゆあにーせかるたぎがーの"  # 抜けた区間は原文
    assert stats["missing"] == 1  # seg-0001（空の seg-0003 は数えない）
    assert stats["extra"] == 1


def test_duplicate_lines_use_raw():
    d = draft_lines(line("seg-0002", "一"), line("seg-0002", "二"))
    doc, stats = seisho.build(d, transcript(), None)
    assert doc["lines"][1]["text"] == "傘を貸しておくんなさい"
    assert stats["duplicate"] == 1


def test_marks_come_from_the_script():
    reactions = {"reactions": [{"t0": 4.0, "kind": "laugh", "method": "auto"}]}
    d = draft_lines(line("seg-0002", "傘を貸しておくんなさい"))
    doc, _ = seisho.build(d, transcript(), reactions)
    assert doc["lines"][1]["marks"] == ["（笑い）"]


def test_markdown_shows_guess_and_raw():
    d = draft_lines(line("seg-0002", "傘を貸しておくんなさい。", "太助"))
    doc, _ = seisho.build(d, transcript(), None)
    text = seisho.markdown({"neta": "架空の噺", "id": "k"}, doc)
    assert "太助（推定）：傘を貸しておくんなさい。" in text
    assert "（原文：傘を貸しておくんなさい）" in text
    assert "演者の言葉そのものではありません" in text


@pytest.fixture
def built(data, tmp_path):
    client = ScriptedClient(writer=[draft(draft_item())], checker=[verdicts(("item-0001", "supported"))])
    pipeline.build(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    return store.Neta(P, N, data)


def test_clean_copy_writes_files_and_leaves_books_alone(built, data, tmp_path):
    before = (built.hanashi.read_text(encoding="utf-8"), built.kata.read_text(encoding="utf-8"))
    doc = json.loads((DUMMY.parent.parent / "drafts" / "scribe.json").read_text(encoding="utf-8"))
    client = ScriptedClient(scribe=[doc])
    run = pipeline.clean_copy(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    kdir = built.koza_dir(K)
    assert store.read(kdir / "seisho.json", "seisho")["lines"]
    assert (kdir / "seisho.md").exists()
    assert run["seisho"]["missing"] == 0
    assert (built.hanashi.read_text(encoding="utf-8"), built.kata.read_text(encoding="utf-8")) == before


def test_failed_scribe_keeps_old_copy(built, data, tmp_path):
    kdir = built.koza_dir(K)
    (kdir / "seisho.md").write_text("前の清書", encoding="utf-8")
    bad = ScriptedClient(scribe=[lambda kw: '{"schema_version": 1}'])
    with pytest.raises(ValueError):
        pipeline.clean_copy(P, N, K, client=bad, budget=Budget(tmp_path / "ledger.json"), root=data)
    assert (kdir / "seisho.md").read_text(encoding="utf-8") == "前の清書"


def test_scribe_sees_no_reactions(built, data, tmp_path):
    client = ScriptedClient(scribe=[draft_lines()])
    pipeline.clean_copy(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    text = text_of(client.calls["scribe"][0])
    assert "react-" not in text and "strength" not in text
    assert "seg-0001" in text and N in text


def test_role_promises():
    text = runner.role_prompt("scribe")
    assert "言い換え、要約" in text and "見せないもの" not in text
