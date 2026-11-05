"""視聴画面と、演者の答えの受け口。手元で演者が答えるためだけの小さなサーバ。LLM は使わない。

127.0.0.1 だけで待ち受ける。別のサイトの画面から書き込まれないように、起動ごとのトークン、
Host、Origin を確かめる。配信するのは画面と view.json だけ。演者の言葉はログに出さない。
読み取りにトークンは要らない。`make viewer` と同じで、同じ機械の上の物は読める前提。
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import performer, pipeline, store, view

MAX_BODY = 64 * 1024
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/view.json": ("view.json", "application/json; charset=utf-8"),
}


class AnswerServer(ThreadingHTTPServer):
    """受け付けは並列。ブラウザの空の接続で画面が止まらないように。書き込みは鍵で 1 件ずつにする。"""

    daemon_threads = True

    def __init__(self, neta: store.Neta, koza_id: str, *, out: Path, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.neta, self.koza_id, self.out = neta, koza_id, out
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(24)
        self.port = self.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def url(self) -> str:
        return f"http://localhost:{self.port}/?t={self.token}"


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise performer.PerformerError(f"{key} が空")
    return value


class Handler(BaseHTTPRequestHandler):
    server: AnswerServer
    timeout = 10  # 本文の来ない接続を残さない

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # URL のトークンも本文も、ログに出さない

    def _send(self, status: int, data: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, doc: dict) -> None:
        self._send(status, json.dumps(doc, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        return self.headers.get("Host") in self.server.hosts  # 名前の差し替えで読まれないように

    def _write_ok(self) -> bool:
        origin = self.headers.get("Origin")
        given = self.headers.get("X-Netacho-Token", "")
        return (
            self._host_ok()
            and (origin is None or urlsplit(origin).netloc in self.server.hosts)
            and hmac.compare_digest(given.encode(), self.server.token.encode())
            and self.headers.get_content_type() == "application/json"
        )

    def do_GET(self) -> None:  # noqa: N802
        found = FILES.get(urlsplit(self.path).path)
        if not self._host_ok() or found is None:
            self._json(404, {"error": "無い"})
            return
        name, kind = found
        try:
            data = (self.server.out / name).read_bytes()
        except OSError:
            self._json(404, {"error": "無い"})
            return
        self._send(200, data, kind)

    def do_POST(self) -> None:  # noqa: N802
        action = {"/api/answer": self._answer, "/api/dismiss": self._dismiss}.get(urlsplit(self.path).path)
        if action is None or not self._write_ok():
            self._json(403, {"error": "受け付けない"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(400, {"error": "長さが無い"})
            return
        if not 0 < length <= MAX_BODY:
            self._json(400, {"error": "長さが合わない"})
            return
        with self.server.lock:  # 帳面の読み書きが重ならないように
            self._save(action, length)

    def _save(self, action: Callable[[dict], Callable[[], None]], length: int) -> None:
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
            after = action(body)
        except performer.PerformerError as e:
            self._json(400, {"error": str(e)})
        except ValueError:
            self._json(400, {"error": "形が違う"})
        except Exception:  # 演者の言葉を応答に出さない
            self._json(500, {"error": "保存できなかった"})
            return
        try:  # ここから先で落ちても、保存は済んでいる。「保存できなかった」と言わない
            after()
            view.write(self.server.neta, self.server.koza_id, self.server.out)
        except Exception:
            self._json(200, {"ok": True, "warning": "保存しましたが、画面を更新できませんでした"})
            return
        self._json(200, {"ok": True})

    def _answer(self, body: dict) -> Callable[[], None]:
        neta = self.server.neta
        performer.answer(
            neta,
            question=_text(body, "question"),
            speaker=_text(body, "speaker"),
            words=_text(body, "words"),
        )
        return lambda: pipeline.after_answer(neta)

    def _dismiss(self, body: dict) -> Callable[[], None]:
        performer.dismiss(
            self.server.neta,
            question=_text(body, "question"),
            speaker=_text(body, "speaker"),
            reason=_text(body, "reason"),
        )
        return lambda: None


def serve(neta: store.Neta, koza_id: str, *, port: int, out: Path = view.OUT) -> None:
    view.write(neta, koza_id, out)
    server = AnswerServer(neta, koza_id, out=out, port=port)
    print(f"開く：{server.url()}（止めるときは Ctrl-C）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
