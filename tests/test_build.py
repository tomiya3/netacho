"""netacho build の流れのテスト。偽の client で、役の境界とマージの約束を確かめる。"""

import json
import shutil
from pathlib import Path

import pytest
from fakes import ScriptedClient, text_of

from netacho import pipeline, runner, store
from netacho.budget import Budget, BudgetError

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


SCENES = [
    {"id": "scene-01", "title": "枕", "order": 1, "segs": ["seg-0001", "seg-0003"]},
    {"id": "scene-02", "title": "傘を借りる", "order": 2, "segs": ["seg-0004", "seg-0009"]},
    {"id": "scene-03", "title": "傘を返す", "order": 3, "segs": ["seg-0010", "seg-0012"]},
]
CHARS = [{"id": "char-01", "name": "太助", "note": "傘を借りる男"}]


def draft_item(**kw):
    base = {
        "id": None,
        "kind": "場面",
        "book": "hanashi",
        "priority": 1,
        "text": "太助がご隠居の家へ傘を借りに行く",
        "basis": "heard",
        "source": {"segs": ["seg-0004", "seg-0006"]},
        "payoff": None,
    }
    return {**base, **kw}


def draft(*items, scenes=SCENES):
    return {"schema_version": 1, "scenes": scenes, "characters": CHARS, "items": list(items)}


def verdicts(*pairs):
    rows = [{"item_id": i, "verdict": v, "reason": "理由"} for i, v in pairs]
    return {"schema_version": 1, "verdicts": rows}


def build(client, root, tmp_path, **kw):
    budget = Budget(tmp_path / "ledger.json")
    return pipeline.build(P, N, K, client=client, budget=budget, root=root, **kw)


def books(root):
    neta = store.Neta(P, N, root)
    return store.read(neta.hanashi, "book"), store.read(neta.kata, "book")


ITEMS = [
    draft_item(),
    draft_item(
        kind="枕",
        book="kata",
        text="番傘が高かったことを枕で説明する",
        source={"segs": ["seg-0002", "seg-0003"]},
    ),
    draft_item(
        kind="見立て",
        book="kata",
        text="雨を行列に見立てる",
        source={"segs": ["seg-0008", "seg-0008"]},
        payoff={"segs": ["seg-0009", "seg-0009"]},
    ),
    draft_item(
        kind="知らない物",
        book="hanashi",
        basis="knowledge",
        text="番傘は竹と油紙の傘",
        source={"segs": ["seg-0002", "seg-0002"]},
    ),
    draft_item(source={"segs": ["seg-9999", "seg-9999"]}),  # 検証で落ちる
]


def test_build_writes_books_and_counts(data, tmp_path):
    client = ScriptedClient(
        writer=[draft(*ITEMS)],
        checker=[verdicts(("item-0001", "supported"), ("item-0002", "weak"), ("item-0003", "unsupported"))],
    )
    doc = build(client, data, tmp_path)
    assert doc["evidence"] == {"kept": 4, "dropped": {"bad_seg": 1}}
    assert doc["checker"] == {"supported": 1, "weak": 1, "unsupported": 1, "unchecked_knowledge": 1}
    assert {r["name"]: r["calls"] for r in doc["roles"]} == {"writer": 1, "checker": 1}
    hanashi, kata = books(data)
    assert [i["id"] for i in hanashi["items"]] == ["item-0001", "item-0004"]
    assert [i["id"] for i in kata["items"]] == ["item-0002", "item-0003"]
    assert kata["items"][1]["held"] is True
    assert hanashi["scenes"] == kata["scenes"]
    neta = store.Neta(P, N, data)
    run = store.read(neta.koza_dir(K) / "run.json", "run")
    assert run["evidence"]["kept"] == 4
    assert (neta.base / "drafts" / "writer.json").exists()
    tl = store.read(neta.koza_dir(K) / "timeline.json", "timeline")
    assert tl["segments"][0]["scene_id"] == "scene-01"


