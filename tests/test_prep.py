"""netacho prep / mark / top のテスト。ffmpeg は本物、Whisper は差し替える。"""

import json
import shutil
import wave

import numpy as np
import pytest

from netacho import cli, store
from netacho.preprocess import audio, speech
from netacho.schema import validate

SR = 16000
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")


def write_wav(path, seconds=30.0):
    """-30dB の音に、10〜12 秒だけ -18dB の音を足す。"""
    rng = np.random.default_rng(0)
    a = rng.standard_normal(int(seconds * SR)) * 10 ** (-30 / 20)
    a[10 * SR : 12 * SR] += rng.standard_normal(2 * SR) * 10 ** (-18 / 20)
    pcm = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def fake_detect(wav, *, terms=None, **_):
    fake_detect.terms = terms
    segs = speech.number_segments(
        [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "えー",
                "no_speech_prob": 0.1,
                "words": [{"start": 0.0, "end": 2.0, "word": "えー", "p": 0.9}],
                "source": "asr",
            },
            {"start": 5.0, "end": 6.0, "text": "", "no_speech_prob": 0.9, "words": [], "source": "asr"},
        ]
    )
    return {
        "speech": {"segments": [{"start": 0.0, "end": 2.0, "source": "vad"}]},
        "transcript": {"model": "fake", "language": "ja", "segments": segs},
    }


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("NETACHO_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(speech, "detect", fake_detect)
    src = tmp_path / "src.wav"
    write_wav(src)
    return src


PREP = ["prep", "--performer", "performer-x", "--neta", "架空の噺", "--koza", "2026-02-03-hall"]


def test_prep_writes_koza_transcript_reactions(data, capsys):
    cli.main([*PREP, "--audio", str(data), "--terms", "番傘,利息,"])
    kdir = store.Neta("performer-x", "架空の噺").koza_dir("2026-02-03-hall")
    koza = store.read(kdir / "koza.json", "koza")
    assert (koza["date"], koza["venue"]) == ("2026-02-03", "hall")
    assert "excerpt" not in koza
    assert len(koza["audio_sha256"]) == 64
    t = store.read(kdir / "transcript.json", "transcript")
    assert [s["id"] for s in t["segments"]] == ["seg-0001", "seg-0002"]
    assert t["segments"][0]["pause_after"] == 3.0
    assert fake_detect.terms == ["番傘", "利息"]
    doc = store.read(kdir / "reactions.json", "reactions")
    assert len(doc["reactions"]) == 1
    assert doc["reactions"][0]["t0"] == pytest.approx(10.0, abs=0.2)
    assert "反応の候補 1 件" in capsys.readouterr().out


def test_prep_excerpt_times_start_at_excerpt(data):
    cli.main([*PREP, "--audio", str(data), "--from", "8", "--to", "20"])
    kdir = store.Neta("performer-x", "架空の噺").koza_dir("2026-02-03-hall")
    assert audio.duration(kdir / "audio.wav") == pytest.approx(12.0, abs=0.05)
    koza = store.read(kdir / "koza.json", "koza")
    assert koza["excerpt"] == {"from": 8.0, "to": 20.0}
    doc = store.read(kdir / "reactions.json", "reactions")
    assert doc["reactions"][0]["t0"] == pytest.approx(2.0, abs=0.2)  # 抜粋の先頭からの秒


def test_prep_rejects_bad_koza_and_missing_audio(data, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["prep", "--performer", "p", "--neta", "n", "--koza", "hall", "--audio", str(data)])
    with pytest.raises(SystemExit):
        cli.main([*PREP, "--audio", str(tmp_path / "none.wav")])


@pytest.mark.parametrize(
    "extra",
    [
        ["--from", "-1"],
        ["--to", "0"],
        ["--from", "5", "--to", "5"],
        ["--from", "9", "--to", "2"],
        ["--from", "99"],
    ],
)
def test_prep_rejects_bad_excerpt(data, extra):
    with pytest.raises(SystemExit):
        cli.main([*PREP, "--audio", str(data), *extra])


def test_prep_only_from_keeps_to_the_end(data):
    cli.main([*PREP, "--audio", str(data), "--from", "20"])
    kdir = store.Neta("performer-x", "架空の噺").koza_dir("2026-02-03-hall")
    assert audio.duration(kdir / "audio.wav") == pytest.approx(10.0, abs=0.05)
    assert store.read(kdir / "koza.json", "koza")["excerpt"] == {"from": 20.0, "to": None}


def test_prep_refuses_to_overwrite_manual_marks(data, tmp_path):
    cli.main([*PREP, "--audio", str(data)])
    tsv = tmp_path / "m.tsv"
    tsv.write_text("5\t6\n", encoding="utf-8")
    cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])
    with pytest.raises(SystemExit):
        cli.main([*PREP, "--audio", str(data)])
    kdir = store.find_koza("2026-02-03-hall")
    assert store.read(kdir / "reactions.json", "reactions")["reactions"][0]["method"] == "manual"
    cli.main([*PREP, "--audio", str(data), "--force"])
    assert store.read(kdir / "reactions.json", "reactions")["reactions"][0]["method"] == "auto"
    assert not (kdir / "reactions.auto.json").exists()  # 古い控えを残さない


