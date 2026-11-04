"""反応の候補のテスト。AST は呼ばず、スコアの列を入れる。"""

import pytest

from netacho import reactions as rx
from netacho.preprocess import ast_scores


def scores(*, laugh=(), applause=(), music=(), n=None):
    n = n or max(len(laugh), len(applause), len(music))

    def fill(v):
        return list(v) + [0.0] * (n - len(v))

    return {
        "model": "fake",
        "window": 5.0,
        "stride": 2.0,
        "laugh": fill(laugh),
        "applause": fill(applause),
        "music": fill(music),
    }


def kinds(doc):
    return [(r["kind"], r["t0"], r["t1"]) for r in doc]


def test_laugh_is_judged_within_the_koza():
    """絶対値では判定しない。最大が 0.3 の高座では 0.15 以上、0.2 の高座では 0.1 以上が山。"""
    low = [0.01, 0.12, 0.01, 0.01, 0.3, 0.01]
    assert kinds(rx.auto_reactions(scores(laugh=low))) == [("laugh", 9.5, 11.5)]
    high = [0.01, 0.12, 0.01, 0.01, 0.2, 0.01]
    assert len(rx.auto_reactions(scores(laugh=high))) == 2


def test_no_peak_means_no_laugh():
    """山の無い抜粋で、弱い揺れを山と呼ばない。"""
    assert rx.auto_reactions(scores(laugh=[0.01, 0.05, 0.02, 0.06])) == []


def test_short_gap_is_merged_and_long_gap_is_not():
    merged = rx.auto_reactions(scores(laugh=[0.3, 0.0, 0.3]))
    assert kinds(merged) == [("laugh", 1.5, 7.5)]
    apart = rx.auto_reactions(scores(laugh=[0.3, 0.0, 0.0, 0.3]))
    assert len(apart) == 2


def test_applause_ignores_music():
    """中手に出囃子が重なって Music が高くても、拍手として採用する。"""
    s = scores(applause=[0.0, 0.65, 0.5, 0.0], music=[0.0, 0.44, 0.9, 0.0])
    assert kinds(rx.auto_reactions(s)) == [("applause", 3.5, 7.5)]


def test_applause_uses_an_absolute_line():
    assert rx.auto_reactions(scores(applause=[0.1, 0.29, 0.1])) == []


def test_music_never_becomes_a_reaction():
    s = scores(music=[0.5, 0.8, 0.5, 0.0, 0.0, 0.6])
    assert rx.auto_reactions(s) == []
    assert rx.music_candidates(s) == [
        {"t0": 1.5, "t1": 7.5, "score": 0.8},
        {"t0": 11.5, "t1": 13.5, "score": 0.6},
    ]


def test_rank_is_by_peak_and_ids_by_time():
    s = scores(laugh=[0.2, 0.0, 0.0, 0.3], applause=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8])
    got = rx.auto_reactions(s)
    assert [r["id"] for r in got] == ["react-0001", "react-0002", "react-0003"]
    assert [r["strength_rank"] for r in got] == [3, 1, 2]  # 種類ごとの最大が同じ強さ。同じなら早い方


def test_rank_does_not_let_applause_always_win():
    """笑いの最大（0.3）と弱い拍手（0.4、最大 0.8）では、笑いが上に来る。"""
    s = scores(laugh=[0.3, 0.0, 0.0, 0.0], applause=[0.0, 0.0, 0.0, 0.4, 0.0, 0.0, 0.8])
    got = rx.auto_reactions(s)
    assert [(r["kind"], r["strength_rank"]) for r in got] == [("laugh", 1), ("applause", 3), ("applause", 2)]


def test_same_peak_rank_goes_to_the_earlier():
    got = rx.auto_reactions(scores(laugh=[0.3, 0.0, 0.0, 0.3]))
    assert [r["strength_rank"] for r in got] == [1, 2]


def test_laugh_and_applause_may_overlap():
    got = rx.auto_reactions(scores(laugh=[0.3], applause=[0.6]))
    assert {r["kind"] for r in got} == {"laugh", "applause"}


def test_empty_scores():
    assert rx.auto_reactions(scores(n=1)) == []
    assert rx.covered_seconds({"scores": {"laugh": [], "stride": 2.0, "window": 5.0}}) is None


def test_covered_seconds():
    assert rx.covered_seconds({"scores": scores(n=3)}) == 9.0
    assert rx.covered_seconds({}) is None


@pytest.mark.parametrize(
    ("samples", "want"),
    [
        (0, []),
        (16000, [0]),
        (80000, [0]),
        (80001, [0, 32000]),  # 末尾の 1 サンプルも窓に入る
        (112000, [0, 32000]),
        (112001, [0, 32000, 64000]),
        (144000, [0, 32000, 64000]),
    ],
)
def test_window_starts(samples, want):
    assert ast_scores.window_starts(samples) == want


def test_score_batches_and_keeps_order():
    import numpy as np

    seen = []

    def predict(batch):
        seen.append(len(batch))
        marks = np.array([float(b[0]) for b in batch])
        return {"laugh": marks, "applause": marks * 0, "music": marks * 0}

    audio = np.zeros(16000 * 60, dtype=np.float32)
    for i, s in enumerate(ast_scores.window_starts(len(audio))):
        audio[s] = i / 100
    got = ast_scores.score(audio, predict=predict)
    assert seen[0] == ast_scores.BATCH and sum(seen) == len(got["laugh"]) == 29
    assert got["model"].startswith("MIT/ast-finetuned-audioset-10-10-0.4593@")
    assert got["laugh"][:3] == [0.0, 0.01, 0.02]
    assert (got["window"], got["stride"]) == (5.0, 2.0)


def test_laugh_labels_match_the_spec():
    assert ast_scores.GROUPS["laugh"] == ("Laughter", "Giggle", "Chuckle, chortle", "Belly laugh")
    assert ast_scores.GROUPS["applause"] == ("Applause", "Clapping")


def test_prep_does_not_import_torch_for_other_commands():
    """torch は AST を回すときだけ読み込む（札の経路に持ち込まない）。"""
    import subprocess
    import sys

    code = (
        "import sys; import netacho.cli, netacho.guide, netacho.reactions; assert 'torch' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


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
    doc = rx.build(scores(laugh=[0.3, 0.0], music=[0.6, 0.0]), t)
    validate(doc, "reactions")
    assert [r["id"] for r in doc["reactions"]] == ["react-0001"]
    assert doc["music"][0]["score"] == 0.6
    assert doc["params"]["model"] == "fake"


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
