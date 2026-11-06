"""契約のテスト。ダミーの全 JSON が通る、壊れた参照で落ちる、役の schema が API の制約に収まる。"""

import copy
import json
from pathlib import Path

import pytest

from netacho import schema
from netacho.schema import RefError, SchemaError, api_schema, check_refs, validate

BASE = Path(__file__).parent / "fixtures" / "dummy_koza" / "performer-dummy" / "雨宿り勘定"
KOZA = BASE / "koza" / "2026-01-01-dummy-hall"
FILES = {
    BASE / "hanashi.json": "book",
    BASE / "kata.json": "book",
    BASE / "questions.json": "questions",
    BASE / "review" / "deshi.json": "review",
    BASE / "guide" / "guide.json": "guide",
    BASE / "drafts" / "writer.json": "writer",
    BASE / "drafts" / "checker.json": "checker",
    BASE / "drafts" / "noter.json": "noter",
    BASE / "drafts" / "deshi.json": "deshi",
    BASE / "drafts" / "lexicon.json": "lexicon",
    BASE / "drafts" / "scribe.json": "scribe",
    BASE / "drafts" / "asker.json": "asker",
    BASE / "drafts" / "aligner.json": "aligner",
    KOZA / "koza.json": "koza",
    KOZA / "transcript.json": "transcript",
    KOZA / "timeline.json": "timeline",
    KOZA / "reactions.json": "reactions",
    KOZA / "note_candidates.json": "note_candidates",
    KOZA / "gaps.json": "gaps",
    KOZA / "seisho.json": "seisho",
    KOZA / "compare.json": "compare",
    KOZA / "run.json": "run",
}
ROLE_SCHEMAS = ["writer", "checker", "noter", "deshi", "lexicon", "scribe", "asker", "aligner"]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def docs() -> dict:
    return {
        "hanashi": _load(BASE / "hanashi.json"),
        "kata": _load(BASE / "kata.json"),
        "koza": _load(KOZA / "koza.json"),
        "transcript": _load(KOZA / "transcript.json"),
        "timeline": _load(KOZA / "timeline.json"),
        "reactions": _load(KOZA / "reactions.json"),
        "note_candidates": _load(KOZA / "note_candidates.json"),
        "review": _load(BASE / "review" / "deshi.json"),
        "questions": _load(BASE / "questions.json"),
        "guide": _load(BASE / "guide" / "guide.json"),
    }


def _item(book: dict, item_id: str) -> dict:
    return next(i for i in book["items"] if i["id"] == item_id)


def test_every_schema_file_has_a_dummy():
    names = {p.stem for p in schema.SCHEMA_DIR.glob("*.json")}
    assert names == set(FILES.values())


@pytest.mark.parametrize(("path", "name"), FILES.items(), ids=lambda v: getattr(v, "stem", v))
def test_dummy_validates(path, name):
    doc = _load(path)
    validate(doc, name)
    assert doc["schema_version"] == 1


def test_dummy_refs_ok(docs):
    check_refs(docs.pop("hanashi"), docs.pop("kata"), **docs)


