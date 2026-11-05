"""画面から答えるサーバのテスト。LLM は使わない。"""

import json
import shutil
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from netacho import performer, server, store, view

DATA = Path(__file__).parent / "fixtures" / "dummy_koza"
P, N, K = "performer-dummy", "雨宿り勘定", "2026-01-01-dummy-hall"


@pytest.fixture
def running(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    neta = store.Neta(P, N, root)
    out = tmp_path / "view"
    view.write(neta, K, out)
    srv = server.AnswerServer(neta, K, out=out)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, path, body=None, *, headers=None, data=None):
    sent = {"Content-Type": "application/json", "X-Netacho-Token": srv.token} if headers is None else headers
    payload = data if data is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=payload, headers=sent)
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def question_id(srv):
    return next(
        q["id"] for q in store.read(srv.neta.questions, "questions")["questions"] if "answer" not in q
    )


def add_question(srv):
    """答えの付いていない問いを、ダミーの帳面に足す。"""
    qs = store.read(srv.neta.questions, "questions")
    rv = store.read(srv.neta.review, "review")
    scene = store.read(srv.neta.hanashi, "book")["scenes"][0]["id"]
    rv["issues"].append(
        {
            "id": "issue-0901",
            "role": "noter",
            "kind": "ask_performer",
            "anchor": {"scene_id": scene},
            "attempt": "笑いの所",
            "what_is_missing": "何をしていますか",
            "round": 1,
            "resolved": False,
        }
    )
    qs["questions"].append(
        {"id": "q-0901", "issue_id": "issue-0901", "scene_id": scene, "text": "何をしていますか"}
    )
    store.write(srv.neta.review, rv, "review")
    store.write(srv.neta.questions, qs, "questions")
    return "q-0901"


def test_serves_only_the_screen_and_its_data(running):
    assert call(running, "/")[0] == 200
    assert call(running, "/view.json")[0] == 200
    for path in ("/.env", "/../.env", "/index.html/../../.env", "/out/view/view.json"):
        assert call(running, path)[0] == 404, path


def test_answer_reaches_kata_and_the_screen(running):
    q = add_question(running)
    status, _ = call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "ここは扇子を置く"})
    assert status == 200
    kata = store.read(running.neta.kata, "book")
    assert any(i["source"].get("words") == "ここは扇子を置く" for i in kata["items"])
    shown = json.loads(call(running, "/view.json")[1])
    assert any(i["source"].get("words") == "ここは扇子を置く" for i in shown["kata"])


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/json"},
        {"Content-Type": "application/json", "X-Netacho-Token": "違う"},
        {"Content-Type": "text/plain", "X-Netacho-Token": "TOKEN"},
        {"Content-Type": "application/json", "X-Netacho-Token": "TOKEN", "Host": "evil.example"},
        {"Content-Type": "application/json", "X-Netacho-Token": "TOKEN", "Origin": "http://evil.example"},
    ],
)
def test_write_without_proof_is_refused(running, headers):
    q = add_question(running)
    sent = {k: (running.token if v == "TOKEN" else v) for k, v in headers.items()}
    sent = {k: v.encode("latin-1", "replace").decode("latin-1") for k, v in sent.items()}
    assert (
        call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "言葉"}, headers=sent)[0]
        == 403
    )
    assert "answer" not in next(
        x for x in store.read(running.neta.questions, "questions")["questions"] if x["id"] == q
    )


def test_reading_with_another_host_is_refused(running):
    assert call(running, "/view.json", headers={"Host": "evil.example"})[0] == 404


def test_bad_bodies_are_refused(running):
    add_question(running)
    assert call(running, "/api/answer", data=b"{")[0] == 400
    assert call(running, "/api/answer", data=b"[]")[0] == 400
    assert call(running, "/api/answer", {"question": "q-0901"})[0] == 400
    assert call(running, "/api/answer", data=b"x" * (server.MAX_BODY + 1))[0] == 400


def test_answer_is_not_overwritten(running):
    q = add_question(running)
    assert call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "最初"})[0] == 200
    status, body = call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "別の言葉"})
    assert status == 400 and "上書きしない" in json.loads(body)["error"]


def test_dismissed_question_is_kept_out_of_kata_and_cannot_be_answered(running):
    q = add_question(running)
    before = len(store.read(running.neta.kata, "book")["items"])
    assert call(running, "/api/dismiss", {"question": q, "speaker": "本人", "reason": "演者の声"})[0] == 200
    assert len(store.read(running.neta.kata, "book")["items"]) == before
    doc = next(x for x in store.read(running.neta.questions, "questions")["questions"] if x["id"] == q)
    assert doc["dismissed"]["reason"] == "演者の声"
    issue = next(i for i in store.read(running.neta.review, "review")["issues"] if i["id"] == "issue-0901")
    assert issue["resolved"] is True
    assert call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "言葉"})[0] == 400


def test_answered_question_cannot_be_dismissed(running):
    q = add_question(running)
    assert call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "言葉"})[0] == 200
    with pytest.raises(performer.PerformerError):
        performer.dismiss(running.neta, question=q, speaker="本人", reason="理由")


def test_empty_reason_is_refused(running):
    q = add_question(running)
    assert call(running, "/api/dismiss", {"question": q, "speaker": "本人", "reason": " "})[0] == 400


def test_server_works_without_the_llm_library(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    srv = server.AnswerServer(store.Neta(P, N, root), K, out=tmp_path / "view")
    assert srv.url().startswith("http://localhost:")
    srv.server_close()


def test_static_viewer_shows_no_form_without_a_token():
    html = (Path(__file__).parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
    assert "TOKEN && !q.answer && !q.dismissed" in html
