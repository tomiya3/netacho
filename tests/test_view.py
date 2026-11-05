"""視聴画面の書き出しのテスト。ダミーの一式を使う。"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from netacho import cli, store, view

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "fixtures" / "dummy_koza"
P, N, K = "performer-dummy", "雨宿り勘定", "2026-01-01-dummy-hall"
HTML = (ROOT / "viewer" / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def doc():
    return view.payload(store.Neta(P, N, DATA), K)


def test_payload_has_everything_for_four_screens(doc):
    for key in ("koza", "reactions", "candidates", "hanashi", "kata", "guide", "review", "questions", "run"):
        assert doc[key], key
    assert doc["reactions"]["scores"]["laugh"]
    assert doc["candidates"]["candidates"][0]["note"]


def test_items_know_their_scene(doc):
    by_id = {i["id"]: i for i in doc["hanashi"] + doc["kata"]}
    assert by_id["item-0001"]["scene_id"] == "scene-02"
    assert by_id["item-0009"]["scene_id"] == "scene-01"  # 演者の項目は anchor から


def test_write_goes_to_out_dir(tmp_path):
    path = view.write(store.Neta(P, N, DATA), K, tmp_path / "view")
    assert json.loads(path.read_text(encoding="utf-8"))["koza"]["id"] == K
    assert sorted(p.name for p in path.parent.iterdir()) == ["index.html", "view.json"]  # 録音は出さない


def _two_koza(tmp_path):
    """二席目に出所のある項目を持つ、ダミーの置き場。"""
    import shutil

    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    neta = store.Neta(P, N, root)
    k2 = "2026-03-03-dummy-hall"
    shutil.copytree(neta.koza_dir(K), neta.koza_dir(k2))
    tl = store.read(neta.koza_dir(k2) / "timeline.json", "timeline")
    tl["koza_id"] = k2
    store.write(neta.koza_dir(k2) / "timeline.json", tl, "timeline")
    hanashi = store.read(neta.hanashi, "book")
    hanashi["items"][0]["source"]["koza_id"] = k2
    store.write(neta.hanashi, hanashi, "book")
    return neta, k2


def test_items_of_another_koza_find_their_scene(tmp_path):
    neta, _ = _two_koza(tmp_path)
    doc = view.payload(neta, K)
    assert next(i for i in doc["hanashi"] if i["id"] == "item-0001")["scene_id"] == "scene-02"


def test_held_items_reach_the_screen(doc):
    assert any(i.get("held") for i in doc["kata"])


def test_broken_other_koza_does_not_stop_the_view(tmp_path):
    neta, k2 = _two_koza(tmp_path)
    for broken in ("{壊れた", '{"schema_version": 1}'):
        (neta.koza_dir(k2) / "timeline.json").write_text(broken, encoding="utf-8")
        assert view.payload(neta, K)["koza"]["id"] == K


def test_failed_write_removes_old_view(tmp_path):
    out = tmp_path / "view"
    view.write(store.Neta(P, N, DATA), K, out)
    with pytest.raises(FileNotFoundError):
        view.write(store.Neta(P, "無い噺", DATA), K, out)
    assert not (out / "view.json").exists()


def test_only_out_view_is_served():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "-d out/view" in make and "-d ." not in make


def test_missing_later_files_are_ok(tmp_path):
    src = DATA / P / N
    neta = store.Neta(P, N, tmp_path)
    for name in ("hanashi.json", "kata.json"):
        store.write(neta.base / name, store.read(src / name, "book"), "book")
    kdir = neta.koza_dir(K)
    for name, schema in (("koza.json", "koza"), ("reactions.json", "reactions")):
        store.write(kdir / name, store.read(src / "koza" / K / name, schema), schema)
    doc = view.payload(neta, K)
    assert doc["guide"] is None and doc["review"] is None and doc["candidates"] is None


def test_cli_view_needs_no_anthropic(tmp_path):
    args = ["view", "--performer", P, "--neta", N, "--koza", K, "--out", str(tmp_path)]
    code = f"import sys\nsys.modules['anthropic'] = None\nfrom netacho import cli\ncli.main({args!r})\n"
    env = {**os.environ, "NETACHO_DATA": str(DATA)}
    subprocess.run([sys.executable, "-c", code], check=True, env=env, capture_output=True)
    assert (tmp_path / "view.json").exists()


def test_view_before_build_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setenv("NETACHO_DATA", str(tmp_path))
    with pytest.raises(SystemExit, match="netacho prep"):
        cli.main(["view", "--performer", P, "--neta", N, "--koza", K, "--out", str(tmp_path / "o")])


def test_makefile_binds_to_localhost_only():
    assert "--bind 127.0.0.1" in (ROOT / "Makefile").read_text(encoding="utf-8")


def test_cli_parser_knows_view():
    args = cli.build_parser().parse_args(["view", "--performer", "p", "--neta", "n", "--koza", "k"])
    assert args.func is cli.cmd_view


def test_html_has_four_screens_and_three_labels():
    for word in ("反応の波と候補", "型", "札", "弟子役の指摘と質問"):
        assert word in HTML
    for word in ("Claude の候補", "演者の決定", "スクリプトの転記"):
        assert word in HTML
    assert "手で付けた" in HTML


def test_html_does_not_say_no_reaction():
    plain = HTML.replace("反応が無かったという意味ではありません", "")
    assert "受けなかった" not in plain and "反応なし" not in plain and "反応が無かった" not in plain


def test_html_never_builds_markup_from_data():
    """演者の言葉は textContent で入れる（文字列を HTML として解釈しない）。"""
    assert "innerHTML" not in HTML and not re.search(r"insertAdjacentHTML|document\.write", HTML)


def test_html_loads_nothing_from_outside():
    assert not re.search(r"(src|href)=\"https?://", HTML)
