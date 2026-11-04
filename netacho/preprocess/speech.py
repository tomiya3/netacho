"""VAD と文字起こし。どちらも faster-whisper 同梱のものを使う。

VAD（Silero）の発話区間と、Whisper の区間は別物として両方残す。
Whisper の区間には固定の ID（seg-0001）と、次の区間までの間（pause_after）を付ける。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
DEFAULT_MODEL = "medium"


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


def transcribe(audio: np.ndarray, *, terms: list[str] | None = None, model_size: str = DEFAULT_MODEL) -> dict:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # 演目の語を initial_prompt に入れると固有名詞の書き起こしが安定する
    prompt = "、".join(terms) + "。" if terms else None
    segments, info = model.transcribe(
        audio,
        language="ja",
        initial_prompt=prompt,
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
        out.append(
            {
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "text": s.text.strip(),
                "no_speech_prob": round(s.no_speech_prob, 3),
                "words": words,
                "source": "asr",
            }
        )
    return {"model": model_size, "language": info.language, "segments": number_segments(out)}


def detect(wav: Path, *, terms: list[str] | None = None, model_size: str = DEFAULT_MODEL) -> dict:
    audio = load_wav(wav)
    return {
        "speech": {"segments": vad_segments(audio)},
        "transcript": transcribe(audio, terms=terms, model_size=model_size),
    }
