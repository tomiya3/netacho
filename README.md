# 根多帳（Netacho）

一席の音声から、演者の根多帳を作る。

開発の土台は、このハッカソンで先に始めた別プロダクト Soegaki（非公開）から流用しています。

## 使い方

repo にはダミー（自作の架空の噺）だけが入っています。

```
make setup
make test
NETACHO_DATA=tests/fixtures/dummy_koza uv run netacho view --performer performer-dummy --neta 雨宿り勘定 --koza 2026-01-01-dummy-hall
make viewer   # http://localhost:8000/（ポートが塞がっていれば make viewer PORT=8765）
```

`--reuse` は、同じ入力の役の出力を使い回します。正本が変わると入力も変わるので、途中で落ちたときのやり直しに使います。

## 出典

反応の検出に次のモデルとデータセットを使っています。

- AST：Yuan Gong, Yu-An Chung, James Glass. "AST: Audio Spectrogram Transformer". ICASSP 2021. https://arxiv.org/abs/2104.01778
- モデル：`MIT/ast-finetuned-audioset-10-10-0.4593`。https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593
- AudioSet：Jort F. Gemmeke ほか. "Audio Set: An ontology and human-labeled dataset for audio events". ICASSP 2017. https://research.google.com/audioset/
