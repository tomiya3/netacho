"""画面から入れた録音の処理の段取り。偽のコマンドで回す。API も Whisper も呼ばない。"""

from pathlib import Path

import pytest

from netacho import jobs


@pytest.mark.parametrize(
    ("neta", "when", "venue", "expected"),
    [
        ("傘貸し長屋", "2026-03-04", "hall-a", ("傘貸し長屋", "2026-03-04-hall-a")),
        (" 傘貸し長屋 ", "", "公民館", ("傘貸し長屋", "0000-00-00-公民館")),
    ],
)
def test_new_koza_makes_the_folder_names(neta, when, venue, expected):
    assert jobs.new_koza(neta, when, venue) == expected


@pytest.mark.parametrize(
    ("neta", "when", "venue"),
    [
        ("", "", "hall"),
        ("時 そば", "", "hall"),
        ("../x", "", "hall"),
        (".hidden", "", "hall"),
        ("傘貸し長屋", "", "a/b"),
        ("傘貸し長屋", "", "x" * 41),
        ("傘貸し長屋", "2026-13-01", "hall"),
        ("傘貸し長屋", "20260301", "hall"),
        ("傘貸し長屋", "きのう", "hall"),
    ],
)
def test_new_koza_refuses_bad_names_and_dates(neta, when, venue):
    with pytest.raises(jobs.JobError):
        jobs.new_koza(neta, when, venue)


def test_audio_suffix():
    assert jobs.audio_suffix("高座.M4A") == ".m4a"
    with pytest.raises(jobs.JobError):
        jobs.audio_suffix("memo.txt")
    with pytest.raises(jobs.JobError):
        jobs.audio_suffix("noext")


def test_parse_terms():
    assert jobs.parse_terms(" 裏長屋, テスト太郎、 ,八っつぁん") == "裏長屋,テスト太郎,八っつぁん"
    assert jobs.parse_terms("") == ""
    with pytest.raises(jobs.JobError):
        jobs.parse_terms("a\nb")
    with pytest.raises(jobs.JobError):
        jobs.parse_terms("あ" * 501)


def job(terms="テスト太郎"):
    return jobs.Job("performer-x", "傘貸し長屋", "2026-03-04-hall", Path("/data/source.m4a"), terms)


class Fake:
    def __init__(self, fail_at=None):
        self.calls, self.fail_at = [], fail_at

    def __call__(self, argv, log):
        self.calls.append(argv)
        return 1 if argv[0] == self.fail_at else 0


def test_job_runs_every_stage_in_order(tmp_path):
    j, fake = job(), Fake()
    j.run(fake, tmp_path / "job.log")
    who = ["--performer", "performer-x", "--neta", "傘貸し長屋", "--koza", "2026-03-04-hall"]
    assert fake.calls == [
        ["auth"],
        ["prep", *who, "--audio", "/data/source.m4a", "--terms=テスト太郎"],
        ["build", *who],
        ["note", *who],
        ["seisho", *who, "--title", "傘貸し長屋", "--terms=テスト太郎"],
    ]
    status = j.status()
    assert status["state"] == "done" and status["code"] == 0
    assert all(s["state"] == "done" and s["seconds"] is not None for s in status["stages"])


def test_job_without_terms_passes_no_terms(tmp_path):
    j, fake = job(terms=""), Fake()
    j.run(fake, tmp_path / "job.log")
    assert not any("--terms" in argv for argv in fake.calls)


def test_job_stops_at_the_failed_stage_and_retries_from_there(tmp_path):
    j, fake = job(), Fake(fail_at="note")
    j.run(fake, tmp_path / "job.log")
    status = j.status()
    assert status["state"] == "failed" and status["failed"] == "ノートと予測の比べ" and status["code"] == 1
    assert [s["state"] for s in status["stages"]] == ["done", "done", "done", "failed", "waiting"]
    j.reset_failed()
    again = Fake()
    j.run(again, tmp_path / "job.log", retry=True)
    assert [argv[0] for argv in again.calls] == ["note", "seisho"]
    assert all(argv[-1] == "--reuse" for argv in again.calls)
    assert j.status()["state"] == "done"


def test_retry_does_not_reuse_for_prep(tmp_path):
    j = job()
    assert "--reuse" not in j.argv(1, reuse=True)
    assert "--reuse" not in j.argv(0, reuse=True)


def test_a_crashing_command_is_a_failure_without_its_message(tmp_path):
    def boom(argv, log):
        raise RuntimeError("演者の言葉を含むかもしれない")

    j = job()
    j.run(boom, tmp_path / "job.log")
    status = j.status()
    assert status["state"] == "failed" and status["code"] == -1
    assert "演者" not in str(status)


def test_runner_takes_one_job_at_a_time(tmp_path):
    runner = jobs.Runner(Fake())
    runner.reserve()
    with pytest.raises(jobs.JobError):
        runner.reserve()
    runner.start(job(), tmp_path).join()
    assert not runner.busy
    runner.reserve()


def test_run_command_writes_the_log(tmp_path):
    log = tmp_path / "job.log"
    code = jobs.run_command(["--help"], log)
    assert code == 0
    assert "$ netacho --help" in log.read_text(encoding="utf-8")


def test_job_with_a_partner_compares_last(tmp_path):
    partner = ("雨宿り", "2026-01-01-x")
    j = jobs.Job("performer-x", "傘貸し長屋", "2026-03-04-hall", Path("/a.m4a"), "", compare=partner)
    fake = Fake()
    j.run(fake, tmp_path / "job.log")
    assert fake.calls[-1] == [
        "compare",
        "--performer",
        "performer-x",
        "--neta",
        "傘貸し長屋",
        "--koza",
        "2026-03-04-hall",
        "--with-neta",
        "雨宿り",
        "--with-koza",
        "2026-01-01-x",
        "--title",
        "傘貸し長屋",
    ]
    assert j.status()["stages"][-1]["name"] == "前の高座と比べる"
    assert j.status()["compare"] == ["雨宿り", "2026-01-01-x"]


def test_names_starting_with_a_dash_are_refused():
    with pytest.raises(jobs.JobError):
        jobs.new_koza("-x", "", "hall")
    with pytest.raises(jobs.JobError):
        jobs.new_koza("傘貸し長屋", "", "-hall")


def test_terms_starting_with_a_dash_stay_one_argument(tmp_path):
    j = jobs.Job("performer-x", "傘貸し長屋", "2026-03-04-hall", Path("/a.m4a"), "-foo,bar")
    assert "--terms=-foo,bar" in j.argv(1, reuse=False)
    assert "--terms=-foo,bar" in j.argv(4, reuse=False)
