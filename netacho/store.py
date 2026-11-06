"""演者のデータの置き場と読み書き。保存の前に schema で検証する。

置き場は repo の外（`NETACHO_DATA`、既定 `~/netacho-data/`）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .schema import validate


class StoreError(ValueError):
    pass


def data_root() -> Path:
    return Path(os.environ.get("NETACHO_DATA") or "~/netacho-data").expanduser()


def _safe(name: str, what: str) -> str:
    """パスの一成分として使えるか。/ や .. で置き場の外へ出さない。"""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise StoreError(f"{what} に使えない名前: {name!r}")
    return name


class Neta:
    """演者と演目で決まる場所。"""

    def __init__(self, performer: str, neta: str, root: Path | None = None) -> None:
        self.performer = _safe(performer, "演者 ID")
        self.neta = _safe(neta, "演目")
        self.base = (root or data_root()) / self.performer / self.neta

    def sibling(self, neta: str) -> Neta:
        """同じ演者の別の演目フォルダ。2 本を別のフォルダに置くとき。"""
        return Neta(self.performer, neta, self.base.parent.parent)

    def koza_dir(self, koza_id: str) -> Path:
        return self.base / "koza" / _safe(koza_id, "高座 ID")

    @property
    def hanashi(self) -> Path:
        return self.base / "hanashi.json"

    @property
    def kata(self) -> Path:
        return self.base / "kata.json"

    @property
    def questions(self) -> Path:
        return self.base / "questions.json"

    @property
    def review(self) -> Path:
        return self.base / "review" / "deshi.json"

    @property
    def recheck(self) -> Path:
        return self.base / "review" / "recheck.json"

    @property
    def guide(self) -> Path:
        return self.base / "guide" / "guide.json"

    @property
    def drafts(self) -> Path:
        return self.base / "drafts"


def find_koza(
    koza_id: str, root: Path | None = None, *, performer: str | None = None, neta: str | None = None
) -> Path:
    """高座 ID からフォルダを探す。同じ ID が別の演者や演目にあれば、performer と neta で絞る。"""
    _safe(koza_id, "高座 ID")
    base = root or data_root()
    if not base.is_dir():
        raise StoreError(f"高座 {koza_id} のフォルダが無い")
    hits = [
        n / "koza" / koza_id
        for p in sorted(base.iterdir())
        if p.is_dir() and performer in (None, p.name)
        for n in sorted(p.iterdir())
        if n.is_dir() and neta in (None, n.name) and (n / "koza" / koza_id).is_dir()
    ]
    if len(hits) != 1:
        raise StoreError(f"高座 {koza_id} のフォルダが {len(hits)} 個ある（--performer と --neta で絞る）")
    return hits[0]


def read(path: Path, schema: str) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    validate(obj, schema)
    return obj


def write(path: Path, obj: dict, schema: str) -> None:
    """検証してから、途中で落ちても壊れないように置き換える。"""
    validate(obj, schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
