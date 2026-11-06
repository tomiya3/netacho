"""清書のテスト。"""

import json
from pathlib import Path

import pytest
from fakes import ScriptedClient, text_of
from test_build import K, N, P, draft, draft_item, verdicts

from netacho import cli, pipeline, runner, seisho, store
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


def test_short_line_keeps_a_near_fix():
    doc, stats = one("へえー", "へえ")
    assert doc["lines"][0]["text"] == "へえ" and stats["kept_raw"] == 0


def test_short_line_is_not_rewritten():
    """相づちの言い換えは、短いので長さでは止まらない。似ていなければ原文に戻す。"""
    doc, stats = one("はい", "承知いたしました")
    assert doc["lines"][0]["text"] == "はい" and stats["kept_raw"] == 1


def test_missing_lines_are_shown_at_the_top():
    doc, stats = seisho.build(draft_lines(), transcript(), None)
    assert stats["missing"] == 2
    assert "返さなかった区間が 2" in seisho.markdown({"neta": "架空の噺", "id": "k"}, doc, stats)


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


def test_long_koza_is_split_and_put_back_in_order(built, data, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "SCRIBE_CHUNK", 5)
    seg = store.read(built.koza_dir(K) / "transcript.json", "transcript")["segments"]
    replies = [
        draft_lines(*(line(s["id"], s["text"]) for s in seg[i : i + 5])) for i in range(0, len(seg), 5)
    ]
    client = ScriptedClient(scribe=replies)
    run = pipeline.clean_copy(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    assert len(client.calls["scribe"]) == 3
    assert run["seisho"]["missing"] == 0
    assert [x["seg_id"] for x in store.read(built.koza_dir(K) / "seisho.json", "seisho")["lines"]][:2] == [
        "seg-0001",
        "seg-0002",
    ]


def test_cost_stays_when_scribe_is_cut(built, data, tmp_path):
    bad = ScriptedClient(scribe=[lambda kw: '{"schema_version": 1}'])
    with pytest.raises(ValueError):
        pipeline.clean_copy(P, N, K, client=bad, budget=Budget(tmp_path / "ledger.json"), root=data)
    run = store.read(built.koza_dir(K) / "run.json", "run")
    assert "scribe" in {r["name"] for r in run["roles"]}
    assert run["evidence"]["kept"] == 1  # build の数字も残る


def test_mark_before_first_line_is_kept():
    t = {"koza_id": "k", "segments": [{"id": "seg-0001", "start": 5.0, "end": 6.0, "text": "一"}]}
    reactions = {"reactions": [{"t0": 1.0, "kind": "applause", "method": "auto"}]}
    doc, _ = seisho.build(draft_lines(line("seg-0001", "一")), t, reactions)
    assert doc["lines"][0]["marks"] == ["（拍手）"] and doc["lines"][1]["text"] == "一"


def test_empty_segment_gets_no_invented_text():
    t = {
        "koza_id": "k",
        "segments": [{"id": "seg-0001", "start": 0.0, "end": 2.0, "text": ""}],
    }
    reactions = {"reactions": [{"t0": 1.0, "kind": "laugh", "method": "auto"}]}
    doc, stats = seisho.build(draft_lines(line("seg-0001", "作文")), t, reactions)
    assert doc["lines"][0]["text"] == "" and stats["changed"] == 0


def test_newlines_in_text_are_flattened():
    doc, _ = one("傘を貸して", "傘を\n貸して")
    assert "\n" not in doc["lines"][0]["text"]


def test_mark_removes_clean_copy_with_old_marks():
    from netacho import cli

    assert {"seisho.json", "seisho.md"} <= set(cli.STALE_AFTER_MARK)


def test_scribe_sees_no_reactions(built, data, tmp_path):
    client = ScriptedClient(scribe=[draft_lines()])
    pipeline.clean_copy(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    text = text_of(client.calls["scribe"][0])
    assert "react-" not in text and "strength" not in text
    assert "seg-0001" in text and N in text


def test_role_promises():
    text = runner.role_prompt("scribe")
    assert "言い換え、要約" in text and "見せないもの" not in text


def test_title_reaches_the_scribe_instead_of_the_folder_name(built, data, tmp_path):
    doc = json.loads((DUMMY.parent.parent / "drafts" / "scribe.json").read_text(encoding="utf-8"))
    client = ScriptedClient(scribe=[doc])
    pipeline.clean_copy(
        P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data, title="正しい題"
    )
    assert "正しい題" in text_of(client.calls["scribe"][0])
    run = store.read(store.Neta(P, N, data).koza_dir(K) / "run.json", "run")
    assert run["seisho"]["title"] == "正しい題"


def test_lexicon_reads_the_whole_koza_and_reaches_every_chunk(built, data, tmp_path, monkeypatch):
    transcript = store.read(store.Neta(P, N, data).koza_dir(K) / "transcript.json", "transcript")
    first, last = transcript["segments"][0]["text"], transcript["segments"][-1]["text"]
    heard = first.strip()[:3]
    terms = {
        "schema_version": 1,
        "terms": [{"heard": heard, "word": "確かな語"}, {"heard": "無い字", "word": "作った語"}],
    }
    monkeypatch.setattr(pipeline, "SCRIBE_CHUNK", 2)
    doc = json.loads((DUMMY.parent.parent / "drafts" / "scribe.json").read_text(encoding="utf-8"))
    replies = [lambda kw, d=doc: _echo(kw, d)] * len(transcript["segments"])
    client = ScriptedClient(lexicon=[terms], scribe=replies)
    pipeline.clean_copy(
        P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data, terms=["会場の語"]
    )
    whole = text_of(client.calls["lexicon"][0])
    assert first.strip() in whole and last.strip() in whole and "会場の語" in whole
    assert "react-" not in whole and "strength" not in whole
    assert len(client.calls["scribe"]) >= 2
    for call in client.calls["scribe"]:
        text = text_of(call)
        assert "確かな語" in text and "会場の語" in text and "作った語" not in text
    run = store.read(store.Neta(P, N, data).koza_dir(K) / "run.json", "run")
    assert (run["seisho"]["terms"], run["seisho"]["terms_dropped"]) == (1, 1)


def test_short_or_empty_lexicon_rows_are_dropped():
    segs = [{"text": "傘を借りた"}]
    rows = [("傘", "笠"), ("借りた", "借りた"), ("を借", "語")]
    draft = {"terms": [{"heard": h, "word": w} for h, w in rows]}
    assert pipeline.check_lexicon(draft, segs) == ([{"聞こえ方": "を借", "語": "語"}], 2)


def test_cost_stays_when_lexicon_fails(built, data, tmp_path):
    bad = ScriptedClient(lexicon=[lambda kw: '{"schema_version": 1}'])
    with pytest.raises(ValueError):
        pipeline.clean_copy(P, N, K, client=bad, budget=Budget(tmp_path / "ledger.json"), root=data)
    run = store.read(store.Neta(P, N, data).koza_dir(K) / "run.json", "run")
    assert "lexicon" in {r["name"] for r in run["roles"]}


def _echo(kw, doc):
    """渡された区間の分だけ返す。"""
    asked = {row["seg_id"] for row in json.loads(kw["messages"][0]["content"][-1]["text"])}
    lines = [line for line in doc["lines"] if line["seg_id"] in asked]
    return json.dumps({"schema_version": 1, "lines": lines}, ensure_ascii=False)


def test_lexicon_promises():
    text = runner.role_prompt("lexicon")
    assert "作りません" in text and "見せないもの" not in text
    assert runner.ROLES["lexicon"][:2] == (runner.OPUS, "high")


def test_scribe_is_opus_high():
    assert runner.ROLES["scribe"][:2] == (runner.OPUS, "high")


def test_title_is_trimmed_and_a_blank_one_stops():
    assert cli._title(None) is None and cli._title(" 題 ") == "題"
    with pytest.raises(SystemExit):
        cli._title("  ")


def test_without_a_title_the_folder_name_goes_and_none_is_recorded(built, data, tmp_path):
    doc = json.loads((DUMMY.parent.parent / "drafts" / "scribe.json").read_text(encoding="utf-8"))
    client = ScriptedClient(scribe=[doc])
    pipeline.clean_copy(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    assert N in text_of(client.calls["scribe"][0])
    assert "title" not in store.read(store.Neta(P, N, data).koza_dir(K) / "run.json", "run")["seisho"]
