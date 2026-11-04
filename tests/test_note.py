"""netacho note のテスト。"""

import json

import pytest
from fakes import ScriptedClient, text_of
from test_build import (
    K,
    N,
    P,
    draft,
    draft_item,
    verdicts,
)

from netacho import pipeline, store
from netacho.budget import Budget


def mitate():
    return draft_item(
        kind="見立て",
        book="kata",
        text="雨を行列に見立てる",
        source={"segs": ["seg-0008", "seg-0008"]},
        payoff={"segs": ["seg-0009", "seg-0009"]},
    )


def built(data, tmp_path):
    client = ScriptedClient(writer=[draft(mitate())], checker=[verdicts(("item-0001", "supported"))])
    pipeline.build(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)


def run_note(data, tmp_path, *notes):
    client = ScriptedClient(noter=list(notes))
    budget = Budget(tmp_path / "ledger.json")
    return client, pipeline.note(P, N, K, client=client, budget=budget, root=data)


def note_doc(*pairs):
    return {"schema_version": 1, "notes": [{"candidate_id": c, "text": t} for c, t in pairs]}


def test_note_writes_candidates_and_markdown(data, tmp_path):
    built(data, tmp_path)
    _, run = run_note(data, tmp_path, note_doc(("cand-0001", "見立ての回収の後に、反応候補が少ない所です。")))
    kdir = store.Neta(P, N, data).koza_dir(K)
    cands = store.read(kdir / "note_candidates.json", "note_candidates")
    assert cands["candidates"][0]["why"] == "no_candidate"
    text = (kdir / "note.md").read_text(encoding="utf-8")
    assert "見立ての回収の後に、反応候補が少ない所です。" in text
    assert "受けなかったという意味ではありません" in text
    assert "行列なら頭を下げて待つしかねえな。" in text  # 根拠が確かめられる
    assert run["notes"] == {"candidates": 1, "fallback": 0}
    assert {r["name"] for r in run["roles"]} == {"writer", "checker", "noter"}  # build の分に積み足す


def test_noter_sees_only_candidate_quotes_and_scene_length(data, tmp_path):
    built(data, tmp_path)
    client, _ = run_note(data, tmp_path, note_doc(("cand-0001", "文。")))
    text = text_of(client.calls["noter"][0])
    assert "react-" not in text and "strength" not in text
    rows = json.loads(text)
    assert set(rows[0]) == {
        "candidate_id",
        "kind",
        "item",
        "why",
        "setup_quote",
        "payoff_quote",
        "after_quote",
        "scene",
        "scene_seconds",
    }
    assert rows[0]["scene"] == "傘を借りる"
    assert "ご隠居さん、傘を返しに来ました。" in rows[0]["after_quote"]  # 窓の中の台詞
    assert "骨折り賃" not in text  # 窓の外は見せない


@pytest.mark.parametrize(
    "bad",
    [
        note_doc(("cand-0001", "ここは受けなかったようです。")),
        note_doc(("cand-0001", "反応なしでした。")),
        note_doc(("cand-0001", "")),
        note_doc(("cand-9999", "別の候補")),
        note_doc(("cand-0001", "一つ目"), ("cand-0001", "二つ目")),
        note_doc(),
    ],
)
def test_bad_note_is_replaced_and_counted(data, tmp_path, bad):
    built(data, tmp_path)
    _, run = run_note(data, tmp_path, bad)
    assert run["notes"]["fallback"] == 1
    text = (store.Neta(P, N, data).koza_dir(K) / "note.md").read_text(encoding="utf-8")
    assert pipeline.NOTE_FALLBACK["no_candidate"] in text
    assert "反応なしでした" not in text


def test_no_candidates_skips_noter_and_says_so(data, tmp_path):
    built(data, tmp_path)
    kdir = store.Neta(P, N, data).koza_dir(K)
    doc = store.read(kdir / "reactions.json", "reactions")
    doc["reactions"] = [{**doc["reactions"][0], "t0": 45.5, "t1": 46.0}]
    store.write(kdir / "reactions.json", doc, "reactions")
    client, run = run_note(data, tmp_path)
    assert client.calls["noter"] == []
    assert run["notes"]["candidates"] == 0
    assert "反応が十分だったという意味ではありません" in (kdir / "note.md").read_text(encoding="utf-8")
