"""テストで共有する置き場。"""

import shutil
from pathlib import Path

import pytest

SRC = Path(__file__).parent / "fixtures" / "dummy_koza"
P, N, K = "performer-dummy", "雨宿り勘定", "2026-01-01-dummy-hall"


@pytest.fixture
def data(tmp_path):
    """ダミーの高座だけを置いた、噺・型の無い状態。"""
    root = tmp_path / "data"
    kdir = root / P / N / "koza" / K
    kdir.parent.mkdir(parents=True)
    shutil.copytree(SRC / P / N / "koza" / K, kdir)
    for name in ("timeline.json", "note_candidates.json", "run.json"):
        (kdir / name).unlink()
    return root
