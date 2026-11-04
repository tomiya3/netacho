#!/usr/bin/env bash
# ローカル・CI・クラウド環境で共通のセットアップ
set -euo pipefail
if ! command -v ffmpeg >/dev/null; then
  if command -v apt-get >/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
  else
    echo "ffmpeg を入れてください（brew install ffmpeg）" >&2; exit 1
  fi
fi
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --locked
# main への直 push を止めるフックを有効化（git repo のときだけ）
git rev-parse --git-dir >/dev/null 2>&1 && git config core.hooksPath .githooks || true