def test_markdown_hides_held_and_marks_knowledge(data, tmp_path):
    client = ScriptedClient(
        writer=[draft(*ITEMS)],
        checker=[verdicts(("item-0001", "supported"), ("item-0002", "weak"), ("item-0003", "unsupported"))],
    )
    build(client, data, tmp_path)
    neta = store.Neta(P, N, data)
    kata_md = neta.kata.with_suffix(".md").read_text(encoding="utf-8")
    assert "番傘が高かった" in kata_md
    assert "雨を行列に見立てる" not in kata_md  # 保留は出さない
    hanashi_md = neta.hanashi.with_suffix(".md").read_text(encoding="utf-8")
    assert "番傘は竹と油紙の傘（暫定）" in hanashi_md
    assert "`scene-02` 傘を借りる" in hanashi_md


def test_writer_never_sees_reactions(data, tmp_path):
    client = ScriptedClient(writer=[draft(draft_item())], checker=[verdicts(("item-0001", "supported"))])
    build(client, data, tmp_path)
    text = text_of(client.calls["writer"][0])
    assert "react-" not in text and "strength" not in text and "反応" not in text
    assert "seg-0003 傘を貸すというのは、お金を貸すのと同じでございます。（間 1.6 秒）" in text
    first = client.calls["writer"][0]["messages"][0]["content"][0]
    assert first["cache_control"] == {"type": "ephemeral"}  # transcript を先頭に
    assert "seg-0001" in first["text"]


def test_checker_sees_only_text_and_quotes(data, tmp_path):
    client = ScriptedClient(writer=[draft(*ITEMS[:3])], checker=[verdicts()])
    build(client, data, tmp_path)
    call = client.calls["checker"][0]
    text = text_of(call)
    assert N not in text  # 演目名を見せない
    assert "seg-" not in text and "koza" not in text
    assert "おう太助か" in text  # 引用はある
    assert "殿様の行列" in text and "頭を下げて" in text  # 仕込みと回収の両方
    rows = json.loads(text)
    assert set(rows[0]) == {"item_id", "text", "quotes"}
    assert "seg-0012" not in text and "骨折り賃" not in text  # 引用の外の transcript は見せない


def test_missing_verdict_is_held(data, tmp_path):
    client = ScriptedClient(writer=[draft(draft_item())], checker=[verdicts()])
    doc = build(client, data, tmp_path)
    assert doc["checker"]["unsupported"] == 1
    assert books(data)[0]["items"][0]["held"] is True


def test_knowledge_only_skips_checker(data, tmp_path):
    client = ScriptedClient(writer=[draft(ITEMS[3])])
    doc = build(client, data, tmp_path)
    assert client.calls["checker"] == []
    assert doc["checker"]["unchecked_knowledge"] == 1


def _performer_item():
    return {
        "id": "item-0002",
        "kind": "staging",
        "priority": 1,
        "text": "枕で番傘の値段を説明する",
        "basis": "heard",
        "source": {
            "speaker": P,
            "recorded_at": "2026-01-02T10:00:00+09:00",
            "words": "ここは枕で番傘の値段を言っておきたい",
            "approved": True,
        },
        "edited_by": "performer",
        "history": [],
        "anchor": {"scene_id": "scene-01", "position": "before"},
        "card_text": "番傘は高価な品でした。",
        "reason": "利息の話が通じないから",
        "approved": True,
    }


