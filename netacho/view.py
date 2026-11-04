"""視聴画面に渡す JSON を書き出す。LLM は呼ばない。録音は書き出さない。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import render, store

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "view"
VIEWER = ROOT / "viewer" / "index.html"


def _optional(path: Path, schema: str) -> dict | None:
    return store.read(path, schema) if path.exists() else None


def _with_scene(items: list[dict], lookup: dict) -> list[dict]:
    return [{**i, "scene_id": render.scene_of_item(i, lookup)} for i in items]


def payload(neta: store.Neta, koza_id: str) -> dict:
    """1 つの高座を見るのに要る物を 1 つの JSON にまとめる。"""
    kdir = neta.koza_dir(koza_id)
    # 正本は演目全体の物。別の高座が出所の項目も場面を引けるように、全高座の timeline を読む
    timelines = [store.read(p, "timeline") for p in sorted((neta.base / "koza").glob("*/timeline.json"))]
    lookup = render.scene_lookup(timelines)
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
    shutil.copy2(VIEWER, out / "index.html")  # 配信は out/view だけ（repo 全体を出さない）
    return path
