"""演者の判断と札のテスト。見せ場の 3 と 4。LLM を呼ばないことを確かめる。"""

import os
import subprocess
import sys

import pytest
from fakes import NO_ISSUES, ScriptedClient, text_of
from test_build import K, N, P, books, draft, draft_item, verdicts

from netacho import cli, guide, performer, pipeline, store
from netacho.budget import Budget
from netacho.performer import PerformerError
from netacho.schema import RefError, check_refs, validate

WORDS = "ここは枕で番傘の値段を、ちょっと言っておきたいな"
CARD = "番傘は、昔はたいへん高価な品でした。"
WHY = "値段が分からないと利息の話が通じないから"


def mitate():
    return draft_item(
        kind="見立て",
        book="kata",
        text="雨を行列に見立てる",
        source={"segs": ["seg-0008", "seg-0008"]},
        payoff={"segs": ["seg-0009", "seg-0009"]},
    )


@pytest.fixture
def built(data, tmp_path, monkeypatch):
    monkeypatch.setenv("NETACHO_DATA", str(data))
    client = ScriptedClient(writer=[draft(mitate())], checker=[verdicts(("item-0001", "supported"))])
    pipeline.build(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    pipeline.note(
        P,
        N,
        K,
        client=ScriptedClient(noter=[{"schema_version": 1, "notes": []}]),
        budget=Budget(tmp_path / "ledger.json"),
        root=data,
    )
    return store.Neta(P, N, data)


def decide(neta, **kw):
    base = dict(speaker="performer-dummy", words=WORDS, card=CARD, why=WHY, scene="scene-01")
    return performer.decide(neta, **{**base, **kw})


GUIDE = ["guide", "--performer", P, "--neta", N]
DECIDE = [
    "decide",
    "--performer",
    P,
    "--neta",
    N,
    "--speaker",
    "performer-dummy",
    "--words",
    WORDS,
    "--card",
    CARD,
    "--why",
    WHY,
    "--scene",
    "scene-01",
]


def test_one_line_from_performer_changes_the_card(built, capsys):
    cli.main(GUIDE)
    assert store.read(built.guide, "guide")["cards"] == []
    cli.main([*DECIDE, "--candidate", "cand-0001", "--koza", K, "--position", "before"])
    cli.main(GUIDE)
    cards = store.read(built.guide, "guide")["cards"]
    assert [c["text"] for c in cards] == [CARD]
    assert cards[0]["llm_fields"] == {}
    assert cards[0]["copied_fields"]["reason"] == WHY
    assert CARD in capsys.readouterr().out


def test_words_are_stored_verbatim(built):
    item = decide(built, words=" 間は　『こう』やる…！\n", card="札の、文。", why="理由 です")
    kata = store.read(built.kata, "book")
    saved = next(i for i in kata["items"] if i["id"] == item["id"])
    assert saved["source"]["words"] == " 間は　『こう』やる…！\n"
    assert saved["text"] == " 間は　『こう』やる…！\n"
    assert (saved["card_text"], saved["reason"]) == ("札の、文。", "理由 です")
    assert saved["edited_by"] == "performer" and saved["basis"] == "heard"
    assert saved["history"][0]["before"] is None


def test_kata_md_shows_reason_for_the_deshi(built, capsys):
    cli.main(DECIDE)
    text = built.kata.with_suffix(".md").read_text(encoding="utf-8")
    assert WORDS in text and WHY in text and CARD in text


def test_guide_runs_without_anthropic_installed(built):
    """anthropic を読み込めない状態で decide と guide が動く（LLM の呼び出しは経路ごと無い）。"""
    code = (
        "import sys\n"
        "sys.modules['anthropic'] = None\n"
        "from netacho import cli\n"
        f"cli.main({DECIDE!r})\n"
        f"cli.main({GUIDE!r})\n"
        "assert sys.modules['anthropic'] is None\n"
    )
    env = {**os.environ, "NETACHO_DATA": str(built.base.parent.parent)}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=os.getcwd()
    )
    assert out.returncode == 0, out.stderr
    assert "LLM の呼び出し 0 回" in out.stdout
    assert [c["text"] for c in store.read(built.guide, "guide")["cards"]] == [CARD]


