"""演者の判断を正本に入れる。LLM は呼ばない。演者の言葉は 1 文字も変えない。"""

from __future__ import annotations

from datetime import date, datetime, timezone

from . import merge, store
from .schema import check_refs


class PerformerError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _required(**fields: str | None) -> None:
    for name, value in fields.items():
        if value is None or not value.strip():
            raise PerformerError(f"{name} が空")


def _staging(
    hanashi: dict,
    kata: dict,
    *,
    speaker: str,
    words: str,
    scene: str,
    position: str,
    card: str | None,
    why: str,
    approved: bool,
    at: str,
    candidate: str | None = None,
) -> dict:
    source = {"speaker": speaker, "recorded_at": at, "words": words, "approved": True}
    if candidate is not None:
        source["candidate_id"] = candidate
    return {
        "id": f"item-{merge.next_item_number(hanashi, kata):04d}",
        "kind": "staging",
        "priority": 1,
        "text": words,
        "basis": "heard",
        "source": source,
        "edited_by": "performer",
        "history": [{"at": at, "before": None, "after": words, "by": speaker}],
        "anchor": {"scene_id": scene, "position": position},
        "card_text": card,
        "reason": why,
        "approved": approved,
    }


def decide(
    neta: store.Neta,
    *,
    speaker: str,
    words: str,
    card: str,
    why: str,
    scene: str,
    position: str = "within",
    candidate: str | None = None,
    koza: str | None = None,
    now: str | None = None,
) -> dict:
    """演者の決定を型に入れる。入れた item を返す。"""
    _required(speaker=speaker, words=words, card=card, why=why)
    hanashi, kata = store.read(neta.hanashi, "book"), store.read(neta.kata, "book")
    if scene not in {s["id"] for s in hanashi["scenes"]}:
        raise PerformerError(f"場面が無い: {scene}")
    if candidate is not None:
        if koza is None:
            raise PerformerError("--candidate には --koza が要る")
        cands = store.read(neta.koza_dir(koza) / "note_candidates.json", "note_candidates")
        if candidate not in {c["id"] for c in cands["candidates"]}:
            raise PerformerError(f"候補が無い: {candidate}")
        if any(i["source"].get("candidate_id") == candidate for i in kata["items"]):
            raise PerformerError(f"{candidate} は決定済み（別の札にするなら --candidate を外す）")
    twice = any(
        i["kind"] == "staging"
        and i["approved"]
        and i["card_text"] == card
        and i["anchor"] == {"scene_id": scene, "position": position}
        for i in kata["items"]
    )
    if twice:
        raise PerformerError("同じ場面の同じ位置に、同じ札がある")
    item = _staging(
        hanashi,
        kata,
        speaker=speaker,
        words=words,
        scene=scene,
        position=position,
        card=card,
        why=why,
        approved=True,
        at=now or _now(),
        candidate=candidate,
    )
    kata["items"].append(item)
    check_refs(hanashi, kata)
    store.write(neta.kata, kata, "book")
    return item


def dismiss(neta: store.Neta, *, question: str, speaker: str, reason: str, now: str | None = None) -> None:
    """的外れの問いを捨てる。型には入れない。指摘を解決にするので、ノートをやり直しても戻らない。"""
    _required(speaker=speaker, reason=reason)
    if not neta.questions.exists() or not neta.review.exists():
        raise PerformerError("質問がまだ無い")
    questions = store.read(neta.questions, "questions")
    review = store.read(neta.review, "review")
    q = next((x for x in questions["questions"] if x["id"] == question), None)
    if q is None:
        raise PerformerError(f"質問が無い: {question}")
    if "answer" in q:
        raise PerformerError(f"{question} は答え済み（演者の言葉は上書きしない）")
    if "dismissed" in q:
        raise PerformerError(f"{question} は捨て済み")
    q["dismissed"] = {"speaker": speaker, "recorded_at": now or _now(), "reason": reason}
    issue = next((i for i in review["issues"] if i["id"] == q["issue_id"]), None)
    if issue is not None:
        issue["resolved"] = True
    store.write(neta.review, review, "review")  # 先に指摘を解決にする。途中で落ちても、やり直せば揃う
    store.write(neta.questions, questions, "questions")


