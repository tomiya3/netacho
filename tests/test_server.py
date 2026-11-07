"""画面から答えるサーバのテスト。LLM は使わない。"""

import io
import json
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import pytest

from netacho import cli, gaps, guide, jobs, performer, pipeline, server, store, view

DATA = Path(__file__).parent / "fixtures" / "dummy_koza"
ROOM_FOR = server._room_for  # 実機の空きを測る本物。下のテストだけで使う


@pytest.fixture(autouse=True)
def plenty_of_room(monkeypatch):
    """テストの結果を、実機のディスクの空きに左右させない。"""
    monkeypatch.setattr(server, "_room_for", lambda kdir, size: True)
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


WRITES = {"/api/answer", "/api/decide", "/api/dismiss", "/api/edit"}


def call(srv, path, body=None, *, headers=None, data=None):
    """書き込みの本文には、画面と同じく開いている高座を添える。"""
    sent = {"Content-Type": "application/json", "X-Netacho-Token": srv.token} if headers is None else headers
    if path in WRITES and isinstance(body, dict):
        body = {"neta": srv.neta.neta, "koza": srv.koza_id, **body}
    payload = data if data is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=payload, headers=sent)
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def second_koza(srv, neta="別の会", koza="2026-02-03-other-hall"):
    src = srv.neta.base
    shutil.copytree(src, src.parent / neta)
    (src.parent / neta / "koza" / K).rename(src.parent / neta / "koza" / koza)
    path = src.parent / neta / "koza" / koza / "koza.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**meta, "id": koza, "neta": neta}), encoding="utf-8")
    return neta, koza


def test_open_switches_the_koza_and_the_view(running):
    neta, koza = second_koza(running)
    status, _ = call(running, "/api/open", {"neta": neta, "koza": koza})
    assert status == 200
    assert running.koza_id == koza and running.neta.neta == neta
    doc = json.loads((running.out / "view.json").read_text(encoding="utf-8"))
    assert doc["koza"]["id"] == koza


@pytest.mark.parametrize(
    "body",
    [
        {"neta": "../..", "koza": K},
        {"neta": N, "koza": "../x"},
        {"neta": N, "koza": "2099-01-01-none"},
    ],
)
def test_open_refuses_what_is_not_a_koza_of_this_performer(running, body):
    status, _ = call(running, "/api/open", body)
    assert status == 400
    assert running.koza_id == K


def test_open_needs_the_token(running):
    headers = {"Content-Type": "application/json"}
    status, _ = call(running, "/api/open", {"neta": N, "koza": K}, headers=headers)
    assert status == 403


def test_koza_date_and_venue_can_be_fixed(running):
    status, _ = call(running, "/api/koza", {"neta": N, "koza": K, "date": "2026-03-04", "venue": "会場 A"})
    assert status == 200
    meta = store.read(running.neta.koza_dir(K) / "koza.json", "koza")
    assert (meta["id"], meta["date"], meta["venue"]) == (K, "2026-03-04", "会場 A")
    doc = json.loads((running.out / "view.json").read_text(encoding="utf-8"))
    assert doc["kozas"][0]["date"] == "2026-03-04"


def test_koza_empty_date_means_unknown(running):
    status, _ = call(running, "/api/koza", {"neta": N, "koza": K, "date": "", "venue": "会場 A"})
    assert status == 200
    assert store.read(running.neta.koza_dir(K) / "koza.json", "koza")["date"] == "0000-00-00"


@pytest.mark.parametrize(
    ("when", "venue"),
    [("2026-13-40", "会場"), ("2026-02-03", "  "), ("きのう", "会場"), ("20260203", "会場")],
)
def test_koza_refuses_a_bad_date_or_an_empty_venue(running, when, venue):
    before = (running.neta.koza_dir(K) / "koza.json").read_text(encoding="utf-8")
    status, _ = call(running, "/api/koza", {"neta": N, "koza": K, "date": when, "venue": venue})
    assert status == 400
    assert (running.neta.koza_dir(K) / "koza.json").read_text(encoding="utf-8") == before


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


def test_clip_answers_range_requests(running):
    put_wav(running)
    path = f"/clip?at=30&t={running.token}"
    whole = call(running, path, headers={})[1]
    status, part = call(running, path, headers={"Range": "bytes=0-1"})
    assert status == 206 and part == whole[:2]
    status, part = call(running, path, headers={"Range": "bytes=100-"})
    assert status == 206 and part == whole[100:]
    assert call(running, path, headers={"Range": f"bytes={len(whole)}-"})[0] == 416