def test_guide_module_does_not_import_runner():
    code = (
        "import sys; import netacho.guide; "
        "assert 'netacho.runner' not in sys.modules; assert 'anthropic' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_cards_are_in_scene_order(built):
    decide(built, scene="scene-03", card="三", position="before")
    decide(built, scene="scene-01", card="一の後", position="after")
    decide(built, scene="scene-01", card="一の前", position="before")
    decide(built, scene="scene-02", card="二", position="within")
    hanashi, kata = store.read(built.hanashi, "book"), store.read(built.kata, "book")
    doc = guide.build(hanashi, kata, None)
    validate(doc, "guide")
    assert [c["text"] for c in doc["cards"]] == ["一の前", "一の後", "二", "三"]
    assert [c["order"] for c in doc["cards"]] == [1, 2, 3, 4]
    check_refs(hanashi, kata, guide=doc)


def test_held_and_unresolved_do_not_reach_the_card(built):
    a = decide(built, card="保留")
    b = decide(built, card="指摘つき")
    c = decide(built, card="出る")
    hanashi, kata = store.read(built.hanashi, "book"), store.read(built.kata, "book")
    next(i for i in kata["items"] if i["id"] == a["id"])["held"] = True
    review = {
        "schema_version": 1,
        "performer_id": P,
        "neta": N,
        "issues": [
            {
                "id": "issue-0001",
                "role": "deshi",
                "kind": "missing_step",
                "anchor": {"item_id": b["id"]},
                "attempt": "x",
                "what_is_missing": "y",
                "round": 1,
                "resolved": False,
            }
        ],
    }
    doc = guide.build(hanashi, kata, review)
    assert [x["item_id"] for x in doc["cards"]] == [c["id"]]
    review["issues"][0]["resolved"] = True
    assert b["id"] in [x["item_id"] for x in guide.build(hanashi, kata, review)["cards"]]


def test_tool_items_never_reach_the_card(built):
    hanashi, kata = store.read(built.hanashi, "book"), store.read(built.kata, "book")
    assert kata["items"][0]["edited_by"] == "tool"
    assert guide.build(hanashi, kata, None)["cards"] == []


def test_regeneration_keeps_performer_item_and_card(built, data, tmp_path):
    item = decide(built, scene="scene-01")
    cli.main(GUIDE)
    before = (built.base / "guide" / "guide.json").read_text(encoding="utf-8")
    client = ScriptedClient(
        writer=[
            draft(
                draft_item(id="item-0001", kind="見立て", book="kata", text="書き換え"),
                draft_item(id=item["id"], kind="枕", book="kata", text="演者の項目を書き換える"),
            )
        ],
        checker=[verdicts()],
    )
    pipeline.build(P, N, K, client=client, budget=Budget(tmp_path / "ledger.json"), root=data)
    _, kata = books(data)
    assert next(i for i in kata["items"] if i["id"] == item["id"]) == item
    cli.main(GUIDE)
    assert (built.base / "guide" / "guide.json").read_text(encoding="utf-8") == before


def _with_question(built, data, tmp_path):
    ask = {
        "kind": "ask_performer",
        "scene_id": "scene-02",
        "item_id": None,
        "attempt": "行列を演ろうとした",
        "what_is_missing": "なぜ間を詰めるのか",
    }
    c = ScriptedClient(deshi=[{"schema_version": 1, "issues": [ask]}])
    pipeline.reread(P, N, K, client=c, budget=Budget(tmp_path / "ledger.json"), root=data)
    return store.read(built.questions, "questions")["questions"][-1]["id"]


def test_answer_records_resolves_and_stays_off_the_cards(built, data, tmp_path):
    q = _with_question(built, data, tmp_path)
    cli.main(
        [
            "answer",
            "--performer",
            P,
            "--neta",
            N,
            "--question",
            q,
            "--speaker",
            "performer-dummy",
            "--words",
            "次が大事だから",
        ]
    )
    questions = store.read(built.questions, "questions")["questions"]
    assert questions[-1]["answer"]["words"] == "次が大事だから"
    assert store.read(built.review, "review")["issues"][-1]["resolved"] is True
    kata = store.read(built.kata, "book")
    item = kata["items"][-1]
    assert (item["kind"], item["approved"], item["card_text"]) == ("staging", False, None)
    assert item["reason"] == "次が大事だから"
    assert item["anchor"]["scene_id"] == "scene-02"
    assert "次が大事だから" in built.kata.with_suffix(".md").read_text(encoding="utf-8")
    cli.main(GUIDE)
    assert store.read(built.guide, "guide")["cards"] == []


def test_answer_cannot_overwrite(built, data, tmp_path):
    q = _with_question(built, data, tmp_path)
    performer.answer(built, question=q, speaker="s", words="最初の答え")
    with pytest.raises(PerformerError, match="答え済み"):
        performer.answer(built, question=q, speaker="s", words="別の答え")
    assert store.read(built.questions, "questions")["questions"][-1]["answer"]["words"] == "最初の答え"


def test_reread_shows_performer_reason_to_deshi(built, data, tmp_path):
    decide(built)
    c = ScriptedClient(deshi=[NO_ISSUES])
    pipeline.reread(P, N, K, client=c, budget=Budget(tmp_path / "ledger.json"), root=data)
    text = text_of(c.calls["deshi"][0])
    assert WHY in text


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"scene": "scene-99"}, "場面が無い"),
        ({"candidate": "cand-0001"}, "--koza"),
        ({"candidate": "cand-9999", "koza": K}, "候補が無い"),
        ({"words": " "}, "words"),
        ({"card": ""}, "card"),
        ({"why": ""}, "why"),
    ],
)
def test_decide_rejects(built, kw, match):
    before = built.kata.read_text(encoding="utf-8")
    with pytest.raises(PerformerError, match=match):
        decide(built, **kw)
    assert built.kata.read_text(encoding="utf-8") == before


