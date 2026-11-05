"""速記と netacho run のテスト。"""

import pytest
from fakes import NO_ISSUES, ScriptedClient
from test_build import K, N, P, draft, draft_item, verdicts

from netacho import cli, pipeline, render, runner, store
from netacho.budget import Budget

KOZA = {"neta": "架空の噺", "id": "k"}


def seg(text, start, end):
    return {"text": text, "start": start, "end": end}


def react(t0, kind="laugh", method="auto"):
    return {"t0": t0, "t1": t0 + 5, "kind": kind, "method": method}


def lines(segs, *rows):
    text = render.sokki_markdown(KOZA, {"segments": segs}, {"reactions": list(rows)})
    return text.splitlines()[4:]


def test_marks_go_after_the_segment():
    got = lines([seg("一", 0, 2), seg("二", 3, 5)], react(1.0), react(4.0, "applause"))
    assert got == ["一（笑い）", "二（拍手）"]


def test_reaction_in_a_gap_goes_to_the_previous_segment():
    """笑いは直前の台詞の後に来る。"""
    assert lines([seg("一", 0, 2), seg("二", 10, 12)], react(5.0)) == ["一（笑い）", "二"]


def test_reaction_before_the_first_segment_is_a_line_of_its_own():
    assert lines([seg("一", 5, 6)], react(1.0)) == ["（笑い）", "一"]


def test_reaction_after_the_end_goes_to_the_last_line():
    assert lines([seg("一", 0, 2)], react(9.0)) == ["一（笑い）"]


def test_close_marks_are_merged_and_far_marks_are_not():
    assert lines([seg("一", 0, 30)], react(1.0), react(3.0), react(4.0, "applause")) == ["一（笑い）（拍手）"]
    assert lines([seg("一", 0, 30)], react(1.0), react(20.0)) == ["一（笑い）（笑い）"]


def test_empty_segment_keeps_its_mark():
    assert lines([seg("", 0, 2), seg("二", 3, 5)], react(1.0)) == ["（笑い）", "二"]


def test_says_unproofed_and_position_is_rough():
    text = render.sokki_markdown(KOZA, {"segments": [seg("一", 0, 2)]}, {"reactions": [react(1.0)]})
    assert "未校正" in text and "目安" in text and "種類も外れる" in text


def test_no_reaction_does_not_read_as_none():
    text = render.sokki_markdown(KOZA, {"segments": []}, {"reactions": []})
    assert "反応が無かったという意味ではありません" in text


def test_manual_is_labelled():
    text = render.sokki_markdown(
        KOZA, {"segments": [seg("一", 0, 2)]}, {"reactions": [react(1.0, "reaction", "manual")]}
    )
    assert "耳で付けた" in text and "一（反応）" in text


def test_note_writes_sokki(data, tmp_path):
    budget = Budget(tmp_path / "ledger.json")
    client = ScriptedClient(writer=[draft(draft_item())], checker=[verdicts(("item-0001", "supported"))])
    pipeline.build(P, N, K, client=client, budget=budget, root=data)
    pipeline.note(P, N, K, client=ScriptedClient(), budget=budget, root=data)
    text = (store.Neta(P, N, data).koza_dir(K) / "sokki.md").read_text(encoding="utf-8")
    assert "未校正" in text and "へえ、ですから骨折り賃をいただきに来ました。" in text
    assert "（笑い）" in text


def test_sokki_survives_a_noter_failure(data, tmp_path):
    budget = Budget(tmp_path / "ledger.json")
    mitate = draft_item(
        kind="見立て",
        book="kata",
        source={"segs": ["seg-0008", "seg-0008"]},
        payoff={"segs": ["seg-0009", "seg-0009"]},
    )
    client = ScriptedClient(writer=[draft(mitate)], checker=[verdicts(("item-0001", "supported"))])
    pipeline.build(P, N, K, client=client, budget=budget, root=data)
    with pytest.raises(IndexError):  # ノート役の返事が無い = 途中で落ちた
        pipeline.note(P, N, K, client=ScriptedClient(noter=[]), budget=budget, root=data)
    assert (store.Neta(P, N, data).koza_dir(K) / "sokki.md").exists()