def answer(neta: store.Neta, *, question: str, speaker: str, words: str, now: str | None = None) -> dict:
    """質問への答えを記録し、指摘を解決にして、型に一行入れる。札には出さない。"""
    _required(speaker=speaker, words=words)
    if not neta.questions.exists() or not neta.review.exists():
        raise PerformerError("質問がまだ無い")
    questions = store.read(neta.questions, "questions")
    review = store.read(neta.review, "review")
    hanashi, kata = store.read(neta.hanashi, "book"), store.read(neta.kata, "book")
    q = next((x for x in questions["questions"] if x["id"] == question), None)
    if q is None:
        raise PerformerError(f"質問が無い: {question}")
    if "dismissed" in q:
        raise PerformerError(f"{question} は捨てた問い")
    if "answer" in q and q["answer"]["words"] != words:
        raise PerformerError(f"{question} は答え済み（演者の言葉は上書きしない）")
    issue = next((i for i in review["issues"] if i["id"] == q["issue_id"]), None)
    if "answer" in q and (issue is None or issue["resolved"]):
        raise PerformerError(f"{question} は答え済み（演者の言葉は上書きしない）")
    at = now or _now()
    q.setdefault("answer", {"speaker": speaker, "recorded_at": at, "words": words})
    if issue is not None:
        issue["resolved"] = True
    # 途中で落ちた答えのやり直しなら、型に二重に入れない
    done = next((i for i in kata["items"] if i["source"].get("question_id") == question), None)
    item = done or _staging(
        hanashi,
        kata,
        speaker=speaker,
        words=words,
        scene=q["scene_id"],
        position="within",
        card=None,
        why=words,
        approved=False,
        at=at,
    )
    item["source"]["question_id"] = question
    if done is None:
        kata["items"].append(item)
    check_refs(hanashi, kata, review=review, questions=questions)
    # kata → questions → review の順。途中で落ちても、やり直せば揃う
    store.write(neta.kata, kata, "book")
    store.write(neta.questions, questions, "questions")
    store.write(neta.review, review, "review")
    return item


UNKNOWN_DATE = "0000-00-00"  # 公演日が分からない高座。schema の形は保つ


def edit_koza(neta: store.Neta, koza_id: str, *, when: str, venue: str) -> None:
    """公演日と会場を直す。フォルダの名前は変えない。"""
    when, venue = when or UNKNOWN_DATE, venue.strip()
    if when != UNKNOWN_DATE:
        date.fromisoformat(when)  # 形が同じでも暦に無い日は受けない
    _required(venue=venue)
    if "\n" in venue or len(venue) > 100:
        raise PerformerError("会場は 1 行、100 字まで")
    path = neta.koza_dir(koza_id) / "koza.json"
    store.write(path, {**store.read(path, "koza"), "date": when, "venue": venue}, "koza")


def edit_answer(neta: store.Neta, *, question: str, speaker: str, words: str, now: str | None = None) -> None:
    """演者が自分の答えを直す。前の言葉は型の履歴に残す。札の文は別の決定なので変えない。"""
    _required(speaker=speaker, words=words)
    if not neta.questions.exists() or not neta.review.exists():
        raise PerformerError("質問がまだ無い")
    questions = store.read(neta.questions, "questions")
    review = store.read(neta.review, "review")
    hanashi, kata = store.read(neta.hanashi, "book"), store.read(neta.kata, "book")
    q = next((x for x in questions["questions"] if x["id"] == question), None)
    if q is None or "answer" not in q:
        raise PerformerError(f"{question} に答えが無い")
    item = next((i for i in kata["items"] if i["source"].get("question_id") == question), None)
    if item is None:
        raise PerformerError(f"{question} の項目が型に無い")
    if q["answer"]["words"] == words:
        return
    at = now or _now()
    before = item["text"]
    if before != words:  # 同じなら、途中で落ちた直しのやり直し。履歴を二重にしない
        if item.get("reason") == before:  # 別に決めた理由は変えない
            item["reason"] = words
        item["text"] = item["source"]["words"] = words
        item["source"].update(speaker=speaker, recorded_at=at)  # 元の書き手は履歴の最初の行に残る
        item.setdefault("history", []).append({"at": at, "before": before, "after": words, "by": speaker})
    q["answer"] = {"speaker": speaker, "recorded_at": at, "words": words}
    check_refs(hanashi, kata, review=review, questions=questions)
    store.write(neta.kata, kata, "book")  # kata → questions の順。途中で落ちても、やり直せば揃う
    store.write(neta.questions, questions, "questions")
