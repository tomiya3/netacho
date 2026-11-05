"""反応の候補のテスト。合成した音だけを使う。"""

import numpy as np
import pytest

from netacho import reactions as rx

SR = rx.SAMPLE_RATE


def noise(seconds: float, db: float, seed: int = 0) -> np.ndarray:
    """RMS が db の白色雑音（話し声の代わり）。"""
    x = np.random.default_rng(seed).standard_normal(int(seconds * SR))
    return (x * 10 ** (db / 20)).astype(np.float32)


def speech_with(
    *bursts: tuple[float, float, float], seconds: float = 60.0, level: float = -30.0
) -> np.ndarray:
    """level の音に、(始まり, 長さ, dB) の音を足す。"""
    a = noise(seconds, level)
    for n, (t0, dur, db) in enumerate(bursts, start=1):
        i = int(t0 * SR)
        a[i : i + int(dur * SR)] += noise(dur, db, seed=n)[: int(dur * SR)]
    return a


def detect(a: np.ndarray) -> list[dict]:
    db = rx.rms_db(a)
    return rx.auto_reactions(db, rx.baseline(db))


def test_burst_over_speech_is_found():
    """発話の上に重なる音を拾う（発話区間を除外しない）。"""
    found = detect(speech_with((20.0, 2.0, -18.0)))
    assert len(found) == 1
    assert found[0]["t0"] == pytest.approx(20.0, abs=0.15)
    assert found[0]["t1"] == pytest.approx(22.0, abs=0.15)
    assert found[0]["method"] == "auto" and found[0]["strength_rank"] == 1


def test_too_small_or_too_short_is_not_found():
    assert detect(speech_with((20.0, 2.0, -35.0))) == []  # 基準との差が 6dB に届かない
    assert detect(speech_with((20.0, 0.2, -18.0))) == []  # 0.4 秒に届かない


def test_short_gap_is_merged_and_long_gap_is_not():
    merged = detect(speech_with((20.0, 0.5, -18.0), (20.8, 0.5, -18.0)))
    assert len(merged) == 1
    apart = detect(speech_with((20.0, 0.5, -18.0), (21.2, 0.5, -18.0)))
    assert len(apart) == 2


def test_two_clicks_with_a_gap_are_not_a_reaction():
    """基準を超えたのが 0.1 秒ずつの 2 点なら、つないでも候補にしない（超えた長さは 0.2 秒）。"""
    db = np.full(200, -30.0)
    base = np.full(200, -30.0)
    db[50] = db[54] = -20.0
    assert rx.find_spans(db, base) == []


def test_chopped_laugh_survives():
    """切れ切れの笑い（0.2 秒ずつ）は、つないでから長さを見るので残る。"""
    bursts = [(20.0 + i * 0.4, 0.2, -18.0) for i in range(6)]
    assert len(detect(speech_with(*bursts))) == 1


def test_rank_is_by_size_within_the_koza():
    found = detect(speech_with((10.0, 1.0, -22.0), (30.0, 3.0, -15.0), (50.0, 1.0, -20.0)))
    assert [r["strength_rank"] for r in found] == [3, 1, 2]


def test_rank_ignores_absolute_level():
    """全体の音量が変わっても順位は同じ（自動音量調整があるので絶対値を使わない）。"""
    a = speech_with((10.0, 1.0, -22.0), (30.0, 3.0, -15.0))
    assert [r["strength_rank"] for r in detect(a)] == [r["strength_rank"] for r in detect(a * 0.25)]


def test_slow_level_change_is_not_a_reaction():
    """音量がゆっくり変わるだけなら候補にしない。"""
    a = noise(120.0, -30.0) * np.linspace(1.0, 2.0, 120 * SR, dtype=np.float32)
    assert detect(a) == []


def test_digital_silence_then_quiet_noise_is_not_a_reaction():
    a = np.concatenate(
        [np.zeros(30 * SR, dtype=np.float32), noise(1.0, -70.0), np.zeros(30 * SR, np.float32)]
    )
    assert detect(a) == []


def test_laugh_over_loud_speech():
    """台詞が基準を持ち上げていても、その上に重なる笑いを拾う。"""
    found = detect(speech_with((30.0, 2.0, -12.0), level=-20.0))
    assert len(found) == 1
    assert found[0]["t0"] == pytest.approx(30.0, abs=0.2)


def test_level_step_is_not_a_reaction():
    """途中で音量が一段上がっても（自動音量調整）、段差は候補にならない。"""
    a = np.concatenate([noise(60.0, -30.0), noise(60.0, -22.0, seed=1)])
    assert detect(a) == []


def test_finds_laugh_after_level_step():
    a = np.concatenate([noise(60.0, -30.0), noise(60.0, -22.0, seed=1)])
    i = 90 * SR
    a[i : i + 2 * SR] += noise(2.0, -10.0, seed=2)[: 2 * SR]
    found = detect(a)
    assert len(found) == 1 and found[0]["t0"] == pytest.approx(90.0, abs=0.2)


