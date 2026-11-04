"""視聴画面に渡す JSON を書き出す。LLM は呼ばない。録音は書き出さない。"""

from __future__ import annotations

import json
from pathlib import Path

from . import render, store

OUT = Path(__file__).resolve().parent.parent / "out" / "view"


def _optional(path: Path, schema: str) -> dict | None:
    return store.read(path, schema) if path.exists() else None


def _with_scene(items: list[dict], lookup: dict) -> list[dict]:
    return [{**i, "scene_id": render.scene_of_item(i, lookup)} for i in items]


def payload(neta: store.Neta, koza_id: str) -> dict:
    """1 つの高座を見るのに要る物を 1 つの JSON にまとめる。"""
    kdir = neta.koza_dir(koza_id)
    timeline = _optional(kdir / "timeline.json", "timeline")
    lookup = render.scene_lookup([timeline] if timeline else [])
    hanashi = store.read(neta.hanashi, "book")
    kata = store.read(neta.kata, "book")
    note = kdir / "note.md"
    return {
        "koza": store.read(kdir / "koza.json", "koza"),
        "reactions": store.read(kdir / "reactions.json", "reactions"),
        "candidates": _optional(kdir / "note_candidates.json", "note_candidates"),
        "note": note.read_text(encoding="utf-8") if note.exists() else None,
        "scenes": sorted(hanashi["scenes"], key=lambda s: s["order"]),
        "characters": hanashi["characters"],
        "hanashi": _with_scene(hanashi["items"], lookup),
        "kata": _with_scene(kata["items"], lookup),
        "guide": _optional(neta.guide, "guide"),
        "review": _optional(neta.review, "review"),
        "questions": _optional(neta.questions, "questions"),
        "run": _optional(kdir / "run.json", "run"),
    }


def write(neta: store.Neta, koza_id: str, out: Path = OUT) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "view.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload(neta, koza_id), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path
