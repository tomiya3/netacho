# 根多帳（Netacho）

一席の音声から、演者の根多帳を作る。

開発の土台は、このハッカソンで先に始めた別プロダクト Soegaki（非公開）から流用しています。

## 出典

反応の検出に次のモデルとデータセットを使っています。

- AST：Yuan Gong, Yu-An Chung, James Glass. "AST: Audio Spectrogram Transformer". ICASSP 2021. https://arxiv.org/abs/2104.01778
- モデル：`MIT/ast-finetuned-audioset-10-10-0.4593`。https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593
- AudioSet：Jort F. Gemmeke ほか. "Audio Set: An ontology and human-labeled dataset for audio events". ICASSP 2017. https://research.google.com/audioset/
