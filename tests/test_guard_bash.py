"""`.claude/hooks/guard-bash.sh` のテスト。

消す・書き換える系は ask、PR 前レビューの印が無い PR 作成は deny、文中の語では止めない。
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
HOOK = ROOT / ".claude" / "hooks" / "guard-bash.sh"
CASES = [
    line.split("\t", 1)
    for line in (Path(__file__).parent / "fixtures" / "guard_bash_cases.tsv").read_text().splitlines()
    if line.strip()
]


def _decide(cmd: str, cwd: Path, hook_cwd: Path | None = None, project: Path | None = None) -> str:
    payload = {"tool_input": {"command": cmd}}
    if hook_cwd is not None:  # Claude Code はフックの入力に cwd を渡す
        payload["cwd"] = str(hook_cwd)
    out = subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project or cwd)},
    ).stdout
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out.strip() else "allow"


@pytest.fixture
def repo(tmp_path):
    """印の無いブランチを持つ使い捨ての git repo（本物の repo の印に左右されない）。"""
    subprocess.run(["git", "init", "-q", "-b", "t-branch", str(tmp_path)], check=True)
    return tmp_path


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq が必要")
@pytest.mark.parametrize(("want", "cmd"), CASES, ids=[c[:40] for _, c in CASES])
def test_guard_bash(repo, want, cmd):
    assert _decide(cmd.replace("\\n", "\n"), repo) == want  # 表の \n は改行（heredoc の例）


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq が必要")
def test_rm_judged_by_location(repo):
    """rm -r / -f は、repo の out/ の下なら承認なし。相対パスはフックの入力の cwd から解決する。"""
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True,
                               text=True, check=True).stdout.strip())  # fmt: skip
    (root / "out").mkdir()
    (root / "src").mkdir()
    assert _decide("rm -rf test_c", repo, root / "out") == "allow"
    assert _decide("rm -rf demo", repo, root / "out") == "ask"
    assert _decide("rm -rf x", repo, root / "src") == "ask"
    assert _decide(f"rm -rf {root}/out/test_c", repo, root) == "allow"
    assert _decide(f"rm -rf {root}/src", repo, root) == "ask"
    (root / "out" / "link").symlink_to(root / "src")  # リンクを通るとリンク先を消す
    assert _decide("rm -rf out/link/x", repo, root) == "ask"
    other = root / "other"  # 別の repo の out/ は対象外
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    (other / "out").mkdir()
    assert _decide("rm -rf out/x", repo, other) == "ask"
    assert _decide("rm -rf out/x", repo, other, project=other) == "allow"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq が必要")
def test_rm_in_repo_under_tmp():
    """/tmp の下の repo でも判定が同じ（macOS は /tmp がリンク、Linux は実体。git の root の形が違う）。"""
    with tempfile.TemporaryDirectory(dir="/tmp") as d:
        subprocess.run(["git", "init", "-q", "-b", "t-branch", d], check=True)
        root = Path(d)
        (root / "out").mkdir()
        assert _decide(f"rm -rf {d}/out/test_c", root) == "allow"
        assert _decide(f"rm -rf {d}/out/demo", root) == "ask"
        assert _decide(f"rm -rf {d}/src", root) == "ask"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq が必要")
def test_rm_through_link_in_claude_tmp_asks(repo):
    """Claude の一時フォルダでも、リンクを通る rm は承認制（OS によらず、リンク先を消すため）。"""
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="claude-test-") as d:
        (Path(d) / "link").symlink_to(repo)
        assert _decide(f"rm -rf {d}/plain/x", repo) == "allow"
        assert _decide(f"rm -rf {d}/link/x", repo) == "ask"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq が必要")
def test_private_tmp_only_where_tmp_links_to_it(repo):
    """/private/tmp を一時フォルダとみなすのは、/tmp がそこへのリンクのとき（macOS）だけ。"""
    linked = os.path.realpath("/tmp") == "/private/tmp"
    want = "allow" if linked else "ask"
    assert _decide("rm -rf /private/tmp/claude-501/abc/scratchpad/gt", repo) == want


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq が必要")
def test_mark_reviewed_unblocks_pr_create(repo):
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], cwd=repo, check=True)
    assert _decide("gh pr create --title x", repo) == "deny"
    on_reviewer = str(ROOT / ".claude" / "hooks" / "on-reviewer.sh")

    def call_agent(kind):
        payload = json.dumps({"tool_input": {"subagent_type": kind, "prompt": "x"}})
        subprocess.run([on_reviewer], input=payload, text=True, cwd=repo, check=True)

    call_agent("general-purpose")  # reviewer 以外を呼んでも印は付かない
    assert _decide("gh pr create --title x", repo) == "deny"
    call_agent("reviewer")
    assert _decide("gh pr create --title x", repo) == "allow"
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "fix"], cwd=repo, check=True)
    assert _decide("gh pr create --title x", repo) == "allow"  # レビュー後に直したコミットは通す
    assert _decide("gh pr merge 1", repo) == "ask"  # 印があってもマージは承認制のまま
    # 同じ名前のブランチを別の履歴で作り直したら、古い印は無効
    subprocess.run(["git", "checkout", "-q", "--orphan", "tmp"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "y"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-q", "-D", "t-branch"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-q", "-m", "t-branch"], cwd=repo, check=True)
    assert _decide("gh pr create --title x", repo) == "deny"
