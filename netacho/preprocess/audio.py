"""ffmpeg で音声を 16kHz モノラルの wav にする。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def extract_wav(media: Path, wav: Path, *, start: float | None = None, end: float | None = None) -> Path:
    """16kHz mono の wav を作る（VAD と Whisper の入力）。start / end を渡すと抜粋を切り出す。"""
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(media)]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    subprocess.run([*cmd, "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    return wav


def duration(media: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(media)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return round(float(json.loads(out)["format"]["duration"]), 3)