@pytest.mark.parametrize(
    ("break_it", "match"),
    [
        (lambda d: d["kata"]["items"][4]["anchor"].update(scene_id="scene-99"), "場面が無い"),
        (lambda d: d["hanashi"]["items"][0]["source"].update(koza_id="other"), "高座が無い"),
        (lambda d: d["hanashi"]["items"][0]["source"].update(segs=["seg-9999", "seg-9999"]), "区間が無い"),
        (lambda d: d["hanashi"]["items"][0]["source"].update(segs=["seg-0007", "seg-0004"]), "順が逆"),
        (lambda d: d["kata"]["items"][2]["payoff"].update(segs=["seg-0009", "seg-9999"]), "payoff"),
        (lambda d: d["hanashi"]["scenes"][0].update(segs=["seg-0001", "seg-9999"]), "区間が無い"),
        (lambda d: d["kata"]["items"][0].update(id="item-0001"), "重複"),
        (lambda d: d["kata"]["items"][0].update(basis="knowledge"), "型は heard だけ"),
        (lambda d: d["hanashi"]["items"][0].update(kind="枕"), "この帳面に置かない"),
        (lambda d: d["kata"]["items"][0].update(kind="場面"), "この帳面に置かない"),
        (lambda d: d["hanashi"]["items"][2].pop("payoff"), "回収の区間が無い"),
        (lambda d: d["kata"]["items"][0].update(payoff=d["kata"]["items"][2]["payoff"]), "回収は付けない"),
        (lambda d: d["kata"]["scenes"].pop(), "場面が違う"),
        (lambda d: d["timeline"]["segments"][0].update(seg_id="seg-9999"), "timeline: 区間が無い"),
        (lambda d: d["timeline"]["segments"][0].update(scene_id="scene-99"), "timeline: 場面が無い"),
        (lambda d: d["timeline"].update(koza_id="other"), "timeline: 高座が違う"),
        (lambda d: d["reactions"].update(koza_id="other"), "reactions: 高座が違う"),
        (lambda d: d["transcript"].update(koza_id="other"), "transcript: 高座が違う"),
        (lambda d: d["note_candidates"]["candidates"][0].update(item_id="item-9999"), "item が無い"),
        (lambda d: d["note_candidates"]["candidates"][0].update(reaction_ids=["react-9999"]), "反応が無い"),
        (lambda d: d["review"]["issues"][0].update(anchor={"scene_id": "scene-99"}), "review/.*場面が無い"),
        (lambda d: d["review"]["issues"][1].update(anchor={"item_id": "item-9999"}), "review/.*item が無い"),
        (lambda d: d["questions"]["questions"][0].update(scene_id="scene-99"), "questions/.*場面が無い"),
        (lambda d: d["questions"]["questions"][0].update(issue_id="issue-9999"), "指摘が無い"),
        (lambda d: d["questions"]["questions"][0].update(issue_id="issue-0002"), "ask_performer でない"),
        (lambda d: d["guide"]["cards"][0].update(item_id="item-0006"), "承認済みの staging でない"),
        (lambda d: d["guide"]["cards"][0].update(item_id="item-0010"), "承認済みの staging でない"),
        (lambda d: d["guide"]["cards"][0].update(scene_id="scene-99"), "guide/.*場面が無い"),
        (lambda d: d["guide"]["cards"][0].update(text="書き換えた札"), "写しになっていない"),
        (lambda d: d["guide"]["cards"][0]["copied_fields"].update(reason="書き換えた"), "写しになっていない"),
        (lambda d: d["guide"]["cards"][0].update(order=2), "order"),
        (lambda d: d["guide"]["cards"].clear(), "札に出ていない"),
        (lambda d: d["guide"]["cards"].append(d["guide"]["cards"][0]), "重複"),
        (lambda d: d["review"]["issues"][1].update(anchor={"item_id": "item-0009"}), "解決していない指摘"),
        (lambda d: d["kata"]["items"][4]["source"].update(approved=False), "確かめていない"),
        (lambda d: d["kata"]["scenes"].append({**d["kata"]["scenes"][0]}), "場面の id が重複"),
        (lambda d: d["hanashi"]["scenes"][1].update(order=1), "order が重複"),
        (lambda d: d["kata"]["characters"].append(d["kata"]["characters"][0]), "人物の id が重複"),
        (lambda d: d["review"]["issues"].append(d["review"]["issues"][0]), "review: id が重複"),
        (
            lambda d: d["questions"]["questions"].append(d["questions"]["questions"][0]),
            "questions: id が重複",
        ),
        (lambda d: d["questions"]["questions"][0].update(scene_id="scene-01"), "指摘と場面が違う"),
        (lambda d: d["note_candidates"]["candidates"].append(d["note_candidates"]["candidates"][0]), "重複"),
        (lambda d: d["note_candidates"]["candidates"][0].update(reaction_ids=["react-0001"]), "why と"),
        (lambda d: d["note_candidates"]["candidates"][1].update(reaction_ids=[]), "why と"),
        (lambda d: d["note_candidates"]["candidates"][0].update(item_id="item-0001"), "回収の無い"),
        (lambda d: d["note_candidates"]["candidates"][0].update(payoff_t=99.0), "窓の外"),
        (lambda d: d["transcript"]["segments"][0].update(end=-0.0, start=1.0), "end が start より前"),
        (
            lambda d: d["note_candidates"]["candidates"][0].update(payoff_t=46.0, window=[45.0, 51.0]),
            "回収の区間の外",
        ),
        (lambda d: d["kata"]["items"][2]["source"].update(koza_id="2026-02-02-other") or None, "別の高座"),
        (lambda d: d["hanashi"]["scenes"][0].update(koza_id="other"), "高座が無い"),
        (lambda d: d["timeline"]["segments"].append(d["timeline"]["segments"][0]), "区間が重複"),
        (lambda d: d["guide"].update(neta="別の演目"), "guide と kata"),
        (
            lambda d: (
                d["kata"]["items"][2]["source"].update(segs=["seg-0009", "seg-0009"])
                or d["kata"]["items"][2]["payoff"].update(segs=["seg-0008", "seg-0008"])
            ),
            "回収が仕込みより前",
        ),
    ],
)
def test_broken_refs_raise(docs, break_it, match):
    break_it(docs)
    with pytest.raises(RefError, match=match):
        check_refs(docs.pop("hanashi"), docs.pop("kata"), **docs)


