"""画面から答えるサーバのテスト。LLM は使わない。"""

import io
import json
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import pytest

from netacho import gaps, guide, performer, pipeline, server, store, view

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


def test_saved_answer_is_not_reported_as_failed_when_the_screen_cannot_update(running, monkeypatch):
    q = add_question(running)

    def broken(*a, **k):
        raise OSError("書き出せない")

    monkeypatch.setattr(view, "write", broken)
    status, body = call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "言葉"})
    doc = json.loads(body)
    assert status == 200 and doc["ok"] is True and "保存しました" in doc["warning"]
    saved = next(x for x in store.read(running.neta.questions, "questions")["questions"] if x["id"] == q)
    assert saved["answer"]["words"] == "言葉"


def test_failure_does_not_show_the_words(running, monkeypatch):
    q = add_question(running)

    def broken(*a, **k):
        raise RuntimeError("演者の言葉")

    monkeypatch.setattr(performer, "answer", broken)
    status, body = call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "演者の言葉"})
    assert status == 500 and "演者の言葉" not in body.decode()


def test_idle_connection_does_not_stop_the_server(running):
    with socket.create_connection(("127.0.0.1", running.port)):
        started = time.monotonic()
        assert call(running, "/")[0] == 200
    assert time.monotonic() - started < 2  # 空の接続の timeout を待たない


def test_missing_screen_file_is_not_found(running):
    (running.out / "view.json").unlink()
    assert call(running, "/view.json")[0] == 404


def test_dismissal_can_be_redone_after_a_crash_between_writes(running, monkeypatch):
    q = add_question(running)
    real = store.write

    def stop_at_questions(path, obj, schema):
        if schema == "questions":
            raise OSError("途中で落ちた")
        real(path, obj, schema)

    monkeypatch.setattr(store, "write", stop_at_questions)
    with pytest.raises(OSError):
        performer.dismiss(running.neta, question=q, speaker="本人", reason="演者の声")
    monkeypatch.setattr(store, "write", real)
    performer.dismiss(running.neta, question=q, speaker="本人", reason="演者の声")
    saved = next(x for x in store.read(running.neta.questions, "questions")["questions"] if x["id"] == q)
    assert saved["dismissed"]["reason"] == "演者の声"


def test_dismissed_scene_is_not_asked_again():
    doc = {
        "questions": [
            {"scene_id": "scene-01", "dismissed": {}},
            {"scene_id": "scene-02", "answer": {}},
            {"scene_id": "scene-03"},
        ]
    }
    assert pipeline.settled_scenes(doc) == {"scene-01", "scene-02"}


def test_note_does_not_bring_a_dismissed_question_back(running):
    q = add_question(running)
    performer.dismiss(running.neta, question=q, speaker="本人", reason="演者の声")
    rv = store.read(running.neta.review, "review")
    qs = store.read(running.neta.questions, "questions")
    attempt = next(i["attempt"] for i in rv["issues"] if i["id"] == "issue-0901")
    t0 = 90.0
    clock_attempt = f"{gaps.clock(t0)} の笑いの所（k）"
    for row in rv["issues"]:
        if row["attempt"] == attempt:
            row["attempt"] = clock_attempt
    found = {
        "koza_id": "k",
        "surprises": [
            {"t0": t0, "scene_id": "scene-01", "before_quote": "前", "question": "問い"},
        ],
    }
    assert gaps.add_questions(found, rv, qs) == 0


def raw_exchange(srv, payload):
    """1 本の接続で送り、切れるまで全部読む。"""
    head = (
        f"POST /api/answer HTTP/1.0\r\nHost: 127.0.0.1:{srv.port}\r\n"
        f"Content-Type: application/json\r\nX-Netacho-Token: {srv.token}\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode()
    with socket.create_connection(("127.0.0.1", srv.port)) as sock:
        sock.sendall(head + payload)
        got = b""
        while chunk := sock.recv(4096):
            got += chunk
    return got


@pytest.mark.parametrize(
    "payload", [b"{", json.dumps({"question": "q-9999", "speaker": "本人", "words": "言"}).encode()]
)
def test_a_refused_write_gets_exactly_one_response(running, payload):
    got = raw_exchange(running, payload)
    assert got.count(b"HTTP/1.0 ") == 1 and got.startswith(b"HTTP/1.0 400")


def test_silent_body_does_not_hold_the_lock(running):
    q = add_question(running)
    head = (
        f"POST /api/answer HTTP/1.0\r\nHost: 127.0.0.1:{running.port}\r\n"
        f"Content-Type: application/json\r\nX-Netacho-Token: {running.token}\r\n"
        "Content-Length: 100\r\n\r\n"
    ).encode()
    with socket.create_connection(("127.0.0.1", running.port)) as sock:
        sock.sendall(head)
        time.sleep(0.1)
        started = time.monotonic()
        assert call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "言葉"})[0] == 200
    assert time.monotonic() - started < 2


