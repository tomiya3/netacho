"""視聴画面と、演者の答えの受け口。手元で演者が答えるためだけの小さなサーバ。LLM は使わない。

127.0.0.1 だけで待ち受ける。別のサイトの画面から書き込まれないように、起動ごとのトークン、
Host、Origin を確かめる。配信するのは画面と view.json だけ。演者の言葉はログに出さない。
読み取りにトークンは要らない。`make viewer` と同じで、同じ機械の上の物は読める前提。
録音だけは別で、トークンのある画面に、問いの所の短い切り抜きだけを渡す。`out/view` には置かない。
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
import wave
import webbrowser
from collections.abc import Callable
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import performer, pipeline, store, view

MAX_BODY = 64 * 1024
POSITIONS = ("before", "within", "after")
CLIP_BEFORE, CLIP_AFTER = 12.0, 6.0  # 笑いの前後に聞かせる長さ。秒
CLIP_MAX = 90.0  # 切り抜きの上限。録音を丸ごと渡さないため。秒
UNKNOWN_DATE = "0000-00-00"  # 公演日の分からない高座
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


def clip_wav(path: Path, t0: float, t1: float) -> bytes:
    """録音の t0 から t1 までの切り抜き。長さは CLIP_MAX まで。"""
    with wave.open(str(path), "rb") as src:
        rate = src.getframerate()
        start = max(0, int(t0 * rate))
        if start >= src.getnframes():
            raise ValueError("録音の外")
        src.setpos(start)
        frames = src.readframes(int(min(t1 - t0, CLIP_MAX) * rate))
        out = BytesIO()
        with wave.open(out, "wb") as dst:
            dst.setnchannels(src.getnchannels())
            dst.setsampwidth(src.getsampwidth())
            dst.setframerate(rate)
            dst.writeframes(frames)
    return out.getvalue()


def _clip_range(args: dict) -> tuple[float, float]:
    """`from` と `to` か、笑いの時刻 `at` から、切り抜く範囲を決める。"""
    if "at" in args:
        at = float(args["at"][0])
        t0, t1 = at - CLIP_BEFORE, at + CLIP_AFTER
        limit = at
    else:
        t0, t1 = float(args["from"][0]), float(args["to"][0])
        limit = t0
    if not 0 <= limit < float("inf") or not t0 < t1:
        raise ValueError
    return t0, t1


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise performer.PerformerError(f"{key} が空")
    return value


def _same_koza(server: AnswerServer, body: dict) -> None:
    """別のタブで高座を開き直していたら、古い画面の書き込みを断る。問いの番号は演目ごとの連番なので。"""
    shown = (body.get("neta", server.neta.neta), body.get("koza", server.koza_id))
    if shown != (server.neta.neta, server.koza_id):
        raise performer.PerformerError("別の高座が開かれています。開き直してください")


class Handler(BaseHTTPRequestHandler):
    server: AnswerServer
    timeout = 10  # 本文の来ない接続を残さない

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # URL のトークンも本文も、ログに出さない

    def _send(self, status: int, data: bytes, kind: str, extra: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")  # URL にトークンがある
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

    def _audio(self, args: dict) -> Path:
        """画面が指した高座の録音。指定が無ければ、開いている高座か、比較の 2 本目。"""
        if "koza" in args:  # 高座を指す。サーバの開いている高座が変わっても、別の録音を鳴らさない
            return self.server.neta.sibling(args["neta"][0]).koza_dir(args["koza"][0]) / "audio.wav"
        side = args.get("side", ["a"])[0]
        kdir = self.server.neta.koza_dir(self.server.koza_id)
        if side != "b":
            return kdir / "audio.wav"
        doc = store.read(kdir / "compare.json", "compare")
        return self.server.neta.sibling(doc["other_neta"]).koza_dir(doc["koza_b"]) / "audio.wav"

    def _clip(self, query: str) -> None:
        args = parse_qs(query)
        token = args.get("t", [""])[0]
        if not hmac.compare_digest(token.encode(), self.server.token.encode()):
            self._json(403, {"error": "受け付けない"})
            return
        try:
            t0, t1 = _clip_range(args)
            data = clip_wav(self._audio(args), t0, t1)
        except (KeyError, ValueError, OverflowError, OSError, wave.Error):
            self._json(404, {"error": "無い"})
            return
        self._send_range(data, "audio/wav")

    def _send_range(self, data: bytes, kind: str) -> None:
        """範囲の指定に応える。Safari の audio は 206 が返らないと再生しないことがある。"""
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if match is None:
            self._send(200, data, kind, {"Accept-Ranges": "bytes"})
            return
        start = int(match[1])
        end = min(int(match[2]) if match[2] else len(data) - 1, len(data) - 1)
        if start > end:
            self._send(416, b"", kind, {"Content-Range": f"bytes */{len(data)}"})
            return
        headers = {"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{len(data)}"}
        self._send(206, data[start : end + 1], kind, headers)

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path == "/clip" and self._host_ok():
            self._clip(parts.query)
            return
        found = FILES.get(parts.path)
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
        actions = {
            "/api/answer": self._answer,
            "/api/dismiss": self._dismiss,
            "/api/decide": self._decide,
            "/api/edit": self._edit,
            "/api/open": self._open,
            "/api/koza": self._koza,
        }
        action = actions.get(urlsplit(self.path).path)
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
        try:
            raw = self.rfile.read(length)  # 本文の来ない接続が書き込みを止めないように、鍵の外で読む
        except OSError:
            self._json(400, {"error": "本文が来ない"})
            return
        with self.server.lock:  # 帳面の読み書きが重ならないように
            self._save(action, raw)

    def _save(self, action: Callable[[dict], Callable[[], None]], raw: bytes) -> None:
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
            after = action(body)
        except performer.PerformerError as e:
            self._json(400, {"error": str(e)})
            return
        except ValueError:
            self._json(400, {"error": "形が違う"})
            return
        except Exception:  # 演者の言葉を応答に出さない
            self._json(500, {"error": "保存できなかった"})
            return
        try:  # ここから先で落ちても、保存は済んでいる。「保存できなかった」と言わない
            after()
            view.write(self.server.neta, self.server.koza_id, self.server.out)
        except Exception:
            self._json(
                200,
                {
                    "ok": True,
                    "warning": "保存しましたが、札や画面を更新できませんでした。netacho guide で作り直せます",
                },
            )
            return
        self._json(200, {"ok": True})

    def _answer(self, body: dict) -> Callable[[], None]:
        _same_koza(self.server, body)
        neta = self.server.neta
        performer.answer(
            neta,
            question=_text(body, "question"),
            speaker=_text(body, "speaker"),
            words=_text(body, "words"),
        )
        return lambda: pipeline.after_performer(neta)

    def _edit(self, body: dict) -> Callable[[], None]:
        _same_koza(self.server, body)
        neta = self.server.neta
        performer.edit_answer(
            neta,
            question=_text(body, "question"),
            speaker=_text(body, "speaker"),
            words=_text(body, "words"),
        )
        return lambda: pipeline.after_performer(neta)

    def _decide(self, body: dict) -> Callable[[], None]:
        _same_koza(self.server, body)
        neta = self.server.neta
        position = _text(body, "position")
        if position not in POSITIONS:
            raise performer.PerformerError("位置が違う")
        performer.decide(
            neta,
            speaker=_text(body, "speaker"),
            words=_text(body, "words"),
            card=_text(body, "card"),
            why=_text(body, "why"),
            scene=_text(body, "scene"),
            position=position,
        )
        return lambda: pipeline.after_performer(neta, first_card=True)

    def _open(self, body: dict) -> Callable[[], None]:
        """別の高座を開く。読めると確かめてから切り替える。"""
        neta, koza_id = _target(self.server, body)
        view.payload(neta, koza_id)
        self.server.neta, self.server.koza_id = neta, koza_id
        return lambda: None

    def _koza(self, body: dict) -> Callable[[], None]:
        """公演日と会場を直す。フォルダの名前は変えない。"""
        neta, koza_id = _target(self.server, body)
        when, venue = _text(body, "date") or UNKNOWN_DATE, _text(body, "venue").strip()
        if when != UNKNOWN_DATE:
            date.fromisoformat(when)  # 形が同じでも暦に無い日は受けない
        if not venue:
            raise performer.PerformerError("会場が空")
        path = neta.koza_dir(koza_id) / "koza.json"
        store.write(path, {**store.read(path, "koza"), "date": when, "venue": venue}, "koza")
        return lambda: None

    def _dismiss(self, body: dict) -> Callable[[], None]:
        _same_koza(self.server, body)
        neta = self.server.neta
        performer.dismiss(
            neta,
            question=_text(body, "question"),
            speaker=_text(body, "speaker"),
            reason=_text(body, "reason"),
        )
        return lambda: pipeline.after_performer(neta)


def _target(server: AnswerServer, body: dict) -> tuple[store.Neta, str]:
    """画面が指した高座。同じ演者の物だけ。名前の検査は store がする。"""
    neta = server.neta.sibling(_text(body, "neta"))
    koza_id = _text(body, "koza")
    if not (neta.koza_dir(koza_id) / "koza.json").is_file():
        raise performer.PerformerError("高座が無い")
    return neta, koza_id


def serve(neta: store.Neta, koza_id: str, *, port: int, out: Path = view.OUT, browser: bool = True) -> None:
    view.write(neta, koza_id, out)
    try:
        server = AnswerServer(neta, koza_id, out=out, port=port)
    except OSError as e:
        raise SystemExit(f"ポート {port} を使えません。--port で別の番号を指定してください") from e
    print(
        f"開く：{server.url()}（止めるときは Ctrl-C）", flush=True
    )  # 出力先が端末でなくても、すぐ見えるように
    if browser:
        webbrowser.open(server.url())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