def test_failed_prep_leaves_no_stale_results(data, monkeypatch):
    cli.main([*PREP, "--audio", str(data)])
    kdir = store.find_koza("2026-02-03-hall")

    def boom(*a, **k):
        raise ValueError("文字起こしに失敗")

    monkeypatch.setattr(speech, "detect", boom)
    with pytest.raises(SystemExit):
        cli.main([*PREP, "--audio", str(data), "--from", "5", "--to", "15"])
    assert not (kdir / "transcript.json").exists()
    assert not (kdir / "reactions.json").exists()
    assert not (kdir / "koza.json").exists()


def test_top_message_when_nothing_found(data, capsys, monkeypatch):
    from netacho import reactions

    monkeypatch.setattr(reactions, "auto_reactions", lambda *a, **k: [])
    cli.main([*PREP, "--audio", str(data)])
    assert "反応なしとは限らない" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["top", "--koza", "2026-02-03-hall", "-n", "0"])


def test_ffmpeg_failure_is_a_short_message(data, tmp_path, capsys):
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio")
    with pytest.raises(SystemExit) as e:
        cli.main([*PREP, "--audio", str(broken)])
    assert "ffprobe" in str(e.value)
    assert str(tmp_path) not in str(e.value)


def test_load_wav_rejects_wrong_format(tmp_path):
    path = tmp_path / "x.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"\0\0\0\0")
    with pytest.raises(ValueError):
        speech.load_wav(path)


def test_paths_cannot_leave_the_data_dir():
    with pytest.raises(store.StoreError):
        store.Neta("..", "n")
    with pytest.raises(store.StoreError):
        store.Neta("p", "a/b")
    with pytest.raises(store.StoreError):
        store.Neta("p", "n").koza_dir("../x")


def test_mark_replaces_reactions_and_keeps_auto(data, tmp_path, capsys):
    cli.main([*PREP, "--audio", str(data)])
    tsv = tmp_path / "m.tsv"
    tsv.write_text("5\t6\n15\t16\n", encoding="utf-8")
    cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])
    kdir = store.find_koza("2026-02-03-hall")
    doc = store.read(kdir / "reactions.json", "reactions")
    assert [r["method"] for r in doc["reactions"]] == ["manual", "manual"]
    assert "wave" in doc  # 波は残す
    auto = store.read(kdir / "reactions.auto.json", "reactions")
    assert [r["method"] for r in auto["reactions"]] == ["auto"]
    tsv.write_text("7\t8\n", encoding="utf-8")
    cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])  # 2 回目でも自動の結果は上書きしない
    auto = store.read(kdir / "reactions.auto.json", "reactions")
    assert [r["method"] for r in auto["reactions"]] == ["auto"]
    capsys.readouterr()
    cli.main(["top", "--koza", "2026-02-03-hall"])
    assert "0:07.0 - 0:08.0" in capsys.readouterr().out


def test_mark_rejects_times_outside_wav(data, tmp_path):
    cli.main([*PREP, "--audio", str(data), "--from", "8", "--to", "20"])
    tsv = tmp_path / "m.tsv"
    tsv.write_text("100\t101\n", encoding="utf-8")  # 元の録音の時刻で付けた間違い
    with pytest.raises(SystemExit):
        cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])
    kdir = store.find_koza("2026-02-03-hall")
    assert store.read(kdir / "reactions.json", "reactions")["reactions"][0]["method"] == "auto"


def test_mark_says_excerpt_base_and_removes_stale(data, tmp_path, capsys):
    cli.main([*PREP, "--audio", str(data), "--from", "8", "--to", "20"])
    kdir = store.find_koza("2026-02-03-hall")
    (kdir / "note.md").write_text("古いノート", encoding="utf-8")
    tsv = tmp_path / "m.tsv"
    tsv.write_text("1\t2\n", encoding="utf-8")
    cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])
    assert "抜粋の先頭" in capsys.readouterr().out
    assert not (kdir / "note.md").exists()