def test_dismissal_refreshes_the_cards(running, monkeypatch):
    q = add_question(running)
    seen = []
    monkeypatch.setattr(guide, "refresh", lambda neta: seen.append(neta))
    assert call(running, "/api/dismiss", {"question": q, "speaker": "本人", "reason": "演者の声"})[0] == 200
    assert seen == [running.neta]


def test_port_in_use_is_told_in_plain_words(running, tmp_path):
    with pytest.raises(SystemExit, match="--port"):
        server.serve(running.neta, K, port=running.port, out=tmp_path / "view2")


def test_token_does_not_leak_by_referrer(running):
    req = urllib.request.Request(f"http://127.0.0.1:{running.port}/")
    with urllib.request.urlopen(req) as res:
        assert res.headers["Referrer-Policy"] == "no-referrer"


def test_dismiss_has_its_own_reason_and_warns_it_cannot_be_undone():
    html = (Path(__file__).parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
    assert "元に戻せません" in html and "reason: words.value" not in html


def put_wav(srv, seconds=60):
    path = srv.neta.koza_dir(K) / "audio.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)


def clip_seconds(data):
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def test_clip_is_short_and_needs_the_token(running):
    put_wav(running)
    status, data = call(running, f"/clip?at=30&t={running.token}", headers={})
    assert status == 200 and clip_seconds(data) == 18
    assert call(running, "/clip?at=30", headers={})[0] == 403
    assert call(running, "/clip?at=30&t=x", headers={})[0] == 403


def test_clip_at_the_start_and_end_of_the_recording(running):
    put_wav(running)
    assert clip_seconds(call(running, f"/clip?at=5&t={running.token}", headers={})[1]) == 18
    assert clip_seconds(call(running, f"/clip?at=59&t={running.token}", headers={})[1]) == 13


@pytest.mark.parametrize("at", ["x", "-1", "nan", "inf", "9999", ""])
def test_bad_clip_time_is_not_found(running, at):
    put_wav(running)
    assert call(running, f"/clip?at={at}&t={running.token}", headers={})[0] == 404


def test_missing_recording_is_not_found(running):
    assert call(running, f"/clip?at=5&t={running.token}", headers={})[0] == 404


def test_clip_with_another_host_is_refused(running):
    put_wav(running)
    assert call(running, f"/clip?at=5&t={running.token}", headers={"Host": "evil.example"})[0] == 404


def test_recording_is_not_written_to_the_screen_folder(running):
    put_wav(running)
    view.write(running.neta, K, running.out)
    assert sorted(p.name for p in running.out.iterdir()) == ["index.html", "view.json"]


def test_serve_shows_the_url_at_once_and_opens_the_browser(tmp_path, monkeypatch, capsys):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", opened.append)
    monkeypatch.setattr(server.AnswerServer, "serve_forever", lambda self: None)
    server.serve(store.Neta(P, N, root), K, port=0, out=tmp_path / "view")
    assert "http://localhost:" in capsys.readouterr().out and len(opened) == 1
    server.serve(store.Neta(P, N, root), K, port=0, out=tmp_path / "view", browser=False)
    assert len(opened) == 1


def test_serve_flushes_the_url(tmp_path, monkeypatch):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    flushed = []
    monkeypatch.setattr(server.AnswerServer, "serve_forever", lambda self: None)

    class Out(io.StringIO):
        def flush(self):
            flushed.append(self.getvalue())

    monkeypatch.setattr(sys, "stdout", Out())
    server.serve(store.Neta(P, N, root), K, port=0, out=tmp_path / "view", browser=False)
    assert any("http://localhost:" in text for text in flushed)
