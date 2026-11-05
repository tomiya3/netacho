"""netacho の入口。流れはスクリプトが決める。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import reactions, store

SCHEMA_VERSION = 1
STALE_AFTER_MARK = ("note_candidates.json", "gaps.json", "note.md", "sokki.md")
# prep をやり直すと意味を失う物
PREP_OUTPUTS = (
    "koza.json",
    "transcript.json",
    "reactions.json",
    "reactions.auto.json",
    "timeline.json",
    "note_candidates.json",
    "gaps.json",
    "note.md",
    "sokki.md",
    "seisho.json",
    "seisho.md",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _koza_meta(args: argparse.Namespace) -> dict:
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-(.+)", args.koza)
    if not m:
        raise SystemExit(f"--koza は 日付-会場 の形で（例 2026-01-01-hall）: {args.koza}")
    koza = {
        "schema_version": SCHEMA_VERSION,
        "id": args.koza,
        "date": m[1],
        "venue": m[2],
        "neta": args.neta,
        "performer_id": args.performer,
        "audio_sha256": _sha256(Path(args.audio)),
    }
    if args.start is not None or args.end is not None:
        koza["excerpt"] = {"from": args.start, "to": args.end}
    return koza


def _to_transcript(detected: dict, koza_id: str) -> dict:
    keys = ("id", "start", "end", "text", "words", "no_speech_prob", "pause_after")
    t = detected["transcript"]
    return {
        "schema_version": SCHEMA_VERSION,
        "koza_id": koza_id,
        "model": t["model"],
        "language": t["language"],
        "segments": [{k: s[k] for k in keys} for s in t["segments"]],
        "vad": [{"start": v["start"], "end": v["end"]} for v in detected["speech"]["segments"]],
    }


def _print_top(doc: dict, n: int = 10) -> None:
    if not doc["reactions"]:
        print("反応の候補を自動では拾えなかった（反応なしとは限らない）。耳で確かめて netacho mark で入れる")
        return
    print(f"反応の候補 {len(doc['reactions'])} 件。強い順に {n} 件（時刻はこの wav の先頭から）：")
    for r in reactions.top(doc, n):
        rank = "-" if r["strength_rank"] is None else r["strength_rank"]
        label = {"laugh": "笑い", "applause": "拍手"}.get(r["kind"], "手")
        print(f"  {rank:>3}  {reactions.clock(r['t0'])} - {reactions.clock(r['t1'])}  {label}  {r['id']}")


def _check_excerpt(args: argparse.Namespace, seconds: float) -> None:
    start, end = args.start, args.end
    if (start is not None and start < 0) or (end is not None and end <= 0):
        raise SystemExit("--from は 0 以上、--to は 0 より大きい値で")
    if start is not None and end is not None and start >= end:
        raise SystemExit("--from は --to より前で")
    if start is not None and start >= seconds:
        raise SystemExit(f"--from が音声の長さ（{seconds:.0f} 秒）を超えている")


def _has_manual_marks(path: Path) -> bool:
    """手の反応があるか。schema に合わない古い物でも、やり直せるように生の JSON で見る。"""
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("reactions", [])
        return any(r.get("method") == "manual" for r in rows)
    except (OSError, ValueError, AttributeError):
        return False


def _books_cite(neta: store.Neta, koza_id: str) -> bool:
    """噺・型がこの高座の区間を指しているか。文字起こしをやり直すと区間 ID がずれる。"""
    for path in (neta.hanashi, neta.kata):
        if path.exists() and koza_id in path.read_text(encoding="utf-8"):
            return True
    return False


def _clear_outputs(neta: store.Neta, kdir: Path, force: bool) -> None:
    """やり直す前に古い生成物を消す（途中で落ちても、新しい wav に古い結果が並ばないように）。"""
    path = kdir / "reactions.json"
    manual = path.exists() and _has_manual_marks(path)
    if not force:
        if manual:
            raise SystemExit("手で付けた反応がある。作り直すなら --force を付ける")
        if _books_cite(neta, kdir.name):
            raise SystemExit("噺・型がこの高座の区間を指している。作り直すなら --force を付ける")
    if path.exists():
        shutil.copy2(path, kdir / "reactions.bak.json")  # 長い処理の途中で落ちても戻せるように
    for name in PREP_OUTPUTS:
        (kdir / name).unlink(missing_ok=True)


def cmd_prep(args: argparse.Namespace) -> None:
    from .preprocess import ast_scores, audio, speech  # Whisper と AST は prep でだけ読み込む

    source = Path(args.audio)
    if not source.is_file():
        raise SystemExit(f"音声が無い: {source.name}")
    _check_excerpt(args, audio.duration(source))
    neta = store.Neta(args.performer, args.neta)
    koza = _koza_meta(args)
    kdir = neta.koza_dir(args.koza)
    _clear_outputs(neta, kdir, args.force)
    wav = audio.extract_wav(source, kdir / "audio.wav", start=args.start, end=args.end)
    terms = [t for t in args.terms.split(",") if t] if args.terms else None
    t0 = time.time()
    transcript = _to_transcript(speech.detect(wav, terms=terms), args.koza)
    t1 = time.time()
    store.write(kdir / "transcript.json", transcript, "transcript")
    doc = reactions.build(ast_scores.score(speech.load_wav(wav)), transcript)
    t2 = time.time()
    store.write(kdir / "reactions.json", doc, "reactions")
    measured = {
        "audio_seconds": audio.duration(wav),
        "transcribe_seconds": round(t1 - t0, 1),
        "reactions_seconds": round(t2 - t1, 1),
    }
    run_path = kdir / "run.json"
    run = {
        "schema_version": SCHEMA_VERSION,
        "koza_id": args.koza,
        "roles": [],
        "total_cost_usd": 0.0,
        "total_seconds": 0.0,
    }
    if run_path.exists():  # 役の費用の実測は消さない
        run = store.read(run_path, "run")
    run["prep"] = measured
    store.write(run_path, run, "run")
    store.write(kdir / "koza.json", koza, "koza")  # 最後に書く。これがあれば prep は完了している
    print(f"区間 {len(transcript['segments'])}、VAD {len(transcript['vad'])}、手がかり {len(doc['hints'])}")
    print(f"音声 {measured['audio_seconds']:.0f} 秒、文字起こし {measured['transcribe_seconds']:.0f} 秒")
    _print_top(doc)


def _within_audio(doc: dict, marks: list[dict]) -> None:
    """時刻がこの wav の中にあること。抜粋なら、元の録音の時刻で付けるとずれる。"""
    length = reactions.covered_seconds(doc)
    if length is None:
        return
    over = [m for m in marks if m["t1"] > length + doc["scores"]["stride"]]
    if over:
        raise ValueError(f"wav の長さ {length:.0f} 秒を超える時刻が {len(over)} 件ある")


def cmd_mark(args: argparse.Namespace) -> None:
    kdir = store.find_koza(args.koza, performer=args.performer, neta=args.neta)
    koza_doc = store.read(kdir / "koza.json", "koza")  # prep が最後まで済んだ高座だけ
    path = kdir / "reactions.json"
    doc = store.read(path, "reactions")
    marks = reactions.manual_reactions(reactions.parse_marks(Path(args.tsv)))
    _within_audio(doc, marks)
    backup = kdir / "reactions.auto.json"
    if not backup.exists():  # 自動の結果は別に残す（prep をやり直さなくて済むように）
        shutil.copy2(path, backup)
    if any(r["method"] == "manual" for r in doc["reactions"]):
        shutil.copy2(path, kdir / "reactions.manual.bak.json")  # 前の手の反応も残す
    doc["reactions"] = marks
    store.write(path, doc, "reactions")
    for name in STALE_AFTER_MARK:  # 反応から作った物は古くなる
        (kdir / name).unlink(missing_ok=True)
    print(f"手で付けた反応 {len(marks)} 件を入れた（自動の結果は {backup.name} に残した）")
    excerpt = koza_doc.get("excerpt")
    if excerpt:
        print(
            f"この高座は抜粋で、時刻は抜粋の先頭（元の録音の {excerpt['from'] or 0} 秒）からの秒として入れた"
        )


def cmd_top(args: argparse.Namespace) -> None:
    if args.n < 1:
        raise SystemExit("-n は 1 以上で")
    kdir = store.find_koza(args.koza, performer=args.performer, neta=args.neta)
    _print_top(store.read(kdir / "reactions.json", "reactions"), args.n)


def cmd_build(args: argparse.Namespace) -> None:
    from . import pipeline, runner

    stats = pipeline.build(
        args.performer, args.neta, args.koza, client=runner.make_client(), reuse=args.reuse
    )
    print(f"項目 {stats['evidence']['kept']}、落とした数 {sum(stats['evidence']['dropped'].values())}")
    print(f"照合 {stats['checker']}")
    print(f"弟子役の指摘 {stats['deshi']}、質問 {stats['questions']}")


def cmd_note(args: argparse.Namespace) -> None:
    from . import pipeline, runner

    doc = pipeline.note(args.performer, args.neta, args.koza, client=runner.make_client(), reuse=args.reuse)
    print(f"候補 {doc['notes']['candidates']}、決まった文に置き換えた数 {doc['notes']['fallback']}")
    g = doc["gaps"]
    print(f"的中 {g['hit']}、空振り {g['miss']}、予測なしで反応あり {g['surprise']}、質問 {doc['questions']}")
    print(f"笑い {g['laughs_total']} のうち窓に入った {g['laughs_hit']}、窓の割合 {g['window_share']:.0%}")


def _rewrite_markdown(neta: store.Neta) -> None:
    from . import pipeline

    pipeline.write_markdown(neta, store.read(neta.hanashi, "book"), store.read(neta.kata, "book"))


def cmd_run(args: argparse.Namespace) -> None:
    """prep、build、note を順に回す。"""
    import anthropic

    from . import runner

    if not args.reuse:  # --reuse はキャッシュだけで済むことがあるので、鍵を要求しない
        try:
            runner.check_auth(runner.make_client())  # 長い文字起こしの後で落ちないように、先に確かめる
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise SystemExit("API の鍵が通らない。ANTHROPIC_API_KEY を確かめる") from e
        except TypeError as e:
            if "authentication" not in str(e).lower():  # 別のバグを認証の問題と言わない
                raise
            raise SystemExit("API の認証が無い。ANTHROPIC_API_KEY を用意する") from e
    where = f"--performer {args.performer} --neta {args.neta} --koza {args.koza}"
    print("1/3 prep")
    cmd_prep(args)
    print(f"2/3 build（落ちたら netacho build {where}）")
    cmd_build(args)
    print(f"3/3 note（落ちたら netacho note {where}）")
    cmd_note(args)


def cmd_seisho(args: argparse.Namespace) -> None:
    from . import pipeline, runner

    client = runner.make_client()
    doc = pipeline.clean_copy(args.performer, args.neta, args.koza, client=client, reuse=args.reuse)
    s = doc["seisho"]
    print(f"清書 {s['lines']} 行、直した行 {s['changed']}、大きく直した行 {s['heavy']}")
    print(f"原文に戻した行 {s['kept_raw']}、抜け {s['missing']}、重複 {s['duplicate']}")
    if s["missing"] or s["duplicate"]:
        print("速記役が返さなかった区間がある。その行は原文のまま", file=sys.stderr)


def cmd_decide(args: argparse.Namespace) -> None:
    from . import performer

    neta = store.Neta(args.performer, args.neta)
    item = performer.decide(
        neta,
        speaker=args.speaker,
        words=args.words,
        card=args.card,
        why=args.why,
        scene=args.scene,
        position=args.position,
        candidate=args.candidate,
        koza=args.koza,
    )
    _rewrite_markdown(neta)
    print(f"型に入れた：{item['id']}。札に写すには netacho guide")


def cmd_answer(args: argparse.Namespace) -> None:
    from . import performer

    neta = store.Neta(args.performer, args.neta)
    from . import guide

    item = performer.answer(neta, question=args.question, speaker=args.speaker, words=args.words)
    _rewrite_markdown(neta)
    guide.refresh(neta)
    print(f"型に入れた：{item['id']}")


def cmd_guide(args: argparse.Namespace) -> None:
    from . import guide

    doc = guide.write(store.Neta(args.performer, args.neta))
    print(f"札 {len(doc['cards'])} 枚（LLM の呼び出し 0 回）")
    for card in doc["cards"]:
        print(f"  {card['order']}. {card['copied_fields']['scene_title']}：{card['text']}")


def cmd_reread(args: argparse.Namespace) -> None:
    from . import pipeline, runner

    client = runner.make_client()
    current = pipeline.reread(args.performer, args.neta, args.koza, client=client, reuse=args.reuse)
    print(f"弟子役の指摘 {len(current)} 件")


def _who(parser: argparse.ArgumentParser) -> None:
    """演者の前で打つコマンドは、演者と演目を環境変数でも渡せる。"""
    for flag, env in (("--performer", "NETACHO_PERFORMER"), ("--neta", "NETACHO_NETA")):
        default = os.environ.get(env)
        parser.add_argument(flag, default=default, required=default is None)


def cmd_view(args: argparse.Namespace) -> None:
    from . import view

    out = Path(args.out) if args.out else view.OUT
    try:
        path = view.write(store.Neta(args.performer, args.neta), args.koza, out)
    except FileNotFoundError as e:
        if not e.filename or Path(e.filename).suffix != ".json" or "schemas" in Path(e.filename).parts:
            raise  # 手順の問題ではない
        raise SystemExit(f"{Path(e.filename).name} が無い。先に netacho prep と build を回す") from e
    print(f"書き出した：{path.name}。make viewer で開く")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="netacho", description="一席の音声から演者の根多帳を作る")
    sub = p.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prep", help="wav、文字起こし、反応の候補")
    prep.add_argument("--audio", required=True)
    prep.add_argument("--performer", required=True)
    prep.add_argument("--neta", required=True)
    prep.add_argument("--koza", required=True)
    prep.add_argument("--from", dest="start", type=float)
    prep.add_argument("--to", dest="end", type=float)
    prep.add_argument("--terms", help="演目の語（カンマ区切り）")
    prep.add_argument("--force", action="store_true", help="手で付けた反応があっても作り直す")
    prep.set_defaults(func=cmd_prep)

    mark = sub.add_parser("mark", help="手で付けた反応の時刻を入れる")
    mark.add_argument("--koza", required=True)
    mark.add_argument("--tsv", required=True)
    mark.add_argument("--performer")
    mark.add_argument("--neta")
    mark.set_defaults(func=cmd_mark)

    top = sub.add_parser("top", help="強い順の反応の時刻（耳で確かめる用）")
    top.add_argument("--koza", required=True)
    top.add_argument("-n", type=int, default=10)
    top.add_argument("--performer")
    top.add_argument("--neta")
    top.set_defaults(func=cmd_top)

    build = sub.add_parser("build", help="噺・型（構成作家役、根拠の検証、照合役、マージ）")
    build.add_argument("--performer", required=True)
    build.add_argument("--neta", required=True)
    build.add_argument("--koza", required=True)
    build.add_argument("--reuse", action="store_true", help="同じ入力の役の出力を使い回す")
    build.set_defaults(func=cmd_build)

    note = sub.add_parser("note", help="「薄い」候補とノート")
    note.add_argument("--performer", required=True)
    note.add_argument("--neta", required=True)
    note.add_argument("--koza", required=True)
    note.add_argument("--reuse", action="store_true", help="同じ入力の役の出力を使い回す")
    note.set_defaults(func=cmd_note)

    run = sub.add_parser("run", help="prep + build + note")
    run.add_argument("--audio", required=True)
    run.add_argument("--performer", required=True)
    run.add_argument("--neta", required=True)
    run.add_argument("--koza", required=True)
    run.add_argument("--from", dest="start", type=float)
    run.add_argument("--to", dest="end", type=float)
    run.add_argument("--terms", help="演目の語（カンマ区切り）")
    run.add_argument("--force", action="store_true", help="手で付けた反応があっても作り直す")
    run.add_argument(
        "--reuse", action="store_true", help="同じ入力の役の出力を使い回す。外れると prep の後で鍵が要る"
    )
    run.set_defaults(func=cmd_run)

    clean = sub.add_parser("seisho", help="清書（速記役。原文も残す）")
    clean.add_argument("--performer", required=True)
    clean.add_argument("--neta", required=True)
    clean.add_argument("--koza", required=True)
    clean.add_argument("--reuse", action="store_true")
    clean.set_defaults(func=cmd_seisho)

    decide = sub.add_parser("decide", help="演者の決定を型に入れる（LLM 0 回）")
    _who(decide)
    decide.add_argument("--speaker", required=True)
    decide.add_argument("--words", required=True, help="演者の言葉のまま")
    decide.add_argument("--card", required=True, help="札の文。演者の言葉のまま")
    decide.add_argument("--why", required=True, help="理由。演者の言葉のまま")
    decide.add_argument("--scene", required=True)
    decide.add_argument("--position", choices=["before", "within", "after"], default="within")
    decide.add_argument("--candidate")
    decide.add_argument("--koza", help="--candidate の高座")
    decide.set_defaults(func=cmd_decide)

    answer = sub.add_parser("answer", help="質問への演者の答えを入れる（LLM 0 回）")
    _who(answer)
    answer.add_argument("--question", required=True)
    answer.add_argument("--speaker", required=True)
    answer.add_argument("--words", required=True, help="演者の言葉のまま")
    answer.set_defaults(func=cmd_answer)

    guide = sub.add_parser("guide", help="案内の札（LLM 0 回）")
    _who(guide)
    guide.set_defaults(func=cmd_guide)

    show = sub.add_parser("view", help="視聴画面の JSON を書き出す（LLM 0 回）")
    show.add_argument("--performer", required=True)
    show.add_argument("--neta", required=True)
    show.add_argument("--koza", required=True)
    show.add_argument("--out", help="書き出し先（既定は out/view）")
    show.set_defaults(func=cmd_view)

    reread = sub.add_parser("reread", help="弟子役に読み直させる")
    _who(reread)
    reread.add_argument("--koza", required=True)
    reread.add_argument("--reuse", action="store_true")
    reread.set_defaults(func=cmd_reread)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (store.StoreError, ValueError) as e:
        sys.exit(f"netacho: {e}")
    except FileNotFoundError as e:  # ffmpeg が無いときもここ。パスは出さない
        sys.exit(f"netacho: 見つからない: {Path(str(e.filename)).name}")
    except subprocess.CalledProcessError as e:
        sys.exit(f"netacho: {Path(str(e.cmd[0])).name} が失敗した（終了コード {e.returncode}）")
