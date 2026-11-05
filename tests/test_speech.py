"""文字起こしの方式のテスト。本物のモデルは動かさない。"""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from netacho.preprocess import speech

AUDIO = np.zeros(16000, dtype=np.float32)


@pytest.fixture(autouse=True)
def pinned_model(monkeypatch):
    seen = []

    def download(repo_id, revision):
        seen.append((repo_id, revision))
        return "/tmp/モデル"

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    return seen


def fake_mlx(calls):
    def transcribe(audio, **kw):
        calls.append(kw)
        return {
            "language": "ja",
            "segments": [
                {
                    "start": np.float64(0.0),
                    "end": np.float64(2.5),
                    "text": " 一席お付き合いを ",
                    "no_speech_prob": 0.1,
                    "words": [{"start": 0.0, "end": 2.5, "word": "一席", "probability": 0.91}],
                },
                {"start": 2.5, "end": 3.0, "text": "  ", "no_speech_prob": 0.9, "words": []},
                {"start": 3.0, "end": 4.0, "text": "願います", "words": None},
            ],
        }

    return SimpleNamespace(transcribe=transcribe)


def test_mlx_result_has_the_same_shape(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx(calls))
    doc = speech.transcribe(AUDIO, terms=["傘", "骨"], model_size=speech.MLX_MODEL)
    assert doc["model"] == f"{speech.MLX_MODEL}@{speech.MLX_REVISION}" and doc["language"] == "ja"
    first, second = doc["segments"]
    assert first["id"] == "seg-0001" and first["text"] == "一席お付き合いを"
    assert first["words"] == [{"start": 0.0, "end": 2.5, "word": "一席", "p": 0.91}]
    assert first["pause_after"] == 0.5 and second["pause_after"] is None
    assert second["words"] == [] and second["no_speech_prob"] == 0.0
    assert type(first["start"]) is float  # numpy の型を JSON に残さない
    assert calls[0]["initial_prompt"] == "傘、骨。" and calls[0]["path_or_hf_repo"] == "/tmp/モデル"
    assert calls[0]["language"] == "ja" and calls[0]["condition_on_previous_text"] is False


def test_empty_lines_are_dropped(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx([]))
    doc = speech.transcribe(AUDIO, model_size=speech.MLX_MODEL)
    assert [s["id"] for s in doc["segments"]] == ["seg-0001", "seg-0002"]


def test_default_model_follows_what_is_installed(monkeypatch):
    monkeypatch.setattr(speech, "find_spec", lambda name: object())
    assert speech.default_model() == speech.MLX_MODEL
    monkeypatch.setattr(speech, "find_spec", lambda name: None)
    assert speech.default_model() == speech.DEFAULT_MODEL


def test_cpu_path_is_used_for_other_models(monkeypatch):
    seen = []
    monkeypatch.setattr(speech, "_transcribe_cpu", lambda audio, terms, model: seen.append(model) or {})
    speech.transcribe(AUDIO, model_size="medium")
    assert seen == ["medium"]


def test_model_version_is_pinned(monkeypatch, pinned_model):
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx([]))
    speech.transcribe(AUDIO, model_size=speech.MLX_MODEL)
    assert pinned_model == [(speech.MLX_MODEL, speech.MLX_REVISION)]


def test_empty_lines_are_dropped_on_the_cpu_path_too():
    rows = [speech._row(0.0, 1.0, " ", 0.0, []), speech._row(1.0, 2.0, "願います", 0.0, [])]
    assert [s["id"] for s in speech._numbered(rows)] == ["seg-0001"]


def test_mlx_failure_falls_back_to_cpu_when_nothing_was_asked_for(monkeypatch, capsys):
    def broken(audio, terms, model):
        raise OSError("GPU が無い")

    seen = []
    monkeypatch.setattr(speech, "default_model", lambda: speech.MLX_MODEL)
    monkeypatch.setattr(speech, "_transcribe_mlx", broken)
    monkeypatch.setattr(speech, "_transcribe_cpu", lambda audio, terms, model: seen.append(model) or {})
    speech.transcribe(AUDIO)
    assert seen == [speech.DEFAULT_MODEL] and "faster-whisper" in capsys.readouterr().err


def test_a_requested_model_does_not_fall_back(monkeypatch):
    def broken(audio, terms, model):
        raise OSError("GPU が無い")

    monkeypatch.setattr(speech, "_transcribe_mlx", broken)
    with pytest.raises(OSError):
        speech.transcribe(AUDIO, model_size=speech.MLX_MODEL)
