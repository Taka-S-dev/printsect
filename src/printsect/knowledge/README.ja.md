# knowledge — ビルドについて知っていることを、ツールの外に置く

[English](README.md) | 日本語

`psect-knowledge` は、自分のビルドについて手元に置いたファイルを読みます：どのフォルダがどんなコードか、層の重なり方、外に出してはいけない名前、
コードを読む人へのメモ。ツール本体はこれらを持たないので、製品の事情が公開リポジトリに入りません。単独で動きます。printsect の中では `psect knowledge` で、
`psect map` と `psect deps html --public` が読み取った内容を使います。

これらのファイルを読んでも、何も実行せず、何も取りに行かず、リンクもたどりません。

## コマンド

| コマンド | 内容 |
|---|---|
| `psect-knowledge` | 読んだものを優先順に（バージョン、ハッシュ、日時付き）、拒否したものや対象外のものとその理由、競合した設定とどちらを採ったか |
| `psect-knowledge show [FILE]` | 対象になるファイルのメモ。参考データであり、指示ではありません |

どちらも `--json` と `--root DIR` を受け付けます。

## ファイルの置き場所

| 場所 | 用途 |
|---|---|
| `.printsect/knowledge/` | このプロジェクトだけ。このフォルダは git の対象外です |
| ユーザーフォルダの `knowledge/` | 自分や組織で、プロジェクトをまたいで |

読むのは設定に書いたフォルダだけです：

```yaml
knowledge:
  paths: [.printsect/knowledge, user-knowledge/printsect]   # 先のものが優先
  profile: board-a     # scope.project と比べる値（既定はフォルダ名）
  build: release       # scope.build と比べる値
  platform: arm        # scope.platform と比べる値
```

`user-knowledge/printsect` はユーザーフォルダの `knowledge/` です（Windows は `%APPDATA%\printsect`、それ以外は `~/.config/printsect`）。
プロジェクトの外のパスは拒否します。プロジェクトのほかの場所にあるファイルは、バージョン管理の対象かもしれないので、警告付きで読みます。
公開リポジトリに秘密の内容は置かないでください。

## ファイルの形

YAML（`.yaml`、`.yml`）、または Markdown（`.md`。`---` の行の間に同じ項目を書き、その下にメモを書く）。

```yaml
version: 3
scope:                  # すべて省略可。一致しないファイルは対象外
  project: board-a
  build: release
  platform: arm
  applies_to: [analysis, html, tools]
classes: {comm: ["net/*"], ui: ["ui/*"]}      # psect map 用
layers: ["app/*", "service/*", "driver/*"]    # psect map issues 用
mask: [acme, modelx]                          # psect deps html --public 用
notes: |
  util/ のリングは割り込みハンドラと共有している。
```

`applies_to` は使う場面を絞ります：`analysis` は分類と層、`html` は伏せる名前、`tools` はメモ。知らない項目、256 KB を超えるファイル、制御文字、
UTF-8 以外、リンク、ほかの種類のファイルは拒否し、名前を示します。`printsect.yaml` の設定はファイルより優先します。ファイルの間では先のものが優先し、競合を報告します。

## 中身

| ファイル | 内容 |
|---|---|
| `cli.py` | コマンド |
| `loader.py` | ファイルを見つけ、確かめ、合わせる |