def test_long_laugh_limit():
    """基準は前後 10 秒の中央値なので、10 秒を超えて続く音は中央値が持ち上がって拾えない（既知の限界）。"""
    assert len(detect(speech_with((30.0, 8.0, -18.0), seconds=90.0))) == 1
    assert detect(speech_with((30.0, 16.0, -18.0), seconds=90.0)) == []


def test_reflected_edge_finds_opening_laugh():
    found = detect(speech_with((0.5, 1.5, -18.0)))
    assert len(found) == 1


def test_same_strength_rank_goes_to_the_earlier():
    db = np.full(100, -30.0)
    base = np.full(100, -30.0)
    db[10:20] = -20.0
    db[60:70] = -20.0
    assert [r["strength_rank"] for r in rx.auto_reactions(db, base)] == [1, 2]


def test_edges_do_not_crash():
    assert rx.rms_db(np.zeros(10, dtype=np.float32)).size == 0
    assert rx.baseline(np.zeros(0)).size == 0
    assert detect(np.zeros(0, dtype=np.float32)) == []
    assert detect(noise(0.1, -30.0)) == []
    assert detect(noise(3.0, -30.0)) == []  # 基準の窓より短い音


def test_wave_keeps_short_peak():
    a = speech_with((20.0, 0.2, -10.0))
    db = rx.rms_db(a)
    w = rx.wave(db, rx.baseline(db))
    assert w["hop"] == 0.5 and len(w["db"]) == len(w["baseline_db"]) == 120
    assert max(w["db"]) > -15  # 間引いても山が残る


def _transcript(segments, vad=()):
    return {"koza_id": "k", "segments": segments, "vad": list(vad)}


def _seg(i, start, end, text="あ", pause=0.0):
    return {"id": f"seg-{i:04d}", "start": start, "end": end, "text": text, "pause_after": pause}


def test_hints():
    t = _transcript(
        [_seg(1, 0.0, 2.0, pause=2.0), _seg(2, 4.0, 6.0, pause=0.5), _seg(3, 6.5, 7.0, text=" ", pause=None)],
        vad=[{"start": 0.0, "end": 2.0}, {"start": 6.5, "end": 7.0}, {"start": 9.0, "end": 10.0}],
    )
    got = rx.hints(t)
    assert [(h["kind"], h["t0"], h["seg_id"]) for h in got] == [
        ("long_pause", 2.0, "seg-0001"),
        ("vad_no_text", 6.5, None),  # 空白だけの区間は文字起こしが空とみなす
        ("vad_no_text", 9.0, None),
    ]


def test_hint_keeps_vad_with_a_small_overlap():
    """台詞の端と少し重なるだけの VAD 区間は、文字の無い部分が長ければ手がかりに残す。"""
    t = _transcript(
        [_seg(1, 0.0, 2.0, pause=None)],
        vad=[{"start": 1.9, "end": 5.0}, {"start": 0.0, "end": 2.0}],
    )
    assert [(h["kind"], h["t0"]) for h in rx.hints(t)] == [("vad_no_text", 1.9)]


@pytest.mark.parametrize(
    ("t", "text"), [(59.96, "1:00.0"), (0.0, "0:00.0"), (61.25, "1:01.2"), (3599.99, "60:00.0")]
)
def test_clock_rounds_before_splitting(t, text):
    assert rx.clock(t) == text


def test_build_validates_against_schema():
    from netacho.schema import validate

    t = _transcript([{**_seg(1, 0.0, 2.0, pause=None)}])
    doc = rx.build(speech_with((20.0, 2.0, -18.0)), t)
    validate(doc, "reactions")
    assert [r["id"] for r in doc["reactions"]] == ["react-0001"]


def test_parse_marks(tmp_path):
    f = tmp_path / "m.tsv"
    f.write_text("# 耳で付けた\n1:02.5\t1:05\t2\n\n10\t12.5\t1\n1:00:00\t1:00:01\n", encoding="utf-8")
    rows = rx.parse_marks(f)
    assert rows == [(62.5, 65.0, 2), (10.0, 12.5, 1), (3600.0, 3601.0, None)]
    got = rx.manual_reactions(rows)
    assert [(r["t0"], r["strength_rank"], r["method"]) for r in got] == [
        (10.0, 1, "manual"),
        (62.5, 2, "manual"),
        (3600.0, None, "manual"),  # 順位を書かなければ空（偽の順位を作らない）
    ]
    assert [r["id"] for r in rx.top({"reactions": got}, 2)] == ["react-0001", "react-0002"]
    assert [r["id"] for r in got] == ["react-0001", "react-0002", "react-0003"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# だけ\n",
        "1\n",
        "1\t2\t3\t4\n",
        "abc\t2\n",
        "5\t3\n",
        "1:2:3:4\t5\n",
        "1:75\t2:00\n",
        "1:75:00\t2:00:00\n",
        "１\t２\n",
    ],
)
def test_parse_marks_rejects(tmp_path, text):
    f = tmp_path / "m.tsv"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        rx.parse_marks(f)


def test_manual_rank_must_not_collide():
    with pytest.raises(ValueError):
        rx.manual_reactions([(1.0, 2.0, 1), (3.0, 4.0, 1)])
