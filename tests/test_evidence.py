"""根拠の検証のテスト。ダミーの transcript に、下書きの項目を当てる。"""

import copy
import json
from pathlib import Path

import pytest

from netacho import evidence

BASE = Path(__file__).parent / "fixtures" / "dummy_koza" / "performer-dummy" / "雨宿り勘定"
KOZA = BASE / "koza" / "2026-01-01-dummy-hall"


@pytest.fixture
def transcript():
    return json.loads((KOZA / "transcript.json").read_text(encoding="utf-8"))


def item(**kw):
    base = {
        "id": None,
        "kind": "場面",
        "book": "hanashi",
        "priority": 1,
        "text": "太助が傘を借りる",
        "basis": "heard",
        "source": {"segs": ["seg-0004", "seg-0006"]},
        "payoff": None,
    }
    return {**base, **kw}


def run(transcript, *items, tool=(), performer=()):
    return evidence.verify_items(
        {"items": list(items)}, transcript, tool_ids=set(tool), performer_ids=set(performer)
    )


def test_time_and_quote_come_from_segments(transcript):
    kept, dropped = run(transcript, item())
    assert not dropped
    src = kept[0]["source"]
    assert src["koza_id"] == "2026-01-01-dummy-hall"
    assert (src["t0"], src["t1"]) == (18.6, 31.0)
    assert src["quote"].startswith("ご隠居さん、こんちは。おう太助か")
    assert kept[0]["edited_by"] == "tool" and kept[0]["drafted_by"] == "writer"


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (item(source={"segs": ["seg-0004", "seg-9999"]}), "bad_seg"),
        (item(source={"segs": ["seg-0006", "seg-0004"]}), "bad_seg"),
        (item(kind="枕", book="kata", basis="knowledge"), "kata_not_heard"),
        (item(kind="見立て", book="kata", basis="knowledge"), "kata_not_heard"),
        (item(kind="サゲ"), "no_payoff"),
        (item(kind="サゲ", payoff={"segs": ["seg-0012", "seg-9999"]}), "bad_payoff_seg"),
        (item(kind="サゲ", payoff={"segs": ["seg-0001", "seg-0001"]}), "payoff_before_source"),
        (item(kind="間", book="kata", source={"segs": ["seg-0004", "seg-0004"]}), "ma_no_pause"),
        (item(kind="上下", book="kata"), "kind_not_allowed"),
        (item(kind="staging", book="kata"), "kind_not_allowed"),
        (item(id="item-0099"), "unknown_id"),
        (item(id="item-0050"), "performer_item"),
    ],
)
def test_dropped_with_reason(transcript, bad, reason):
    kept, dropped = run(transcript, bad, performer=["item-0050"])
    assert kept == []
    assert dropped == {reason: 1}


def test_same_id_twice_keeps_the_first(transcript):
    kept, dropped = run(
        transcript, item(id="item-0001", text="先"), item(id="item-0001", text="後"), tool=["item-0001"]
    )
    assert [k["text"] for k in kept] == ["先"]
    assert dropped == {"duplicate_id": 1}


def test_kata_knowledge_is_never_kept_even_if_writer_says_hanashi(transcript):
    """枕は book を hanashi と書いても型に入る。型なので knowledge は落ちる。"""
    _, dropped = run(transcript, item(kind="枕", book="hanashi", basis="knowledge"))
    assert dropped == {"kata_not_heard": 1}


def test_hanashi_knowledge_stays(transcript):
    kept, dropped = run(transcript, item(kind="知らない物", book="hanashi", basis="knowledge"))
    assert not dropped and kept[0]["basis"] == "knowledge"


def test_book_is_forced_by_kind(transcript):
    kept, _ = run(
        transcript,
        item(kind="枕", book="hanashi"),
        item(kind="人物", book="kata"),
        item(kind="知らない物", book="kata"),
    )
    assert [k["book"] for k in kept] == ["kata", "hanashi", "kata"]