def test_rebuild_keeps_performer_items_and_ids(data, tmp_path):
    first = ScriptedClient(writer=[draft(draft_item())], checker=[verdicts(("item-0001", "supported"))])
    build(first, data, tmp_path)
    neta = store.Neta(P, N, data)
    hanashi, kata = books(data)
    kata["items"].append(_performer_item())
    store.write(neta.kata, kata, "book")

    second = ScriptedClient(
        writer=[
            draft(
                draft_item(id="item-0001", text="太助が傘を借りに行く（直した）"),
                draft_item(id="item-0002", kind="枕", book="kata", text="演者の項目を書き換えようとした"),
                draft_item(text="新しい項目"),
            )
        ],
        checker=[verdicts(("item-0001", "supported"), ("item-0003", "supported"))],
    )
    doc = build(second, data, tmp_path)
    assert doc["evidence"]["dropped"] == {"performer_item": 1}
    hanashi, kata = books(data)
    assert kata["items"] == [_performer_item()]  # 演者の項目はそのまま
    assert [i["id"] for i in hanashi["items"]] == ["item-0001", "item-0003"]  # id は変えない、続きを振る
    fixed = hanashi["items"][0]
    assert fixed["text"] == "太助が傘を借りに行く（直した）"
    assert fixed["history"][0]["before"] == "太助がご隠居の家へ傘を借りに行く"
    assert "演者の項目" in text_of(second.calls["writer"][0]) or "performer" in text_of(
        second.calls["writer"][0]
    )


def test_rebuild_with_new_scene_keeps_anchor_of_old_scene(data, tmp_path):
    """下書きから場面が消えても、演者の項目が指す場面は残る。"""
    first = ScriptedClient(writer=[draft(draft_item())], checker=[verdicts(("item-0001", "supported"))])
    build(first, data, tmp_path)
    neta = store.Neta(P, N, data)
    _, kata = books(data)
    kata["items"].append(_performer_item())
    store.write(neta.kata, kata, "book")
    second = ScriptedClient(
        writer=[draft(draft_item(id="item-0001"), scenes=SCENES[1:])],
        checker=[verdicts(("item-0001", "supported"))],
    )
    build(second, data, tmp_path)
    assert [s["id"] for s in books(data)[1]["scenes"]] == ["scene-01", "scene-02", "scene-03"]


def test_reuse_does_not_call_api_twice(data, tmp_path):
    def client():
        return ScriptedClient(writer=[draft(draft_item())], checker=[verdicts(("item-0001", "supported"))])

    c1 = client()
    build(c1, data, tmp_path)
    # 正本を消して、同じ入力にする
    neta = store.Neta(P, N, data)
    neta.hanashi.unlink()
    neta.kata.unlink()
    c2 = client()
    doc = build(c2, data, tmp_path, reuse=True)
    assert c2.calls["writer"] == [] and c2.calls["checker"] == []
    assert doc["roles"][0]["calls"] == 1  # 費用は前の実行の分を引き継ぐ
    assert (data / ".cache" / "roles").is_dir()


def test_cache_key_changes_with_role_body(data, tmp_path, monkeypatch):
    client = ScriptedClient(
        writer=[draft(draft_item()), draft(draft_item())], checker=[verdicts(("item-0001", "supported"))] * 2
    )
    build(client, data, tmp_path)
    neta = store.Neta(P, N, data)
    neta.hanashi.unlink()
    neta.kata.unlink()
    real = runner.role_prompt
    monkeypatch.setattr(runner, "role_prompt", lambda r: real(r) + "\n追記")
    build(client, data, tmp_path, reuse=True)
    assert len(client.calls["writer"]) == 2


def test_budget_stop_leaves_books_untouched(data, tmp_path):
    ledger = tmp_path / "ledger.json"
    Budget(ledger).record("writer", "claude-opus-5-5", 29.9)
    client = ScriptedClient(writer=[draft(draft_item())])
    with pytest.raises(BudgetError):
        pipeline.build(P, N, K, client=client, budget=Budget(ledger), root=data)
    assert client.calls["writer"] == []
    assert not store.Neta(P, N, data).hanashi.exists()


def test_role_files_have_the_promises():
    writer = runner.role_prompt("writer")
    assert "型は `heard` だけ" in writer
    assert "見せないもの" not in writer  # `---` より下は役に渡さない
    assert "`上下` は録音から分からない" in writer
    checker = runner.role_prompt("checker")
    assert "落語の知識で補いません" in checker
