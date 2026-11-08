# core — printsect の全ツールが共有するもの

[English](README.md) | 日本語

ツールではなく、ツールが立つ土台です。

## 決まり

- ツール（`deps`、`probe`、`deploy` など）は、`core` と自分自身からだけ import し、ほかのツールからは
  import しません。それで各ツールが単独で動けます。
- `core` はどのツールも import しません。
- ツール同士をつなぐこと（deps のツリーのプローブの印、2 つのツールにまたがる警告、`psect run` のループ）は、
  `psect` コマンドである `printsect/cli.py` と、`printsect/suite/` だけで行います。`suite` はすべての
  ツールを使ってよく、どのツールからも使われません。ツールは、そのための入り口を用意します：
  `deps.cli.configure` の `marks=`、`probe.cli.configure` の `after_add=`、`deploy.cli.configure` の `wait_for=`。

`tests/test_layout.py` がこれらをすべて確かめます。ツールを 1 つ起動したとき、ほかのツールが読み込まれない
ことも含みます。

## 設定を確かめる

ツールは、自分が読む `printsect.yaml` の部分を、`__init__.py` の `SECTIONS`（キー -> 型、または入れ子の
対応）として宣言し、`parser.set_defaults(sections=SECTIONS)` で渡します。すると `core.cli.project()` が、
それらの部分と printsect 自身の部分（`CORE_SCHEMA`）にある、知らないキーや型の違うキーで止まり、近い
正しい名前を示します。ほかのツールの部分には触れないので、全ツール向けに設定したプロジェクトでも各ツールが
動きます。キーがプラグインによって決まる部分（`deps`、`deploy`）は `type` だけを宣言し、残りはプラグインの
`SETTINGS` が受け持ちます。`psect check` はすべてをまとめて確かめます。

## ツールの足し方

1. `printsect/<tool>/` フォルダに、`configure(...)`、`build_parser()`、`main()` を持つ `cli.py`、
   `__main__.py`、`__init__.py` の `SECTIONS`、そしてこの形の `README.md`（と `README.ja.md`）を置く。
2. `pyproject.toml` にコマンド `psect-<tool>` を足し、`printsect/cli.py` の `TOOLS`、`cmd_check`、
   `build_parser()` に 1 行ずつ足す。
3. テストを `tests/<tool>/` に置く。

## 中身

| ファイル | 内容 |
|---|---|
| `cli.py` | `--json` / `--root`、JSON の応答、終了コード（0 ok、1 失敗、2 設定、3 人の作業待ち） |
| `config.py` | プロジェクトのルートを探す。`printsect.yaml` と `printsect.local.yaml`。既定値。設定の確認 |
| `init_config.yaml`, `init_local.yaml` | `psect init` が書くもの：全部分をコメントにし、架空の例を付けたもの（`.ja.yaml` が日本語版） |
| `plugins.py` | 役割と、その実装：標準のもの、またはエントリポイント `printsect.<role>` から |
| `errors.py` | `PsectError`、`ConfigError` |
| `fsutil.py` | 途中で壊れない書き込み。git から自動で除外される `.printsect/` フォルダ |
| `term.py` | 色を付けるか、画面上の文字の幅 |
| `timeutil.py` | 時刻の書式 |