def test_cards_must_be_in_scene_order(docs):
    second = {**docs["kata"]["items"][4], "id": "item-0011"}
    second["anchor"] = {"scene_id": "scene-02", "position": "before"}
    docs["kata"]["items"].append(second)
    cards = docs["guide"]["cards"]
    extra = copy.deepcopy(cards[0])
    extra.update(item_id="item-0011", scene_id="scene-02")
    extra["copied_fields"]["scene_title"] = "傘を借りる"
    extra["copied_fields"]["position"] = "before"
    cards.insert(0, extra)
    cards[0]["order"], cards[1]["order"] = 1, 2
    with pytest.raises(RefError, match="場面順でない"):
        check_refs(docs.pop("hanashi"), docs.pop("kata"), **docs)


def test_unresolved_issue_keeps_item_off_the_cards(docs):
    docs["review"]["issues"][1]["anchor"] = {"item_id": "item-0009"}
    docs["guide"]["cards"] = []
    check_refs(docs.pop("hanashi"), docs.pop("kata"), **docs)


def test_real_data_shapes_pass(docs):
    """実データで起きる形：空の区間、重なる区間、手で付けた反応、札 0 枚、項目 0 件。"""
    segs = docs["transcript"]["segments"]
    segs[0]["text"] = ""
    segs[0]["words"] = []
    segs[1]["start"] = segs[0]["end"] - 0.5  # 区間の重なり
    segs[-1]["pause_after"] = None
    docs["reactions"]["reactions"][0].update(method="manual", strength_rank=None)
    validate(docs["transcript"], "transcript")
    validate(docs["reactions"], "reactions")
    check_refs(
        docs["hanashi"],
        docs["kata"],
        koza=docs["koza"],
        transcript=docs["transcript"],
        reactions=docs["reactions"],
    )
    kata = {**docs["kata"], "items": []}
    empty_guide = {**docs["guide"], "cards": []}
    validate(kata, "book")
    validate(empty_guide, "guide")
    check_refs(docs["hanashi"], kata, guide=empty_guide)


def test_answer_item_is_on_the_book_but_not_on_the_cards(docs):
    """演者の答えは、札に出さない staging として型に入る（source は確認済み、札は未承認）。"""
    answer = _item(docs["kata"], "item-0010")
    assert answer["source"]["approved"] is True and answer["approved"] is False
    assert "item-0010" not in [c["item_id"] for c in docs["guide"]["cards"]]


