"""視聴画面に渡す JSON を書き出す。LLM は呼ばない。録音は書き出さない。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import render, store

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "view"
VIEWER = ROOT / "viewer" / "index.html"


def _optional(path: Path, schema: str) -> dict | None:
    return store.read(path, schema) if path.exists() else None


def _with_scene(items: list[dict], lookup: dict) -> list[dict]:
    return [{**i, "scene_id": render.scene_of_item(i, lookup)} for i in items]


def _timelines(neta: store.Neta, koza_id: str) -> list[dict]:
    """全高座の timeline。作りかけの別の高座があっても、見たい高座は開けるように読み飛ばす。"""
    rows = []
    for path in sorted((neta.base / "koza").glob("*/timeline.json")):
        try:
            rows.append(store.read(path, "timeline"))
        except ValueError:
            if path.parent.name == koza_id:
                raise
            print(f"読み飛ばした：{path.parent.name} の timeline.json", file=sys.stderr)
    return rows


def payload(neta: store.Neta, koza_id: str) -> dict:
    """1 つの高座を見るのに要る物を 1 つの JSON にまとめる。"""
    kdir = neta.koza_dir(koza_id)
    # 正本は演目全体の物。別の高座が出所の項目も場面を引けるように、全高座の timeline を読む
    lookup = render.scene_lookup(_timelines(neta, koza_id))
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
    path.unlink(missing_ok=True)  # 失敗したとき、前の高座の画面を残さない
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload(neta, koza_id), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    shutil.copy2(VIEWER, out / "index.html")  # 配信は out/view だけ（repo 全体を出さない）
    return path
