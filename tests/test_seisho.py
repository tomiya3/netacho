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
    assert stats == {"lines": 2, "changed": 1, "kept_raw": 0, "missing": 0}


def test_rewrite_goes_back_to_raw_and_is_counted():
    """言い換えや要約は聞き違いの修正ではない。"""
    d = draft_lines(
        line("seg-0001", "ゆあにーせかるたぎがーの"),
        line("seg-0002", "全く別の文章に書き換えました、長々と。"),
    )
    doc, stats = seisho.build(d, transcript(), None)
    assert doc["lines"][1]["text"] == "傘を貸しておくんなさい"
    assert stats["kept_raw"] == 1 and stats["changed"] == 0


def test_missing_and_extra_lines():
    d = draft_lines(line("seg-0002", "傘を貸しておくんなさい"), line("seg-9999", "足した行"))
    doc, stats = seisho.build(d, transcript(), None)
    assert [x["seg_id"] for x in doc["lines"]] == ["seg-0001", "seg-0002"]
    assert doc["lines"][0]["text"] == "ゆあにーせかるたぎがーの"  # 抜けた区間は原文
    assert stats["missing"] == 2  # seg-0001 と空の seg-0003


def test_duplicate_lines_use_raw():
    d = draft_lines(line("seg-0002", "一"), line("seg-0002", "二"))
    doc, _ = seisho.build(d, transcript(), None)
    assert doc["lines"][1]["text"] == "傘を貸しておくんなさい"


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


def test_scribe_sees_no_reactions(built, data, tmp_path):
    client = ScriptedClient(scribe=[draft_lines()])
    pipeline.clean_copy(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    text = text_of(client.calls["scribe"][0])
    assert "react-" not in text and "strength" not in text
    assert "seg-0001" in text and N in text


def test_role_promises():
    text = runner.role_prompt("scribe")
    assert "言い換え、要約" in text and "見せないもの" not in text
