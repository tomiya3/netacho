"""netacho の入口。流れはスクリプトが決める。"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

from . import reactions, store

SCHEMA_VERSION = 1


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
    print(f"反応の候補 {len(doc['reactions'])} 件。強い順に {n} 件（時刻はこの wav の先頭から）：")
    for r in reactions.top(doc, n):
        print(
            f"  {r['strength_rank']:>3}  {reactions.clock(r['t0'])} - {reactions.clock(r['t1'])}  {r['id']}"
        )


def cmd_prep(args: argparse.Namespace) -> None:
    from .preprocess import audio, speech  # Whisper は prep でだけ読み込む

    if not Path(args.audio).is_file():
        raise SystemExit(f"音声が無い: {args.audio}")
    neta = store.Neta(args.performer, args.neta)
    koza = _koza_meta(args)
    kdir = neta.koza_dir(args.koza)
    wav = audio.extract_wav(Path(args.audio), kdir / "audio.wav", start=args.start, end=args.end)
    store.write(kdir / "koza.json", koza, "koza")
    terms = [t for t in args.terms.split(",") if t] if args.terms else None
    transcript = _to_transcript(speech.detect(wav, terms=terms), args.koza)
    store.write(kdir / "transcript.json", transcript, "transcript")
    doc = reactions.build(speech.load_wav(wav), transcript)
    store.write(kdir / "reactions.json", doc, "reactions")
    print(f"区間 {len(transcript['segments'])}、VAD {len(transcript['vad'])}、手がかり {len(doc['hints'])}")
    _print_top(doc)


def cmd_mark(args: argparse.Namespace) -> None:
    kdir = store.find_koza(args.koza)
    path = kdir / "reactions.json"
    doc = store.read(path, "reactions")
    backup = kdir / "reactions.auto.json"
    if not backup.exists():  # 自動の結果は別に残す（prep をやり直さなくて済むように）
        shutil.copy2(path, backup)
    doc["reactions"] = reactions.manual_reactions(reactions.parse_marks(Path(args.tsv)))
    store.write(path, doc, "reactions")
    print(f"手で付けた反応 {len(doc['reactions'])} 件を入れた（自動の結果は {backup.name} に残した）")


def cmd_top(args: argparse.Namespace) -> None:
    _print_top(store.read(store.find_koza(args.koza) / "reactions.json", "reactions"), args.n)


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
    prep.set_defaults(func=cmd_prep)

    mark = sub.add_parser("mark", help="手で付けた反応の時刻を入れる")
    mark.add_argument("--koza", required=True)
    mark.add_argument("--tsv", required=True)
    mark.set_defaults(func=cmd_mark)

    top = sub.add_parser("top", help="強い順の反応の時刻（耳で確かめる用）")
    top.add_argument("--koza", required=True)
    top.add_argument("-n", type=int, default=10)
    top.set_defaults(func=cmd_top)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (store.StoreError, ValueError) as e:
        sys.exit(f"netacho: {e}")
