"""VAD と文字起こし。VAD は faster-whisper 同梱の物。文字起こしは MLX か faster-whisper。

VAD（Silero）の発話区間と、Whisper の区間は別物として両方残す。
Whisper の区間には固定の ID（seg-0001）と、次の区間までの間（pause_after）を付ける。
"""

from __future__ import annotations

import sys
import wave
from importlib.util import find_spec
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
DEFAULT_MODEL = "medium"
MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
MLX_REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"  # 版を固定する。AST と同じ考え


def load_wav(wav: Path) -> np.ndarray:
    """16kHz mono PCM16 の wav を float32 配列に（av に依存しない）。"""
    with wave.open(str(wav), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError("wav は 16kHz モノラル PCM16 のはず")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def vad_segments(audio: np.ndarray, *, min_silence_ms: int = 300, speech_pad_ms: int = 100) -> list[dict]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(min_silence_duration_ms=min_silence_ms, speech_pad_ms=speech_pad_ms)
    segs = get_speech_timestamps(audio, opts, sampling_rate=SAMPLE_RATE)
    return [
        {
            "start": round(s["start"] / SAMPLE_RATE, 3),
            "end": round(s["end"] / SAMPLE_RATE, 3),
            "source": "vad",
        }
        for s in segs
    ]


def number_segments(segments: list[dict]) -> list[dict]:
    """区間に ID と pause_after を付ける。最後の区間の pause_after は None。"""
    for i, s in enumerate(segments):
        s["id"] = f"seg-{i + 1:04d}"
        following = segments[i + 1] if i + 1 < len(segments) else None
        s["pause_after"] = round(max(0.0, following["start"] - s["end"]), 3) if following else None
    return segments


def default_model() -> str:
    """Apple の GPU で動くなら MLX、無ければ CPU の faster-whisper。CI は Linux なので後者になる。"""
    return MLX_MODEL if find_spec("mlx_whisper") else DEFAULT_MODEL


def _prompt(terms: list[str] | None) -> str | None:
    # 演目の語を initial_prompt に入れる。効くのは最初の窓（約 30 秒）だけ。
    # condition_on_previous_text=False だと次の窓で捨てられる。語の直しは清書が担う
    return "、".join(terms) + "。" if terms else None


def _row(start: float, end: float, text: str, no_speech: float, words: list[dict]) -> dict:
    return {
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "text": text.strip(),
        "no_speech_prob": round(float(no_speech), 3),
        "words": words,
        "source": "asr",
    }


def _numbered(rows: list[dict]) -> list[dict]:
    """空の行を除いて番号を振る。方式によって ID の振り方を変えない。"""
    return number_segments([r for r in rows if r["text"]])


def _transcribe_mlx(audio: np.ndarray, terms: list[str] | None, model: str) -> dict:
    import mlx_whisper
    from huggingface_hub import snapshot_download

    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=snapshot_download(repo_id=model, revision=MLX_REVISION),
        language="ja",
        initial_prompt=_prompt(terms),
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    out = []
    for s in result["segments"]:
        words = [
            {
                "start": round(float(w["start"]), 3),
                "end": round(float(w["end"]), 3),
                "word": w["word"],
                "p": round(float(w["probability"]), 2),
            }
            for w in s.get("words") or []
        ]
        out.append(_row(s["start"], s["end"], s["text"], s.get("no_speech_prob") or 0.0, words))
    model_id = f"{model}@{MLX_REVISION}"
    return {"model": model_id, "language": result.get("language") or "ja", "segments": _numbered(out)}


def _transcribe_cpu(audio: np.ndarray, terms: list[str] | None, model_size: str) -> dict:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio,
        language="ja",
        initial_prompt=_prompt(terms),
        vad_filter=False,  # VAD は別に取る
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    out = []
    for s in segments:
        words = [
            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word, "p": round(w.probability, 2)}
            for w in (s.words or [])
        ]
        out.append(_row(s.start, s.end, s.text, s.no_speech_prob, words))
    return {"model": model_size, "language": info.language, "segments": _numbered(out)}


def transcribe(audio: np.ndarray, *, terms: list[str] | None = None, model_size: str | None = None) -> dict:
    model = model_size or default_model()
    if model == MLX_MODEL:
        try:
            return _transcribe_mlx(audio, terms, model)
        except (ImportError, OSError, RuntimeError):  # GPU の無い Mac の VM など。止めずに CPU へ
            if model_size is not None:
                raise
            print("MLX が使えないので faster-whisper で文字起こしする", file=sys.stderr)
            return _transcribe_cpu(audio, terms, DEFAULT_MODEL)
    return _transcribe_cpu(audio, terms, model)


def detect(wav: Path, *, terms: list[str] | None = None, model_size: str | None = None) -> dict:
    audio = load_wav(wav)
    return {
        "speech": {"segments": vad_segments(audio)},
        "transcript": transcribe(audio, terms=terms, model_size=model_size),
    }