def test_error_message_does_not_show_values():
    """エラーの文に演者の言葉を出さない（ログに残るため）。"""
    kata = _load(BASE / "kata.json")
    kata["items"][4]["source"] = {"words": "演者の言葉そのもの"}
    with pytest.raises(SchemaError) as e:
        validate(kata, "book")
    assert "演者の言葉そのもの" not in str(e.value)


def test_second_koza_does_not_check_first_koza_spans(docs):
    """別の高座の区間は、この transcript では確かめない（ID が重なるだけで別物）。"""
    docs["hanashi"]["items"][0]["source"].update(koza_id="2026-02-02-other", segs=["seg-9999", "seg-9999"])
    check_refs(
        docs.pop("hanashi"),
        docs.pop("kata"),
        koza_ids={"2026-02-02-other"},
        **docs,
    )


@pytest.mark.parametrize(
    "break_it",
    [
        lambda k: k["items"][4].pop("anchor"),  # staging に位置が無い
        lambda k: k["items"][4].update(source=k["items"][0]["source"]),  # staging が録音由来
        lambda k: k["items"][4].update(edited_by="tool"),  # staging を道具が書いた
        lambda k: k["items"][4].update(card_text=None),  # 承認済みの札に文が無い
        lambda k: k["items"][0].update(kind="上下"),  # 上下は演者の追記だけ
        lambda k: k["items"][0].update(card_text="x"),  # staging 以外に札の欄
        lambda k: k["items"][0].update(priority=4),
        lambda k: k["items"][0].update(extra=1),
        lambda k: k["items"][0].update(id="x-1"),
        lambda k: k.pop("schema_version"),
    ],
)
def test_book_schema_rejects(break_it):
    kata = _load(BASE / "kata.json")
    break_it(kata)
    with pytest.raises(SchemaError):
        validate(kata, "book")


def test_guide_llm_fields_must_be_empty():
    guide = _load(BASE / "guide" / "guide.json")
    guide["cards"][0]["llm_fields"] = {"text": "LLM が書いた"}
    with pytest.raises(SchemaError):
        validate(guide, "guide")


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def _keys(node):
    if isinstance(node, dict):
        yield from node.keys()
        for v in node.values():
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


@pytest.mark.parametrize("name", ROLE_SCHEMAS)
def test_role_schema_fits_structured_outputs(name):
    s = api_schema(name)
    for obj in _objects(s):
        assert obj.get("additionalProperties") is False
        assert set(obj["required"]) == set(obj["properties"])  # 無いときは null にする
    bad = {"oneOf", "if", "then", "else", "not", "$ref", "definitions", *schema._API_UNSUPPORTED}
    assert not set(_keys(s)) & bad


def test_api_schema_does_not_touch_local_schema():
    before = copy.deepcopy(schema.load("writer"))
    api_schema.cache_clear()
    api_schema("writer")
    assert schema.load("writer") == before
    assert "minItems" in json.dumps(before)  # 手元の検証では長さ 2 を見る


@pytest.mark.parametrize(
    "break_it",
    [
        lambda w: w["items"][0].update(priority=4),
        lambda w: w["scenes"][0].update(id="s1"),
        lambda w: w["characters"][0].update(id="c1"),
        lambda w: w["items"][0].update(kind="staging"),
        lambda w: w["items"][0].update(kind="上下"),
    ],
)
def test_writer_schema_rejects(break_it):
    doc = _load(BASE / "drafts" / "writer.json")
    break_it(doc)
    with pytest.raises(SchemaError):
        validate(doc, "writer")


def test_writer_span_length_checked_locally():
    doc = _load(BASE / "drafts" / "writer.json")
    doc["items"][0]["source"]["segs"] = ["seg-0001"]
    with pytest.raises(SchemaError):
        validate(doc, "writer")