def test_answer_rejects(built, data, tmp_path):
    with pytest.raises(PerformerError):
        performer.answer(built, question="q-9999", speaker="s", words="w")
    q = _with_question(built, data, tmp_path)
    with pytest.raises(PerformerError):
        performer.answer(built, question=q, speaker="s", words="")


def test_decide_same_candidate_twice_is_refused(built):
    decide(built, candidate="cand-0001", koza=K)
    with pytest.raises(PerformerError, match="決定済み"):
        decide(built, candidate="cand-0001", koza=K)


def test_decide_reread_answer_reread_brings_the_card_back(built, data, tmp_path):
    """演者の項目への指摘で札が止まり、答えると戻り、読み直しても止まらない。"""
    item = decide(built)
    cli.main(GUIDE)
    assert len(store.read(built.guide, "guide")["cards"]) == 1
    point = {
        "kind": "missing_step",
        "scene_id": None,
        "item_id": item["id"],
        "attempt": "枕の説明を演ろうとした",
        "what_is_missing": "どの値段を言うのか",
    }
    budget = Budget(tmp_path / "ledger.json")
    pipeline.reread(
        P,
        N,
        K,
        client=ScriptedClient(deshi=[{"schema_version": 1, "issues": [point]}]),
        budget=budget,
        root=data,
    )
    assert store.read(built.guide, "guide")["cards"] == []  # 古い札を残さない
    q = store.read(built.questions, "questions")["questions"][-1]
    cli.main(
        [
            "answer",
            "--performer",
            P,
            "--neta",
            N,
            "--question",
            q["id"],
            "--speaker",
            "s",
            "--words",
            "一分銀",
        ]
    )
    assert len(store.read(built.guide, "guide")["cards"]) == 1  # 答えると戻る
    pipeline.reread(
        P,
        N,
        K,
        client=ScriptedClient(deshi=[{"schema_version": 1, "issues": [point]}]),
        budget=budget,
        root=data,
    )
    assert len(store.read(built.guide, "guide")["cards"]) == 1  # 聞き直して止めない
    assert len(store.read(built.questions, "questions")["questions"]) == 1


def test_answer_retried_after_a_crash_does_not_double(built, data, tmp_path, monkeypatch):
    q = _with_question(built, data, tmp_path)
    real = store.write

    def crash_on_questions(path, obj, schema):
        if schema == "questions":
            raise OSError("途中で落ちた")
        real(path, obj, schema)

    monkeypatch.setattr(store, "write", crash_on_questions)
    with pytest.raises(OSError):
        performer.answer(built, question=q, speaker="s", words="答え")
    monkeypatch.setattr(store, "write", real)
    performer.answer(built, question=q, speaker="s", words="答え")
    kata = store.read(built.kata, "book")
    assert sum(1 for i in kata["items"] if i["text"] == "答え") == 1
    assert store.read(built.review, "review")["issues"][-1]["resolved"] is True


def test_approved_card_without_text_is_skipped_by_builder(built):
    hanashi, kata = store.read(built.hanashi, "book"), store.read(built.kata, "book")
    item = decide(built)
    kata = store.read(built.kata, "book")
    next(i for i in kata["items"] if i["id"] == item["id"])["card_text"] = ""
    assert guide.build(hanashi, kata, None)["cards"] == []


def test_performer_and_neta_from_env(built, monkeypatch, capsys):
    monkeypatch.setenv("NETACHO_PERFORMER", P)
    monkeypatch.setenv("NETACHO_NETA", N)
    cli.main(["guide"])
    assert "札 0 枚" in capsys.readouterr().out


def test_card_count_goes_to_run_json(built):
    decide(built)
    cli.main(GUIDE)
    run = store.read(built.koza_dir(K) / "run.json", "run")
    assert run["guide_performer_cards"] == 1


def test_bad_guide_never_saved(built, monkeypatch):
    decide(built)
    real = guide.build

    def tampered(*a):
        doc = real(*a)
        doc["cards"][0]["text"] = "書き換えた札"
        return doc

    monkeypatch.setattr(guide, "build", tampered)
    with pytest.raises(RefError):
        guide.write(built)
    assert not built.guide.exists()
