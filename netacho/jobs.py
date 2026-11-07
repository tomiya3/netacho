"""画面から入れた録音を、段階ごとに netacho のコマンドで回す。同時に動かすのは 1 つだけ。

各段階は別のプロセスで動かす（`python -m netacho ...`）。端末で打つのと同じ道を通るので、
費用の上限（budget.py）も、--reuse のやり直しも、そのまま効く。
画面を閉じても、サーバが動いている間は処理が続く。状態はサーバのメモリに持つ。
出力は高座のフォルダの job.log にだけ書き、応答には段階の名前と終了コードしか出さない
（文字起こしの文や、演者の言葉を応答に出さないため）。
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import store

UNKNOWN_DATE = "0000-00-00"
# フォルダの名前にする。空白、区切り、先頭の . と -（コマンドの引数と取り違える）を許さない
NAME = re.compile(r"[^\s/\\.\-][^\s/\\]{0,39}")
AUDIO_TYPES = (".wav", ".m4a", ".mp3", ".aac", ".flac", ".mp4", ".mov")
MAX_TERMS = 500

# (段階の名前, コマンド, やり直しで --reuse を付けるか)
STAGES = (
    ("鍵の確認", "auth", False),
    ("文字起こしと笑いの検出", "prep", False),
    ("帳面と弟子の問い", "build", True),
    ("ノートと予測の比べ", "note", True),
    ("清書", "seisho", True),
)
COMPARE = ("前の高座と比べる", "compare", True)  # 比べる相手を選んだときだけ、最後に足す


class JobError(ValueError):
    pass


def _name(value: str, what: str) -> str:
    value = value.strip()
    if not NAME.fullmatch(value):
        raise JobError(f"{what}は空白や / を含まず、. や - で始まらない 40 字までで")
    return value


def new_koza(neta: str, when: str, venue: str) -> tuple[str, str]:
    """演目のフォルダ名と高座 ID。公演日が空なら 0000-00-00。"""
    neta = _name(neta, "演目")
    venue = _name(venue, "会場")
    when = when.strip()
    if when:
        try:
            date.fromisoformat(when)
        except ValueError as e:
            raise JobError("公演日は 2026-01-31 の形で") from e
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            raise JobError("公演日は 2026-01-31 の形で")
    return neta, f"{when or UNKNOWN_DATE}-{venue}"


def audio_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in AUDIO_TYPES:
        raise JobError(f"録音は {'、'.join(AUDIO_TYPES)} のどれかで")
    return suffix


def parse_terms(text: str) -> str:
    """演目の語。カンマか読点で区切る。1 行、500 字まで。"""
    if len(text) > MAX_TERMS or "\n" in text or "\r" in text:
        raise JobError(f"語は 1 行、{MAX_TERMS} 字までで")
    return ",".join(w.strip() for w in re.split("[,、]", text) if w.strip())


def run_command(argv: list[str], log: Path) -> int:
    """netacho のコマンドを別のプロセスで動かし、出力は job.log に足す。"""
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n$ netacho {argv[0]}\n")
        f.flush()
        done = subprocess.run([sys.executable, "-m", "netacho", *argv], stdout=f, stderr=subprocess.STDOUT)
    return done.returncode


class Job:
    """1 本の録音の処理。stages は [{name, state, seconds}]。state は waiting|running|done|failed。"""

    def __init__(
        self,
        performer: str,
        neta: str,
        koza_id: str,
        audio: Path,
        terms: str,
        *,
        compare: tuple[str, str] | None = None,
    ) -> None:
        """compare は比べる相手の (演目のフォルダ, 高座 ID)。同じ演者の物。"""
        self.performer, self.neta, self.koza_id = performer, neta, koza_id
        self.audio, self.terms, self.compare = audio, terms, compare
        self.plan = STAGES + ((COMPARE,) if compare else ())
        self.stages = [{"name": name, "state": "waiting", "seconds": None} for name, _, _ in self.plan]
        self.started: float | None = None
        self.code: int | None = None

    @property
    def state(self) -> str:
        states = [s["state"] for s in self.stages]
        if "failed" in states:
            return "failed"
        if all(s == "done" for s in states):
            return "done"
        return "running"

    def argv(self, index: int, *, reuse: bool) -> list[str]:
        command = self.plan[index][1]
        if command == "auth":
            return ["auth"]
        who = ["--performer", self.performer, "--neta", self.neta, "--koza", self.koza_id]
        if command == "prep":
            extra = ["--audio", str(self.audio)] + ([f"--terms={self.terms}"] if self.terms else [])
        elif command == "seisho":  # 語が - で始まっても引数と取り違えないように = でつなぐ
            extra = ["--title", self.neta] + ([f"--terms={self.terms}"] if self.terms else [])
        elif command == "compare" and self.compare:
            extra = ["--with-neta", self.compare[0], "--with-koza", self.compare[1], "--title", self.neta]
        else:
            extra = []
        return [command, *who, *extra] + (["--reuse"] if reuse and self.plan[index][2] else [])

    def run(self, command: Callable[[list[str], Path], int], log: Path, *, retry: bool = False) -> None:
        """待っている段階から順に回す。落ちたらそこで止める。"""
        for i, stage in enumerate(self.stages):
            if stage["state"] == "done":
                continue
            stage["state"], stage["seconds"] = "running", None
            self.started = time.monotonic()
            try:
                code = command(self.argv(i, reuse=retry), log)
            except Exception:  # 落ちた理由は応答に出さない
                code = -1
            stage["seconds"] = round(time.monotonic() - self.started, 1)
            if code != 0:
                stage["state"], self.code = "failed", code
                return
            stage["state"] = "done"
        self.code = 0

    def reset_failed(self) -> None:
        for stage in self.stages:
            if stage["state"] == "failed":
                stage["state"], stage["seconds"] = "waiting", None
        self.code = None

    def status(self) -> dict:
        stages = [dict(s) for s in self.stages]
        for s in stages:
            if s["state"] == "running" and self.started is not None:
                s["seconds"] = round(time.monotonic() - self.started, 1)
        failed = next((s["name"] for s in stages if s["state"] == "failed"), None)
        return {
            "state": self.state,
            "neta": self.neta,
            "koza": self.koza_id,
            "stages": stages,
            "failed": failed,
            "code": self.code,
            "log": f"{self.neta}/koza/{self.koza_id}/job.log",
            "compare": list(self.compare) if self.compare else None,
        }


class Runner:
    """サーバに 1 つ。処理は 1 本ずつ。"""

    def __init__(self, command: Callable[[list[str], Path], int] = run_command) -> None:
        self.command = command
        self.lock = threading.Lock()
        self.job: Job | None = None
        self.busy = False  # 受け取り中も含めて、動いている間は True

    def reserve(self) -> None:
        """録音を受け取る前に枠を取る。大きな録音を受けている間に、2 本目を受けないように。"""
        with self.lock:
            if self.busy:
                raise JobError("別の録音を処理しています。終わってから入れてください")
            self.busy = True

    def release(self) -> None:
        with self.lock:
            self.busy = False

    def start(self, job: Job, kdir: Path, *, retry: bool = False) -> threading.Thread:
        """reserve の後に呼ぶ。終わったら枠を返す。"""
        self.job = job
        log = kdir / "job.log"

        def work() -> None:
            try:
                job.run(self.command, log, retry=retry)
            finally:
                self.release()

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread

    def retry(self, neta: store.Neta) -> threading.Thread:
        job = self.job
        if job is None or job.state != "failed":
            raise JobError("やり直す処理がありません")
        self.reserve()
        try:
            job.reset_failed()
            return self.start(job, neta.sibling(job.neta).koza_dir(job.koza_id), retry=True)
        except Exception:
            self.release()
            raise

    def status(self) -> dict | None:
        return None if self.job is None else self.job.status()