@pytest.mark.parametrize("at", ["x", "-1", "nan", "inf", "1e306", "9999", ""])
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


def decide_body(srv, **kw):
    scene = store.read(srv.neta.hanashi, "book")["scenes"][0]["id"]
    body = {
        "scene": scene,
        "position": "within",
        "speaker": "本人",
        "words": "扇子を置く",
        "card": "扇子を置く",
        "why": "間のため",
    }
    return {**body, **kw}


def test_decide_makes_a_card_on_the_screen(running):
    assert call(running, "/api/decide", decide_body(running))[0] == 200
    shown = json.loads(call(running, "/view.json")[1])
    assert any(c["text"] == "扇子を置く" for c in shown["guide"]["cards"])
    assert any(
        i["source"].get("words") == "扇子を置く" for i in store.read(running.neta.kata, "book")["items"]
    )


def test_decide_makes_the_first_card_when_no_guide_exists(running):
    running.neta.guide.unlink()
    assert call(running, "/api/decide", decide_body(running))[0] == 200
    assert running.neta.guide.exists()


@pytest.mark.parametrize(
    "change",
    [{"scene": "scene-99"}, {"position": "横"}, {"card": " "}, {"words": ""}, {"why": ""}, {"speaker": ""}],
)
def test_bad_decisions_are_refused(running, change):
    before = store.read(running.neta.kata, "book")["items"]
    assert call(running, "/api/decide", decide_body(running, **change))[0] == 400
    assert store.read(running.neta.kata, "book")["items"] == before


def test_decide_needs_the_token(running):
    sent = {"Content-Type": "application/json"}
    assert call(running, "/api/decide", decide_body(running), headers=sent)[0] == 403