def test_force_keeps_a_copy_of_manual_marks(data, tmp_path, monkeypatch):
    cli.main([*PREP, "--audio", str(data)])
    tsv = tmp_path / "m.tsv"
    tsv.write_text("5\t6\n", encoding="utf-8")
    cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])

    def boom(*a, **k):
        raise ValueError("文字起こしに失敗")

    monkeypatch.setattr(speech, "detect", boom)
    with pytest.raises(SystemExit):
        cli.main([*PREP, "--audio", str(data), "--force"])
    kdir = store.find_koza("2026-02-03-hall")
    bak = store.read(kdir / "reactions.bak.json", "reactions")
    assert bak["reactions"][0]["method"] == "manual"


def test_mark_twice_keeps_previous_marks(data, tmp_path):
    cli.main([*PREP, "--audio", str(data)])
    tsv = tmp_path / "m.tsv"
    tsv.write_text("5\t6\n", encoding="utf-8")
    cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])
    tsv.write_text("7\t8\n", encoding="utf-8")
    cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])
    kdir = store.find_koza("2026-02-03-hall")
    assert store.read(kdir / "reactions.manual.bak.json", "reactions")["reactions"][0]["t0"] == 5.0


def test_mark_needs_finished_prep(data, tmp_path):
    cli.main([*PREP, "--audio", str(data)])
    kdir = store.find_koza("2026-02-03-hall")
    (kdir / "koza.json").unlink()
    before = (kdir / "reactions.json").read_text(encoding="utf-8")
    tsv = tmp_path / "m.tsv"
    tsv.write_text("5\t6\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["mark", "--koza", "2026-02-03-hall", "--tsv", str(tsv)])
    assert (kdir / "reactions.json").read_text(encoding="utf-8") == before


def test_force_works_on_broken_reactions(data):
    cli.main([*PREP, "--audio", str(data)])
    kdir = store.find_koza("2026-02-03-hall")
    (kdir / "reactions.json").write_text("{壊れた", encoding="utf-8")
    cli.main([*PREP, "--audio", str(data), "--force"])
    assert store.read(kdir / "reactions.json", "reactions")["reactions"]


def test_prep_keeps_role_costs_in_run_json(data):
    cli.main([*PREP, "--audio", str(data)])
    kdir = store.find_koza("2026-02-03-hall")
    run = store.read(kdir / "run.json", "run")
    run["total_cost_usd"] = 1.5
    store.write(kdir / "run.json", run, "run")
    cli.main([*PREP, "--audio", str(data)])
    assert store.read(kdir / "run.json", "run")["total_cost_usd"] == 1.5


def test_prep_refuses_when_books_cite_the_koza(data):
    cli.main([*PREP, "--audio", str(data)])
    neta = store.Neta("performer-x", "架空の噺")
    neta.kata.write_text('{"source": "2026-02-03-hall"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main([*PREP, "--audio", str(data)])
    cli.main([*PREP, "--audio", str(data), "--force"])


def test_prep_measures_seconds(data):
    cli.main([*PREP, "--audio", str(data)])
    run = store.read(store.find_koza("2026-02-03-hall") / "run.json", "run")
    assert run["prep"]["audio_seconds"] == pytest.approx(30.0, abs=0.05)
    assert run["prep"]["transcribe_seconds"] >= 0


def test_same_koza_id_on_two_netas(data, tmp_path):
    cli.main([*PREP, "--audio", str(data)])
    other = ["prep", "--performer", "performer-x", "--neta", "別の噺", "--koza", "2026-02-03-hall"]
    cli.main([*other, "--audio", str(data)])
    with pytest.raises(SystemExit):
        cli.main(["top", "--koza", "2026-02-03-hall"])
    cli.main(["top", "--koza", "2026-02-03-hall", "--neta", "別の噺"])


def test_find_koza_takes_id_literally(data, tmp_path):
    cli.main([*PREP, "--audio", str(data)])
    with pytest.raises(store.StoreError):
        store.find_koza("2026-*")


def test_find_koza_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("NETACHO_DATA", str(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["top", "--koza", "2026-02-03-none"])


def test_store_write_validates_before_saving(tmp_path):
    path = tmp_path / "koza.json"
    with pytest.raises(ValueError):
        store.write(path, {"schema_version": 1}, "koza")
    assert not path.exists()


def test_store_write_is_valid_json(tmp_path):
    path = tmp_path / "a" / "timeline.json"
    doc = {"schema_version": 1, "koza_id": "k", "segments": []}
    store.write(path, doc, "timeline")
    validate(json.loads(path.read_text(encoding="utf-8")), "timeline")
