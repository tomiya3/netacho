"""AST（Audio Spectrogram Transformer）で、窓ごとの笑い・拍手・音楽のスコアを取る。

モデルは AudioSet で学習した MIT/ast-finetuned-audioset-10-10-0.4593。多ラベルなので sigmoid を取る。
このスコアは絶対値では判定できない（笑いは最大でも約 0.3）。判定は reactions.py が高座の中の山で行う。
5 秒の窓は、モデルの入力の長さ（10.24 秒）まで詰め物をして見る。学習時とは条件が違う。
"""

from __future__ import annotations

import os
from collections.abc import Callable

import numpy as np

MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
REVISION = "f826b80d28226b62986cc218e5cec390b1096902"  # 実測を再現できるように版を固定する
SAMPLE_RATE = 16000
WINDOW_SEC = 5.0
STRIDE_SEC = 2.0
BATCH = 16
GROUPS = {
    "laugh": ("Laughter", "Giggle", "Chuckle, chortle", "Belly laugh"),
    "applause": ("Applause", "Clapping"),
    "music": ("Music",),
}

Predictor = Callable[[list[np.ndarray]], dict[str, np.ndarray]]


def window_starts(samples: int, *, sr: int = SAMPLE_RATE) -> list[int]:
    """窓の始まり（サンプル）。末尾の拍手を落とさないよう、途中で切れる窓も 1 つ取る。"""
    window, stride = round(WINDOW_SEC * sr), round(STRIDE_SEC * sr)
    if samples == 0:
        return []
    return [0, *range(stride, max(samples - window + stride, 1), stride)]


def load_predictor() -> Predictor:
    """モデルを読み込む。torch はここでだけ読み込む（札などの経路に持ち込まない）。"""
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # mps に無い演算は CPU で回す
    import torch
    from transformers import ASTForAudioClassification, AutoFeatureExtractor

    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    extractor = AutoFeatureExtractor.from_pretrained(MODEL, revision=REVISION)
    model = ASTForAudioClassification.from_pretrained(MODEL, revision=REVISION).to(device).eval()
    label_ids = {v: int(k) for k, v in model.config.id2label.items()}
    indices = {name: [label_ids[x] for x in labels] for name, labels in GROUPS.items()}

    def predict(batch: list[np.ndarray]) -> dict[str, np.ndarray]:
        inputs = extractor(batch, sampling_rate=SAMPLE_RATE, return_tensors="pt")
        with torch.inference_mode():
            logits = model(inputs["input_values"].to(device)).logits
        probs = torch.sigmoid(logits).cpu().numpy()
        return {name: probs[:, ids].max(axis=1) for name, ids in indices.items()}

    return predict


def score(audio: np.ndarray, *, predict: Predictor | None = None) -> dict:
    """窓ごとのスコア。{"window", "stride", "model", "laugh": [...], "applause": [...], "music": [...]}。"""
    predict = predict or load_predictor()
    window = round(WINDOW_SEC * SAMPLE_RATE)
    starts = window_starts(len(audio))
    out: dict[str, list[float]] = {name: [] for name in GROUPS}
    for i in range(0, len(starts), BATCH):
        batch = [audio[s : s + window] for s in starts[i : i + BATCH]]
        result = predict(batch)
        for name in GROUPS:
            out[name] += [round(float(x), 4) for x in result[name]]
    return {"model": f"{MODEL}@{REVISION}", "window": WINDOW_SEC, "stride": STRIDE_SEC, **out}
