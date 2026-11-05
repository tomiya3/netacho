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
    assert {r["name"] for r in run["roles"]} == {"writer", "checker", "deshi", "noter"}  # build に積み足す


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


@pytest.mark.parametrize(
    "text",
    [
        "回収の後に、反応候補が少ない所です。",
        "見立ての回収の後は、反応候補が見つからない所です。原音で確かめてください。",
        "サゲの後の反応候補が、この高座の中では小さい方です。",
    ],
)
def test_allowed_note_wording(text):
    assert pipeline.note_is_ok(text)


@pytest.mark.parametrize(
    "text",
    [
        "ここは受け無かったようです。",
        "反応候補が無かった所です。",
        "そこでも反応候補は拾えていません。",
        "反応候補はなかった所です。",
        "反応候補が出なかった所です。",
        "静かだった所です。",
        "反応が薄い所です。",
        "手応えが弱い所です。",
        "回収の後の六秒に候補があまりない所です。",
        "ｳｹなかった所です。",
        "笑いが起きなかった所です。",
        "反応がありませんでした。",
        "無反応でした。",
        "盛り上がらなかった所です。",
        "お客さんは退屈していたようです。",
        "間を長くした方がよいでしょう。",
        "枕で説明してみてはどうでしょう。",
        "回収の後 6 秒に候補がありません。",
        "一文目。二文目。三文目。",
        "",
    ],
)
def test_rejected_note_wording(text):
    assert not pipeline.note_is_ok(text)


def test_note_source_tells_who_wrote_it(data, tmp_path):
    built(data, tmp_path)
    path = store.Neta(P, N, data).koza_dir(K) / "note_candidates.json"
    run_note(data, tmp_path, note_doc(("cand-0001", "回収の後に、反応候補が見つからない所です。")))
    assert store.read(path, "note_candidates")["candidates"][0]["note_source"] == "claude"
    run_note(data, tmp_path, note_doc(("cand-0001", "ここは受けなかった。")))
    assert store.read(path, "note_candidates")["candidates"][0]["note_source"] == "fixed"


def test_failed_note_leaves_no_old_note(data, tmp_path):
    built(data, tmp_path)
    run_note(data, tmp_path, note_doc(("cand-0001", "文です。")))
    kdir = store.Neta(P, N, data).koza_dir(K)
    assert (kdir / "note.md").exists()
    with pytest.raises(IndexError):  # 偽の client の返事が尽きる = API の失敗の代わり
        run_note(data, tmp_path)
    assert not (kdir / "note.md").exists()
    assert not (kdir / "note_candidates.json").exists()  # 文の無い候補を残さない


def test_after_quote_is_capped(data, tmp_path):
    built(data, tmp_path)
    kdir = store.Neta(P, N, data).koza_dir(K)
    doc = store.read(kdir / "transcript.json", "transcript")
    doc["segments"][9]["text"] = "あ" * 500
    store.write(kdir / "transcript.json", doc, "transcript")
    client, _ = run_note(data, tmp_path, note_doc(("cand-0001", "文です。")))
    assert len(json.loads(text_of(client.calls["noter"][0]))[0]["after_quote"]) == pipeline.AFTER_QUOTE_MAX


def test_cost_is_kept_when_noter_output_is_broken(data, tmp_path):
    built(data, tmp_path)
    client = ScriptedClient(noter=[lambda kw: '{"schema_version": 1}'])  # schema に合わない
    with pytest.raises(ValueError):
        pipeline.note(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    run = store.read(store.Neta(P, N, data).koza_dir(K) / "run.json", "run")
    assert "noter" in {r["name"] for r in run["roles"]}


def test_manual_marks_are_shown_in_note(data, tmp_path):
    built(data, tmp_path)
    kdir = store.Neta(P, N, data).koza_dir(K)
    doc = store.read(kdir / "reactions.json", "reactions")
    doc["reactions"] = [{**doc["reactions"][0], "method": "manual", "strength_rank": None}]
    store.write(kdir / "reactions.json", doc, "reactions")
    client, _ = run_note(data, tmp_path)
    assert client.calls["noter"] == []
    assert "手で付けたもの" in (kdir / "note.md").read_text(encoding="utf-8")


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