def test_run_checks_auth_before_long_prep(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "cmd_prep", lambda a: called.append("prep"))

    class NoAuth:
        def __init__(self):
            self.messages = self

        def count_tokens(self, **kw):
            raise TypeError("Could not resolve authentication method")

    monkeypatch.setattr(runner, "make_client", NoAuth)
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        cli.main(["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K])
    assert called == []


def test_run_does_not_hide_other_type_errors(monkeypatch):
    class Bug:
        def __init__(self):
            self.messages = self

        def count_tokens(self, **kw):
            raise TypeError("別のバグ")

    monkeypatch.setattr(runner, "make_client", Bug)
    with pytest.raises(TypeError, match="別のバグ"):
        cli.main(["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K])


def test_run_with_reuse_needs_no_key(monkeypatch):
    calls = []
    for name in ("cmd_prep", "cmd_build", "cmd_note"):
        monkeypatch.setattr(cli, name, lambda a, n=name: calls.append(n))
    monkeypatch.setattr(runner, "check_auth", lambda c: pytest.fail("確かめない"))
    cli.main(["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K, "--reuse"])
    assert len(calls) == 3


def test_mark_on_the_boundary_of_a_segment_goes_to_that_segment():
    assert lines([seg("一", 0, 2), seg("二", 3, 5)], react(3.0)) == ["一", "二（笑い）"]


def test_long_segment_keeps_the_mark_at_its_end():
    assert lines([seg("長い台詞", 0, 25), seg("次", 26, 27)], react(10.0)) == ["長い台詞（笑い）", "次"]


def test_run_reports_other_errors_as_they_are(monkeypatch):
    """ネットワーク断などを認証の問題と言わない。"""

    class Down:
        def __init__(self):
            self.messages = self

        def count_tokens(self, **kw):
            raise ConnectionError("つながらない")

    monkeypatch.setattr(runner, "make_client", Down)
    with pytest.raises(ConnectionError):
        cli.main(["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K])


def test_run_passes_options_to_every_step(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "cmd_prep", lambda a: seen.update(start=a.start, terms=a.terms, force=a.force))
    monkeypatch.setattr(cli, "cmd_build", lambda a: seen.update(build=a.reuse))
    monkeypatch.setattr(cli, "cmd_note", lambda a: seen.update(note=a.reuse))
    monkeypatch.setattr(runner, "make_client", ScriptedClient)
    base = ["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K]
    cli.main([*base, "--from", "3", "--terms", "傘", "--force", "--reuse"])
    assert seen == {"start": 3.0, "terms": "傘", "force": True, "build": True, "note": True}


def test_run_stops_at_first_failure_and_says_where(monkeypatch, capsys):
    calls = []

    def boom(a):
        raise ValueError("build で落ちた")

    monkeypatch.setattr(cli, "cmd_prep", lambda a: calls.append("prep"))
    monkeypatch.setattr(cli, "cmd_build", boom)
    monkeypatch.setattr(cli, "cmd_note", lambda a: calls.append("note"))
    monkeypatch.setattr(runner, "make_client", ScriptedClient)
    with pytest.raises(SystemExit):
        cli.main(["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K])
    assert calls == ["prep"]
    assert "netacho build" in capsys.readouterr().out


def test_run_calls_prep_build_note_in_order(data, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_prep", lambda a: calls.append("prep"))
    monkeypatch.setattr(cli, "cmd_build", lambda a: calls.append("build"))
    monkeypatch.setattr(cli, "cmd_note", lambda a: calls.append("note"))
    monkeypatch.setattr(runner, "make_client", lambda: ScriptedClient(deshi=[NO_ISSUES]))
    cli.main(["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K])
    assert calls == ["prep", "build", "note"]
