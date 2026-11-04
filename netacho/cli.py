"""netacho の入口。流れはスクリプトが決める。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import reactions, store

SCHEMA_VERSION = 1
STALE_AFTER_MARK = ("note_candidates.json", "note.md", "sokki.md")
# prep をやり直すと意味を失う物
PREP_OUTPUTS = (
    "koza.json",
    "transcript.json",
    "reactions.json",
    "reactions.auto.json",
    "timeline.json",
    "note_candidates.json",
    "note.md",
    "sokki.md",
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
        print(f"  {rank:>3}  {reactions.clock(r['t0'])} - {reactions.clock(r['t1'])}  {r['id']}")


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
    from .preprocess import audio, speech  # Whisper は prep でだけ読み込む

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
    doc = reactions.build(speech.load_wav(wav), transcript)
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


def _within_wave(doc: dict, marks: list[dict]) -> None:
    """時刻がこの wav の中にあること。抜粋なら、元の録音の時刻で付けるとずれる。"""
    wave = doc.get("wave")
    if wave is None:
        return
    length = wave["hop"] * len(wave["db"])
    over = [m for m in marks if m["t1"] > length + wave["hop"]]
    if over:
        raise ValueError(f"wav の長さ {length:.0f} 秒を超える時刻が {len(over)} 件ある")


def cmd_mark(args: argparse.Namespace) -> None:
    kdir = store.find_koza(args.koza, performer=args.performer, neta=args.neta)
    koza_doc = store.read(kdir / "koza.json", "koza")  # prep が最後まで済んだ高座だけ
    path = kdir / "reactions.json"
    doc = store.read(path, "reactions")
    marks = reactions.manual_reactions(reactions.parse_marks(Path(args.tsv)))
    _within_wave(doc, marks)
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
