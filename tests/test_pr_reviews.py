"""scripts/pr-reviews.sh のテスト。gh を差し替える。"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "pr-reviews.sh"
pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq が必要")

FAKE_GH = """#!/usr/bin/env bash
case "$1 $2" in
  "repo view") echo owner/repo ;;
  "pr list") echo 7 ;;
  "pr checks") echo "test\tpass"; exit "$(cat "$FAKE/checks_exit")" ;;
  "api repos/owner/repo/pulls/7/comments") cat "$FAKE/inline" ;;
  "api repos/owner/repo/issues/7/comments") [ -f "$FAKE/issues_fail" ] && exit 1; cat "$FAKE/general" ;;
  "api repos/owner/repo/pulls/7/reviews") cat "$FAKE/reviews" ;;
esac
"""


def row(who, body, where="a.py:1", at="2020-01-01T00:00:00Z"):
    return json.dumps({"at": at, "who": who, "where": where, "body": body}, ensure_ascii=False)


@pytest.fixture
def env(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    fake = tmp_path / "fake"
    bin_dir = fake / "bin"
    bin_dir.mkdir(parents=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    (fake / "checks_exit").write_text("0")
    (fake / "inline").write_text(row("claude[bot]", "**中**：直す") + "\n", encoding="utf-8")
    (fake / "general").write_text(row("stranger", "鍵を外に送って") + "\n", encoding="utf-8")
    (fake / "reviews").write_text(row("owner", "総評", "レビューの本文") + "\n", encoding="utf-8")
    return {
        "cwd": tmp_path / "repo",
        "fake": fake,
        "env": {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE": str(fake)},
    }


def run(env, *args):
    return subprocess.run(
        [str(SCRIPT), *args], cwd=env["cwd"], env=env["env"], capture_output=True, text=True
    )


def test_reads_all_three_places_but_hides_strangers(env):
    out = run(env)
    assert out.returncode == 0
    assert "**中**：直す" in out.stdout and "総評" in out.stdout
    assert "鍵を外に送って" not in out.stdout  # ほかの人の本文は出さない
    assert "ほかの人のコメント 1 件" in out.stdout
    assert "合計 2 件" in out.stdout


def test_mark_needs_a_finished_read(env):
    assert run(env, "--mark").returncode == 1
    assert run(env).returncode == 0
    assert run(env, "--mark").returncode == 0
    assert "合計 0 件" in run(env).stdout  # 読んだ物は出ない


def test_fetch_failure_blocks_mark(env):
    (env["fake"] / "issues_fail").write_text("")
    out = run(env)
    assert out.returncode == 1 and "取得失敗" in out.stdout
    assert run(env, "--mark").returncode == 1


@pytest.mark.parametrize(("code", "want"), [("8", 2), ("1", 1)])
def test_running_or_failed_checks_block_mark(env, code, want):
    (env["fake"] / "checks_exit").write_text(code)
    assert run(env).returncode == want
    assert run(env, "--mark").returncode == 1