def test_ma_pause_must_follow_the_last_segment(transcript):
    """長い範囲のどこかに間があるだけでは、間の根拠にならない。"""
    _, dropped = run(transcript, item(kind="間", book="kata", source={"segs": ["seg-0003", "seg-0004"]}))
    assert dropped == {"ma_no_pause": 1}


def test_ma_needs_a_real_pause(transcript):
    kept, dropped = run(transcript, item(kind="間", book="kata", source={"segs": ["seg-0003", "seg-0003"]}))
    assert kept and not dropped  # seg-0003 の後に 1.6 秒


def test_payoff_is_resolved_and_stripped_elsewhere(transcript):
    kept, _ = run(
        transcript,
        item(
            kind="見立て",
            book="kata",
            source={"segs": ["seg-0008", "seg-0008"]},
            payoff={"segs": ["seg-0009", "seg-0009"]},
        ),
        item(payoff={"segs": ["seg-0009", "seg-0009"]}),
    )
    assert kept[0]["payoff"]["t1"] == 45.0
    assert "payoff" not in kept[1]


def test_empty_segments_drop_item_but_partly_empty_keeps(transcript):
    """全部空の区間は根拠にならない。一部だけ空なら残す。"""
    t = copy.deepcopy(transcript)
    t["segments"][3]["text"] = ""
    t["segments"][4]["text"] = " "
    kept, dropped = run(t, item(source={"segs": ["seg-0004", "seg-0005"]}))
    assert kept == [] and dropped == {"empty_quote": 1}
    kept, dropped = run(t, item(source={"segs": ["seg-0004", "seg-0006"]}))
    assert kept[0]["source"]["quote"] == "傘を貸しておくんなさい。"
    kept, dropped = run(
        t,
        item(
            kind="サゲ", source={"segs": ["seg-0001", "seg-0001"]}, payoff={"segs": ["seg-0004", "seg-0004"]}
        ),
    )
    assert dropped == {"empty_payoff": 1}


def test_span_too_long(transcript):
    t = copy.deepcopy(transcript)
    t["segments"][-1]["end"] = 500.0
    _, dropped = run(t, item(source={"segs": ["seg-0001", "seg-0012"]}))
    assert dropped == {"span_too_long": 1}


def test_payoff_span_too_long(transcript):
    t = copy.deepcopy(transcript)
    t["segments"][-1]["end"] = 500.0
    kind = {"kind": "サゲ", "source": {"segs": ["seg-0001", "seg-0001"]}}
    _, dropped = run(t, item(**kind, payoff={"segs": ["seg-0002", "seg-0012"]}))
    assert dropped == {"payoff_too_long": 1}


def test_gag_inside_one_segment_is_allowed(transcript):
    """Whisper の区間は長いので、仕込みと回収が同じ区間に入ることがある。"""
    span = {"segs": ["seg-0008", "seg-0008"]}
    kept, dropped = run(transcript, item(kind="見立て", book="kata", source=span, payoff=span))
    assert kept and not dropped


def test_overlapping_segments_use_the_latest_end(transcript):
    t = copy.deepcopy(transcript)
    t["segments"][3]["end"] = 40.0
    kept, _ = run(t, item())
    assert kept[0]["source"]["t1"] == 40.0


def test_scenes_and_timeline(transcript):
    draft = {
        "scenes": [
            {"id": "scene-02", "title": "b", "order": 2, "segs": ["seg-0004", "seg-0009"]},
            {"id": "scene-01", "title": "a", "order": 1, "segs": ["seg-0001", "seg-0003"]},
            {"id": "scene-03", "title": "壊れた", "order": 3, "segs": ["seg-0010", "seg-9999"]},
        ]
    }
    scenes, dropped = evidence.verify_scenes(draft, transcript)
    assert [s["id"] for s in scenes] == ["scene-01", "scene-02"] and dropped == 1
    tl = evidence.timeline(scenes, transcript)
    assert tl["segments"][0]["scene_id"] == "scene-01"
    assert tl["segments"][3]["scene_id"] == "scene-02"
    assert tl["segments"][11] == {"seg_id": "seg-0012", "scene_id": None, "speaker": None, "confidence": None}