def test_static_viewer_has_no_decide_form_without_a_token():
    html = (Path(__file__).parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
    assert 'if (TOKEN) root.append(el("h2", {}, "札に出す")' in html


def test_decision_stays_in_kata_when_the_card_cannot_be_written(running, monkeypatch):
    def broken(neta):
        raise OSError("札を書けない")

    monkeypatch.setattr(guide, "write", broken)
    status, body = call(running, "/api/decide", decide_body(running))
    assert status == 200 and "netacho guide" in json.loads(body)["warning"]
    assert any(
        i["source"].get("words") == "扇子を置く" for i in store.read(running.neta.kata, "book")["items"]
    )


def test_the_same_card_twice_is_refused_and_kept_once(running):
    assert call(running, "/api/decide", decide_body(running))[0] == 200
    assert call(running, "/api/decide", decide_body(running))[0] == 400
    items = store.read(running.neta.kata, "book")["items"]
    assert sum(i["source"].get("words") == "扇子を置く" for i in items) == 1


def test_answer_alone_does_not_make_a_first_card(running):
    q = add_question(running)
    running.neta.guide.unlink()
    assert call(running, "/api/answer", {"question": q, "speaker": "本人", "words": "言葉"})[0] == 200
    assert not running.neta.guide.exists()


def test_clip_of_the_second_recording(running):
    other = running.neta.koza_dir("2026-02-01-dummy-hall")
    other.mkdir(parents=True)
    with wave.open(str(other / "audio.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 30)
    status, data = call(running, f"/clip?at=15&side=b&t={running.token}", headers={})
    assert status == 200 and clip_seconds(data) == 18


def test_second_recording_missing_is_not_found(running):
    assert call(running, f"/clip?at=5&side=b&t={running.token}", headers={})[0] == 404


def test_side_cannot_leave_the_comparison(running):
    put_wav(running)
    assert (
        call(running, f"/clip?at=30&side=../../x&t={running.token}", headers={})[0] == 200
    )  # 1 本目を返すだけ


def test_clip_length_follows_the_line(running):
    put_wav(running)
    status, data = call(running, f"/clip?from=10&to=50&t={running.token}", headers={})
    assert status == 200 and clip_seconds(data) == 40


def test_clip_is_capped_so_the_whole_recording_is_not_handed_out(running):
    put_wav(running, seconds=200)
    data = call(running, f"/clip?from=0&to=200&t={running.token}", headers={})[1]
    assert clip_seconds(data) == server.CLIP_MAX


@pytest.mark.parametrize("query", ["from=10", "to=10", "from=20&to=10", "from=-1&to=5", "from=x&to=5"])
def test_bad_clip_ranges_are_not_found(running, query):
    put_wav(running)
    assert call(running, f"/clip?{query}&t={running.token}", headers={})[0] == 404


def answered(srv):
    add_question(srv)
    status, _ = call(srv, "/api/answer", {"question": "q-0901", "speaker": "本人", "words": "前の言葉"})
    assert status == 200


def test_edit_changes_the_answer_and_the_kata_and_keeps_the_old_words(running):
    answered(running)
    status, _ = call(running, "/api/edit", {"question": "q-0901", "speaker": "本人", "words": "直した言葉"})
    assert status == 200
    q = next(x for x in store.read(running.neta.questions, "questions")["questions"] if x["id"] == "q-0901")
    assert q["answer"]["words"] == "直した言葉"
    items = store.read(running.neta.kata, "book")["items"]
    item = next(i for i in items if i["source"].get("question_id") == "q-0901")
    assert item["text"] == item["source"]["words"] == "直した言葉"
    assert item["edited_by"] == "performer"
    assert (item["history"][-1]["before"], item["history"][-1]["after"]) == ("前の言葉", "直した言葉")
    doc = json.loads((running.out / "view.json").read_text(encoding="utf-8"))
    assert "直した言葉" in json.dumps(doc["kata"], ensure_ascii=False)


def test_edit_with_the_same_words_leaves_no_history(running):
    answered(running)
    before = store.read(running.neta.kata, "book")
    status, _ = call(running, "/api/edit", {"question": "q-0901", "speaker": "本人", "words": "前の言葉"})
    assert status == 200
    assert store.read(running.neta.kata, "book") == before


@pytest.mark.parametrize(
    "body",
    [
        {"question": "q-0901", "speaker": "本人", "words": "  "},
        {"question": "q-0901", "speaker": "", "words": "言葉"},
        {"question": "q-9999", "speaker": "本人", "words": "言葉"},
    ],
)
def test_edit_refuses_empty_words_or_an_unknown_question(running, body):
    answered(running)
    status, _ = call(running, "/api/edit", body)
    assert status == 400


def test_edit_refuses_a_question_without_an_answer(running):
    add_question(running)
    status, _ = call(running, "/api/edit", {"question": "q-0901", "speaker": "本人", "words": "言葉"})
    assert status == 400


def test_edit_needs_the_token(running):
    answered(running)
    body = {"question": "q-0901", "speaker": "本人", "words": "言葉"}
    status, _ = call(running, "/api/edit", body, headers={"Content-Type": "application/json"})
    assert status == 403


def test_viewer_lets_the_performer_fix_an_answer():
    html = (Path(__file__).parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
    assert "あとから直せます" in html and "あとから直せません" not in html
    assert "/api/edit" in html


def test_clip_plays_the_koza_the_screen_names_not_the_open_one(running):
    neta, koza = second_koza(running)
    for path, rate in ((running.neta.koza_dir(K), 8000), (running.neta.sibling(neta).koza_dir(koza), 16000)):
        with wave.open(str(path / "audio.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\0\0" * rate * 3)
    query = urllib.parse.urlencode({"from": 0, "to": 1, "neta": neta, "koza": koza, "t": running.token})
    status, data = call(running, f"/clip?{query}", headers={})
    assert status == 200
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 16000


def test_clip_does_not_leave_the_performer(running):
    query = urllib.parse.urlencode({"from": 0, "to": 1, "neta": "../..", "koza": K, "t": running.token})
    status, _ = call(running, f"/clip?{query}", headers={})
    assert status == 404


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/answer", {"question": "q-0901", "speaker": "本人", "words": "言葉"}),
        ("/api/dismiss", {"question": "q-0901", "speaker": "本人", "reason": "理由"}),
        ("/api/edit", {"question": "q-0901", "speaker": "本人", "words": "言葉"}),
        (
            "/api/decide",
            {
                "scene": "scene-01",
                "position": "within",
                "speaker": "本人",
                "words": "言葉",
                "card": "札",
                "why": "理由",
            },
        ),
    ],
)
def test_a_stale_tab_cannot_write_into_the_koza_opened_elsewhere(running, path, body):
    add_question(running)
    neta, koza = second_koza(running)
    assert call(running, "/api/open", {"neta": neta, "koza": koza})[0] == 200
    before = [p.read_bytes() for p in sorted(running.neta.base.parent.rglob("*.json"))]
    status, data = call(running, path, {**body, "neta": N, "koza": K})
    assert status == 400 and "別の高座" in data.decode()
    assert [p.read_bytes() for p in sorted(running.neta.base.parent.rglob("*.json"))] == before


def test_viewer_sends_which_koza_it_shows_with_every_write():
    html = (Path(__file__).parents[1] / "viewer" / "index.html").read_text(encoding="utf-8")
    assert "JSON.stringify({ neta: NETA, koza: KOZA, ...body })" in html


def test_edit_keeps_a_reason_decided_separately(running):
    answered(running)
    kata = store.read(running.neta.kata, "book")
    item = next(i for i in kata["items"] if i["source"].get("question_id") == "q-0901")
    item["reason"] = "別に決めた理由"
    store.write(running.neta.kata, kata, "book")
    performer.edit_answer(running.neta, question="q-0901", speaker="本人", words="直した言葉")
    items = store.read(running.neta.kata, "book")["items"]
    assert next(i for i in items if i["id"] == item["id"])["reason"] == "別に決めた理由"


def test_edit_retried_after_a_crash_does_not_double_the_history(running):
    answered(running)
    saved = running.neta.questions.read_bytes()
    performer.edit_answer(running.neta, question="q-0901", speaker="本人", words="直した言葉")
    running.neta.questions.write_bytes(saved)  # 型を書いた後、問いを書く前に落ちた
    performer.edit_answer(running.neta, question="q-0901", speaker="本人", words="直した言葉")
    items = store.read(running.neta.kata, "book")["items"]
    item = next(i for i in items if i["source"].get("question_id") == "q-0901")
    assert [h["after"] for h in item["history"]] == ["前の言葉", "直した言葉"]
    q = next(x for x in store.read(running.neta.questions, "questions")["questions"] if x["id"] == "q-0901")
    assert q["answer"]["words"] == "直した言葉"


@pytest.mark.parametrize("path", sorted(WRITES))
def test_write_without_the_koza_it_was_made_on_is_refused(running, path):
    status, _ = call(running, path, data=json.dumps({"question": "q-0001", "words": "x"}).encode())
    assert status == 400


def test_edit_by_someone_else_changes_who_wrote_the_answer(running):
    add_question(running)
    call(running, "/api/answer", {"question": "q-0901", "speaker": "本人", "words": "前の言葉"})
    body = {"question": "q-0901", "speaker": "代筆", "words": "後の言葉"}
    assert call(running, "/api/edit", body)[0] == 200
    kata = store.read(running.neta.kata, "book")
    item = next(i for i in kata["items"] if i["source"].get("question_id") == "q-0901")
    rows = store.read(running.neta.questions, "questions")["questions"]
    said = next(q for q in rows if q["id"] == "q-0901")["answer"]
    assert (item["source"]["speaker"], item["source"]["recorded_at"]) == ("代筆", said["recorded_at"])
    assert item["history"][0]["by"] == "本人"
    assert item["history"][-1]["before"] == "前の言葉"


@pytest.mark.parametrize("venue", ["会場\nA", "長" * 101])
def test_koza_refuses_a_venue_that_is_not_one_short_line(running, venue):
    assert call(running, "/api/koza", {"neta": N, "koza": K, "date": "2026-03-04", "venue": venue})[0] == 400


def test_edit_drops_the_recheck_of_the_old_answer(running):
    add_question(running)
    call(running, "/api/answer", {"question": "q-0901", "speaker": "本人", "words": "前の言葉"})
    side = {"verdict": "not_written", "quote": "", "reply": "無い"}
    rows = [
        {"question_id": q, "before": side, "after": side, "cites_answer": False} for q in ("q-0901", "q-0001")
    ]
    store.write(running.neta.recheck, {"schema_version": 1, "koza_id": K, "rows": rows}, "recheck")
    body = {"question": "q-0901", "speaker": "本人", "words": "後の言葉"}
    assert call(running, "/api/edit", body)[0] == 200
    left = store.read(running.neta.recheck, "recheck")["rows"]
    assert [r["question_id"] for r in left] == ["q-0001"]



# ---------- 録音を入れる ----------


class FakeCommand:
    """netacho のコマンドの代わり。呼ばれた引数を控える。API も Whisper も呼ばない。"""

    def __init__(self, fail_at=None):
        self.calls, self.fail_at = [], fail_at

    def __call__(self, argv, log, env):
        self.calls.append(argv)
        log.write_text("演者の言葉（応答に出てはいけない）", encoding="utf-8")
        return 2 if argv[0] == self.fail_at else 0


def start_server(neta, koza, out, command):
    srv = server.AnswerServer(neta, koza, out=out, runner=jobs.Runner(command))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def uploading(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    out = tmp_path / "view"
    neta = store.Neta(P, N, root)
    view.write(neta, K, out)
    srv = start_server(neta, K, out, FakeCommand())
    yield srv
    srv.shutdown()
    srv.server_close()


def upload(srv, data=b"RIFF-dummy", *, headers=None, **params):
    query = {"neta": "傘貸し長屋", "date": "2026-03-04", "venue": "hall-b"}
    query.update({"terms": "テスト太郎", "name": "a.m4a"})
    query.update(params)
    sent = headers
    if sent is None:
        sent = {"Content-Type": "application/octet-stream", "X-Netacho-Token": srv.token}
    return call(srv, "/api/upload?" + urllib.parse.urlencode(query), headers=sent, data=data)


def wait_job(srv):
    for _ in range(200):
        if not srv.runner.busy:
            return srv.runner.status()
        time.sleep(0.01)
    raise AssertionError("処理が終わらない")


def job_state(srv, token=True):
    headers = {"X-Netacho-Token": srv.token} if token else {}
    return call(srv, "/api/job", headers=headers)


def test_upload_saves_the_recording_and_runs_every_stage(uploading):
    status, body = upload(uploading)
    assert status == 200, body
    done = wait_job(uploading)
    assert done["state"] == "done" and (done["neta"], done["koza"]) == ("傘貸し長屋", "2026-03-04-hall-b")
    kdir = uploading.neta.sibling("傘貸し長屋").koza_dir("2026-03-04-hall-b")
    assert (kdir / "source.m4a").read_bytes() == b"RIFF-dummy"
    assert not list(kdir.glob("*.part"))
    calls = uploading.runner.command.calls
    assert [c[0] for c in calls] == ["auth", "prep", "build", "note", "seisho"]
    assert calls[1][calls[1].index("--audio") + 1] == str(kdir / "source.m4a")
    assert uploading.koza_id == K  # 開いている高座は勝手に変えない


def test_job_state_needs_the_token_and_shows_the_budget(uploading):
    assert job_state(uploading, token=False)[0] == 403
    status, body = job_state(uploading)
    doc = json.loads(body)
    assert status == 200 and doc["job"] is None and doc["busy"] is False
    assert (doc["run_limit_usd"], doc["total_limit_usd"]) == (5.0, 30.0)


def test_job_state_tells_only_whether_the_key_exists(uploading, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    status, body = job_state(uploading)
    assert json.loads(body)["api_key"] is True
    assert b"sk-test-secret" not in body
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert json.loads(job_state(uploading)[1])["api_key"] is False


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "application/octet-stream"},
        {"Content-Type": "application/json", "X-Netacho-Token": "TOKEN"},
        {"Content-Type": "application/octet-stream", "X-Netacho-Token": "TOKEN", "Origin": "http://evil.example"},
    ],
)
def test_upload_needs_the_token_the_type_and_the_origin(uploading, headers):
    headers = {k: (uploading.token if v == "TOKEN" else v) for k, v in headers.items()}
    status, _ = upload(uploading, headers=headers)
    assert status == 403
    assert uploading.runner.status() is None


@pytest.mark.parametrize(
    "params",
    [
        {"venue": "a b"},
        {"venue": ""},
        {"neta": "../x"},
        {"date": "2026-02-30"},
        {"name": "memo.txt"},
        {"terms": "a\nb"},
        {"neta": N, "date": "2026-01-01", "venue": "dummy-hall"},  # もうある高座
    ],
)
def test_upload_refuses_bad_fields(uploading, params):
    status, body = upload(uploading, **params)
    assert status == 400
    assert "error" in json.loads(body)
    assert uploading.runner.status() is None and not uploading.runner.busy


def test_upload_can_compare_with_an_earlier_koza(uploading):
    status, body = upload(uploading, with_neta=N, with_koza=K)
    assert status == 200, body
    assert wait_job(uploading)["state"] == "done"
    last = uploading.runner.command.calls[-1]
    assert last[0] == "compare" and last[last.index("--with-koza") + 1] == K


@pytest.mark.parametrize(
    "params",
    [
        {"with_neta": N, "with_koza": "2099-01-01-none"},
        {"with_neta": "../x", "with_koza": K},
    ],
)
def test_upload_refuses_a_partner_that_is_not_a_koza(uploading, params):
    status, _ = upload(uploading, **params)
    assert status == 400
    assert uploading.runner.status() is None and not uploading.runner.busy


def check(srv, **params):
    return upload(srv, data=b"", **{"check": "1", "size": "10", **params})


def test_check_validates_without_reading_or_reserving(uploading):
    assert check(uploading)[0] == 200
    assert uploading.runner.status() is None and not uploading.runner.busy
    assert not uploading.neta.sibling("傘貸し長屋").base.exists()
    assert check(uploading, venue="a b")[0] == 400
    assert check(uploading, size="abc")[0] == 400
    assert check(uploading, size=str(3 * 1024**3))[0] == 400
    uploading.runner.reserve()
    assert check(uploading)[0] == 409


def test_upload_refuses_a_second_while_busy(uploading):
    uploading.runner.reserve()
    status, _ = upload(uploading)
    assert status == 409
    assert not uploading.neta.sibling("傘貸し長屋").base.exists()


def test_failed_stage_is_reported_without_the_log_and_can_be_retried(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    out = tmp_path / "view"
    neta = store.Neta(P, N, root)
    view.write(neta, K, out)
    srv = start_server(neta, K, out, FakeCommand(fail_at="build"))
    try:
        assert upload(srv)[0] == 200
        wait_job(srv)
        status, body = job_state(srv)
        doc = json.loads(body)
        assert doc["job"]["state"] == "failed" and doc["job"]["failed"] == "帳面と弟子の問い"
        assert doc["job"]["code"] == 2
        assert "演者の言葉" not in body.decode()
        srv.runner.command.fail_at = None
        status, body = call(srv, "/api/retry", {})
        assert status == 200
        assert wait_job(srv)["state"] == "done"
        assert [c[0] for c in srv.runner.command.calls[-3:]] == ["build", "note", "seisho"]
        assert all("--reuse" in c for c in srv.runner.command.calls[-3:])
    finally:
        srv.shutdown()
        srv.server_close()


def test_retry_without_a_failed_job_is_refused(uploading):
    assert call(uploading, "/api/retry", {})[0] == 409


def test_server_without_a_koza_serves_the_start_screen(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    out = tmp_path / "view"
    view.write_empty(out)
    srv = start_server(store.Neta(P, server.NO_NETA, root), None, out, FakeCommand())
    try:
        assert call(srv, "/view.json")[0] == 404
        assert call(srv, "/")[0] == 200
        status, _ = call(srv, "/api/answer", {"question": "q-1", "speaker": "本人", "words": "x"})
        assert status == 400  # 開いている高座が無いので書かない
        assert upload(srv)[0] == 200
        assert wait_job(srv)["state"] == "done"
        assert not (root / P / server.NO_NETA).exists()
    finally:
        srv.shutdown()
        srv.server_close()


def test_open_works_from_the_start_screen(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    out = tmp_path / "view"
    view.write_empty(out)
    srv = start_server(store.Neta(P, server.NO_NETA, root), None, out, FakeCommand())
    try:
        assert call(srv, "/api/open", {"neta": N, "koza": K})[0] == 200
        assert (srv.neta.neta, srv.koza_id) == (N, K)
        assert json.loads((out / "view.json").read_text(encoding="utf-8"))["koza"]["id"] == K
    finally:
        srv.shutdown()
        srv.server_close()


def test_serve_falls_back_to_the_start_screen_for_a_half_built_koza(tmp_path, monkeypatch):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    neta = store.Neta(P, N, root)
    neta.hanashi.unlink()  # build の前で止まった高座
    out = tmp_path / "view"
    started = {}

    def fake_forever(self):
        started["koza"] = self.koza_id
        raise KeyboardInterrupt

    monkeypatch.setattr(server.AnswerServer, "serve_forever", fake_forever)
    server.serve(neta, K, port=0, out=out, browser=False)
    assert started["koza"] is None
    assert not (out / "view.json").exists() and (out / "index.html").exists()


def capture_serve(monkeypatch):
    got = {}
    monkeypatch.setattr(server, "serve", lambda neta, koza, **kw: got.update(neta=neta.neta, koza=koza))
    return got


def test_cli_serve_opens_the_newest_koza_when_none_is_given(tmp_path, monkeypatch):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    monkeypatch.setenv("NETACHO_DATA", str(root))
    got = capture_serve(monkeypatch)
    cli.main(["serve", "--performer", P, "--no-open"])
    assert got == {"neta": N, "koza": K}


def test_cli_serve_starts_empty_without_any_koza(tmp_path, monkeypatch):
    monkeypatch.setenv("NETACHO_DATA", str(tmp_path))
    got = capture_serve(monkeypatch)
    cli.main(["serve", "--performer", P, "--no-open"])
    assert got == {"neta": server.NO_NETA, "koza": None}


def test_cli_serve_needs_both_neta_and_koza(tmp_path, monkeypatch):
    monkeypatch.setenv("NETACHO_DATA", str(tmp_path))
    capture_serve(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["serve", "--performer", P, "--neta", N])


def raw_post(srv, path, head, body):
    """本文を途中まで送って切る。urllib では送れない形。"""
    with socket.create_connection(("127.0.0.1", srv.port)) as sock:
        lines = [f"POST {path} HTTP/1.1", f"Host: 127.0.0.1:{srv.port}", *head, "", ""]
        sock.sendall("\r\n".join(lines).encode() + body)
        sock.shutdown(socket.SHUT_WR)
        return sock.recv(4096)


def upload_query():
    q = {"neta": "傘貸し長屋", "date": "", "venue": "hall-c", "name": "a.wav"}
    return "/api/upload?" + urllib.parse.urlencode(q)


def test_a_cut_upload_leaves_no_file_and_frees_the_slot(uploading):
    head = [
        "Content-Type: application/octet-stream",
        f"X-Netacho-Token: {uploading.token}",
        "Content-Length: 100000",
    ]
    raw_post(uploading, upload_query(), head, b"x" * 10)
    kdir = uploading.neta.sibling("傘貸し長屋").koza_dir("0000-00-00-hall-c")
    for _ in range(100):
        if not uploading.runner.busy:
            break
        time.sleep(0.02)
    assert not uploading.runner.busy and uploading.runner.status() is None
    assert not list(kdir.glob("source*"))


def test_upload_without_a_length_is_refused(uploading):
    head = ["Content-Type: application/octet-stream", f"X-Netacho-Token: {uploading.token}"]
    reply = raw_post(uploading, upload_query(), head, b"")
    assert reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.1 400")
    assert "長さが無い" in reply.decode()
    assert not uploading.runner.busy


def test_upload_over_the_limit_is_refused(uploading, monkeypatch):
    monkeypatch.setattr(server, "MAX_AUDIO", 5)
    status, body = upload(uploading, data=b"123456")
    assert status == 400 and "2 GB" in json.loads(body)["error"]
    assert uploading.runner.status() is None
    assert not uploading.neta.sibling("傘貸し長屋").base.exists()


def test_upload_needs_room_on_the_disk(uploading, monkeypatch):
    monkeypatch.setattr(server, "_room_for", lambda kdir, size: False)
    assert check(uploading)[0] == 400
    status, body = upload(uploading)
    assert status == 400 and "空き" in json.loads(body)["error"]
    assert uploading.runner.status() is None and not uploading.runner.busy


def test_room_for_measures_an_existing_parent(tmp_path):
    assert ROOM_FOR(tmp_path / "a" / "b" / "c", 1)
    assert not ROOM_FOR(tmp_path / "a", 10**18)


def test_room_for_refuses_when_it_cannot_measure(tmp_path, monkeypatch):
    def broken(path):
        raise OSError("測れない")

    monkeypatch.setattr(server.shutil, "disk_usage", broken)
    assert not ROOM_FOR(tmp_path, 1)
