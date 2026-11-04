"""速記と netacho run のテスト。"""

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


def test_reaction_in_a_gap_goes_to_the_next_segment():
    assert lines([seg("一", 0, 2), seg("二", 10, 12)], react(5.0)) == ["一", "二（笑い）"]


def test_reaction_after_the_end_goes_to_the_last_line():
    assert lines([seg("一", 0, 2)], react(9.0)) == ["一（笑い）"]


def test_same_mark_is_not_repeated():
    assert lines([seg("一", 0, 10)], react(1.0), react(3.0), react(4.0, "applause")) == ["一（笑い）（拍手）"]


def test_empty_segment_keeps_its_mark():
    assert lines([seg("", 0, 2), seg("二", 3, 5)], react(1.0)) == ["（笑い）", "二"]


def test_says_unproofed_and_position_is_rough():
    text = render.sokki_markdown(KOZA, {"segments": []}, {"reactions": []})
    assert "未校正" in text and "目安" in text


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


def test_run_calls_prep_build_note_in_order(data, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_prep", lambda a: calls.append("prep"))
    monkeypatch.setattr(cli, "cmd_build", lambda a: calls.append("build"))
    monkeypatch.setattr(cli, "cmd_note", lambda a: calls.append("note"))
    monkeypatch.setattr(runner, "make_client", lambda: ScriptedClient(deshi=[NO_ISSUES]))
    cli.main(["run", "--audio", "x", "--performer", P, "--neta", N, "--koza", K])
    assert calls == ["prep", "build", "note"]
