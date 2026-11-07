"""視聴画面と、演者の答えの受け口。手元で演者が答えるためだけの小さなサーバ。LLM は使わない。

127.0.0.1 だけで待ち受ける。別のサイトの画面から書き込まれないように、起動ごとのトークン、
Host、Origin を確かめる。配信するのは画面と view.json だけ。演者の言葉はログに出さない。
読み取りにトークンは要らない。`make viewer` と同じで、同じ機械の上の物は読める前提。
録音だけは別で、トークンのある画面に、問いの所の短い切り抜きだけを渡す。`out/view` には置かない。
画面から録音を受け取り、jobs で段階ごとに処理する。処理の状態と費用の累計も、トークンのある画面にだけ渡す。
高座が 1 本も無いときは、高座を開かずに起動する（録音を入れる画面だけ）。
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import threading
import wave
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import budget, jobs, performer, pipeline, store, view

MAX_BODY = 64 * 1024
MAX_AUDIO = 2 * 1024**3  # 録音の上限。2 GB
CHUNK = 1024 * 1024
NO_NETA = "-"  # 高座を開いていないときの仮の演目。フォルダは作らない
POSITIONS = ("before", "within", "after")
CLIP_BEFORE, CLIP_AFTER = 12.0, 6.0  # 笑いの前後に聞かせる長さ。秒
CLIP_MAX = 90.0  # 切り抜きの上限。録音を丸ごと渡さないため。秒
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/view.json": ("view.json", "application/json; charset=utf-8"),
}


class AnswerServer(ThreadingHTTPServer):
    """受け付けは並列。ブラウザの空の接続で画面が止まらないように。書き込みは鍵で 1 件ずつにする。"""

    daemon_threads = True

    def __init__(
        self,
        neta: store.Neta,
        koza_id: str | None,
        *,
        out: Path,
        port: int = 0,
        runner: jobs.Runner | None = None,
    ) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.neta, self.koza_id, self.out = neta, koza_id, out
        self.runner = runner or jobs.Runner()
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
    if (body.get("neta"), body.get("koza")) != (server.neta.neta, server.koza_id):
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

    def _token_ok(self) -> bool:
        origin = self.headers.get("Origin")
        given = self.headers.get("X-Netacho-Token", "")
        return (
            self._host_ok()
            and (origin is None or urlsplit(origin).netloc in self.server.hosts)
            and hmac.compare_digest(given.encode(), self.server.token.encode())
        )

    def _write_ok(self) -> bool:
        return self._token_ok() and self.headers.get_content_type() == "application/json"

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
        if parts.path == "/api/job":
            self._job()
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

    def _job(self) -> None:
        """処理の状態と、費用の累計と上限。始める前の確認に使う。"""
        if not self._token_ok():
            self._json(403, {"error": "受け付けない"})
            return
        try:
            spent = round(budget.Budget().total_spent(), 2)
        except (OSError, ValueError, KeyError, TypeError):
            spent = None
        self._json(
            200,
            {
                "job": self.server.runner.status(),
                "busy": self.server.runner.busy,
                "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),  # 有無だけ。値は出さない
                "spent_usd": spent,
                "run_limit_usd": budget.RUN_LIMIT_USD,
                "total_limit_usd": budget.TOTAL_LIMIT_USD,
            },
        )

    def _upload(self, query: str) -> None:
        """録音を受け取り、処理を始める。演目、公演日、会場、語、ファイル名は URL の引数で受ける。

        `check=1` なら本文を読まずに、入力、処理の空き、ディスクの空きだけを確かめる。
        大きな録音を送り始めてから断ると、ブラウザには理由が届かないことがあるので、画面は先にこれを呼ぶ。
        受け取りの途中で送るのが止まっても、Handler.timeout（読み取りごと 10 秒）で切れて枠を返す。
        """
        if not self._token_ok() or self.headers.get_content_type() != "application/octet-stream":
            self._json(403, {"error": "受け付けない"})
            return
        args = parse_qs(query)
        arg = lambda key: args.get(key, [""])[0]  # noqa: E731
        check = arg("check") == "1"
        try:  # 確かめるだけのときは、これから送る録音の大きさ
            length = int(arg("size") if check else self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(400, {"error": "長さが無い"})
            return
        if not 0 < length <= MAX_AUDIO:
            self._json(400, {"error": "録音は 2 GB までで"})
            return
        try:
            neta_name, koza_id = jobs.new_koza(arg("neta"), arg("date"), arg("venue"))
            suffix = jobs.audio_suffix(arg("name"))
            terms = jobs.parse_terms(arg("terms"))
            kdir = self.server.neta.sibling(neta_name).koza_dir(koza_id)
            if (kdir / "koza.json").exists():
                raise jobs.JobError(
                    "同じ演目に、同じ日付と会場の高座がもうあります。途中で止まった高座なら、"
                    "端末で netacho build、note、seisho に --reuse を付けて続きを回せます"
                )
            compare = None
            if arg("with_koza"):
                other, other_koza = _target(self.server, {"neta": arg("with_neta"), "koza": arg("with_koza")})
                if (other.neta, other_koza) == (neta_name, koza_id):
                    raise jobs.JobError("同じ高座とは比べられません")
                compare = (other.neta, other_koza)
        except (jobs.JobError, store.StoreError, performer.PerformerError) as e:
            self._json(400, {"error": str(e)})
            return
        except ValueError:  # パスに使えない字（NUL など）が、比べる相手の名前などに入っていた
            self._json(400, {"error": "入力が正しくありません"})
            return
        if not _room_for(kdir, length):
            message = "ディスクの空きが足りません。録音の 2 倍と 1 GB ほどの空きを作ってください"
            self._json(400, {"error": message})
            return
        runner = self.server.runner
        if check:
            if runner.busy:
                self._json(409, {"error": "別の録音を処理しています。終わってから入れてください"})
                return
            self._json(200, {"ok": True})
            return
        try:
            runner.reserve()
        except jobs.JobError as e:
            self._json(409, {"error": str(e)})
            return
        try:  # ここから先で何が起きても、始められなかったら枠を返す
            dest = self._receive(kdir, suffix, length)
        except Exception:
            runner.release()
            self._json(400, {"error": "録音を受け取れなかった"})
            return
        try:
            job = jobs.Job(self.server.neta.performer, neta_name, koza_id, dest, terms, compare=compare)
            runner.start(job, kdir)
        except Exception as e:
            runner.release()
            dest.unlink(missing_ok=True)
            print(f"処理を始められなかった（{type(e).__name__}）", flush=True)  # 種類だけ。中身は出さない
            self._json(500, {"error": "処理を始められなかった"})
            return
        self._json(200, {"ok": True, "job": job.status()})

    def _receive(self, kdir: Path, suffix: str, length: int) -> Path:
        """本文を少しずつ書く。途中で切れたら、書きかけを残さない。"""
        kdir.mkdir(parents=True, exist_ok=True)
        dest = kdir / f"source{suffix}"
        part = kdir / f"source{suffix}.part"
        left = length
        try:
            with part.open("wb") as f:
                while left:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        raise OSError("本文が途中で切れた")
                    f.write(chunk)
                    left -= len(chunk)
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)
        return dest

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == "/api/upload":
            self._upload(urlsplit(self.path).query)
            return
        if urlsplit(self.path).path == "/api/retry":
            self._retry()
            return
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

    def _retry(self) -> None:
        """落ちた段階から、--reuse を付けてやり直す。"""
        if not self._token_ok():
            self._json(403, {"error": "受け付けない"})
            return
        try:
            self.server.runner.retry(self.server.neta)
        except jobs.JobError as e:
            self._json(409, {"error": str(e)})
            return
        self._json(200, {"ok": True, "job": self.server.runner.status()})

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
            if self.server.koza_id is not None:
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
        performer.edit_koza(neta, koza_id, when=_text(body, "date"), venue=_text(body, "venue"))
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


def _room_for(kdir: Path, size: int) -> bool:
    """録音と、prep が作る wav や途中の物の分の空きがあるか。録音の 2 倍と 1 GB を見込む。"""
    where = kdir
    while not where.exists():  # まだ無いフォルダは、ある所まで遡って測る
        where = where.parent
    try:
        return shutil.disk_usage(where).free >= 2 * size + 1024**3
    except OSError:  # 測れなければ受け取らない
        return False


def _target(server: AnswerServer, body: dict) -> tuple[store.Neta, str]:
    """画面が指した高座。同じ演者の物だけ。名前の検査は store がする。"""
    neta = server.neta.sibling(_text(body, "neta"))
    koza_id = _text(body, "koza")
    if not (neta.koza_dir(koza_id) / "koza.json").is_file():
        raise performer.PerformerError("高座が無い")
    return neta, koza_id


def serve(
    neta: store.Neta, koza_id: str | None, *, port: int, out: Path = view.OUT, browser: bool = True
) -> None:
    """koza_id が None なら、高座を開かずに、録音を入れる画面だけで起動する。"""
    try:
        if koza_id is not None:
            view.write(neta, koza_id, out)
    except (OSError, ValueError) as e:  # 作りかけの高座でも、録音を入れる画面は開く
        print(f"{koza_id} を開けません（{type(e).__name__}）。録音を入れる画面だけを開きます", flush=True)
        koza_id = None
    if koza_id is None:
        view.write_empty(out)
    try:
        server = AnswerServer(neta, koza_id, out=out, port=port)
    except OSError as e:
        raise SystemExit(f"ポート {port} を使えません。--port で別の番号を指定してください") from e
    print(
        f"開く：{server.url()}（止めるときは Ctrl-C）", flush=True
    )  # 出力先が端末でなくても、すぐ見えるように
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY がありません。見るのと答えるのはできますが、録音の処理は鍵の確認で止まります",
            flush=True,
        )
    if browser:
        webbrowser.open(server.url())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
