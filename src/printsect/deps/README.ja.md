# deps — C のビルドで、各出力が何から作られるか

[English](README.md) | 日本語

`psect-deps` はビルドを読み、どのソースとヘッダがどのオブジェクト、アーカイブ、出力に入るか、つまり
ファイルを変えたら何を作り直す必要があるかに答えます。単独で動きます。printsect の中では `psect deps` で、
プローブの場所も示します。

```
build/out/netdrv.out
├── build/obj/netdrv_part.o
│   ├── build/lib/libnet.a
│   │   ├── rx.o ← net/rx.c
│   │   │   └── util/ring.h
│   │   └── tx.o ← net/tx.c
│   └── build/lib/libutil.a
│       └── ring.o ← util/ring.c
└── netdrv_ctors.o ← gen/ctors.c
```

## コマンド

| コマンド | 内容 |
|---|---|
| `psect-deps` | ビルドを読み、`.printsect/deps.json` と `DEPS.md` に書く |
| `psect-deps tree [NAME…]` | 上のツリー。`-s` でソース名、`--headers` で全ヘッダ、`--probed` でプローブのある枝だけ（`psect deps` で） |
| `psect-deps tree -f PATTERN` | 一致するファイルまでの道筋だけを残す：文字列、`*.h`、または `--regex` |
| `psect-deps which FILE` | ファイルがどこに入るか（最初の 20 通り。`--all` でそれ以上） |
| `psect-deps impact FILE` | 変更すると作り直されるもの：生成ファイル、オブジェクト、アーカイブ、出力と、その配置先。`--depth N` で N 段まで |
| `psect-deps find PATTERN` | パスが一致するファイル。`--quickfix` でエディタ用の `file:1: chain` |
| `psect-deps html` | ブラウザで見て検索できる、1 つで完結したページ |

どのコマンドも `--json` と `--root DIR` を受け付けます。ビルドを読むのは `psect-deps` 自身だけです。
`tree`、`which`、`find`、`html` は保存した内容から答え、make を実行しません。`make -n` でも Makefile の
`$(shell …)` は実行されるからです。その後 Makefile や設定が変わっていれば知らせるので、`psect-deps` を
もう一度実行します。

試すには：`psect-deps --root examples/demo` のあと、`psect-deps tree --root examples/demo`。

## ビルドの読み方

1. **make に、実行するはずのものを表示させる。** `make -n -B -w` は、全部をビルドするときのコマンドを
   どれも実行せずに一覧します：変数、`include`、パターンルール、サブ make は展開済みです。
   （`$(shell …)` と `$(MAKE)` の行は実行されます。サブ make にも `-n` が付きます。）
2. **コマンドはツール名ではなく引数の形で見分ける**ので、どのクロスツールチェーンでも動きます：
   `-c` とソースがあればコンパイル。アーカイブのあとにオブジェクトが並び `-o` がなければアーカイブの作成。
   `-o` と、入力にオブジェクトかアーカイブがあればリンク（`ld -r` の部分リンクも）。`-l` は `-L` と、
   ビルドが作るアーカイブから探します。フォルダは `Entering directory` の行と、レシピの中の `cd` から追います。
3. **ヘッダは `#include` をたどって求める。** 各コンパイルの `-I`、`-iquote`、`-include` を使います。
   Makefile にヘッダが書かれていることはまれだからです。プロジェクトの中のヘッダだけを数えます。
   `#if` は評価しないので、答えは少し広めに出ることはあっても、狭くなることはありません。

大きなプロジェクトでも動きます：約 100 万行（ソース 8,000、ヘッダ 3,000）を約 6 秒で読み、各コマンドは
0.5〜2 秒で答えます。これは OS がファイルを一度読んだあとの時間です。作ったばかりのファイルを初めて読む
ときは、Windows ではウイルス対策の検査が 1 つずつ入るため、数十秒かかることがあります。`benchmarks/deps/`
を参照してください。

## 設定（`printsect.yaml`、すべて省略可）

```yaml
deps:
  command: make -n -B -w   # ビルドを表示するもの。例: make -f build.mk -n -B -w all
  cwd: .                   # 実行する場所
  timeout: 300
  headers: true            # false: Makefile の情報だけ（.c -> .o -> .a -> out）、速い
  # source_extensions / object_extensions / archive_extensions、ignore_tools、
  # watch_names / watch_suffixes：まず要りません
```

この PC で make を動かせない（メーカーの道具のシェルの中だけ）ときは、そこで出力を保存してファイルを
読ませます：`make -n -B -w > make.log` のあと、`psect-deps --from-log make.log`。

## 書くファイル

| ファイル | 用途 |
|---|---|
| `.printsect/deps.json` | グラフ：`outs`、`archives`、`objects`（それぞれ `source`、`headers` 付き） |
| `.printsect/DEPS.md` | ツリーと、ヘッダごとの出力。エディタで読む用 |
| `.printsect/deps.html` | `psect-deps html` のページ。プロジェクトからの相対パスだけを持ち、何も取りに行かず、人に渡しても安全 |

## ほかのビルド方式

make では表せないビルド（ターゲットの一覧、コンパイラを呼ぶスクリプト、独自のビルド方式）では、どの言語でもよいので小さな変換
プログラムで「何から何が作られるか」を書き出し、そのファイルを読ませます：`psect-deps --from-graph build.json`（`-` は標準入力）。
何も実行せず、そのビルドの事情を printsect に入れる必要もありません。

```json
{
  "version": 1,
  "objects": {"build/rx.o": {"source": "net/rx.c", "headers": ["net/net.h"]}},
  "archives": {"build/libnet.a": {"objects": ["build/rx.o"]}},
  "outs": {"build/app.out": {"archives": ["build/libnet.a"], "libs": ["m"]}},
  "inputs": ["targets.csv"]
}
```

ファイルには、部品の確実性（オブジェクト、アーカイブ、出力の `"status"`：`confirmed`、`build`、`script`、`inferred`、`unresolved`、
`excluded`）、ファイルを生成するもの（`"made": {"gen/a.c": {"from": ["a.def"], "by": "tools/gen.py"}}`）、出力の配置先
（`"copies": {"target:/lib/app.out": "build/app.out"}`）、外したものとその理由（`"excluded": {"vendor/x.c": "not ours"}`）も書けます。
`impact`、ページ、各コマンドの答えがこれを使います。

パスはすべてプロジェクトからの相対パスで、プロジェクトの中を指します。名前を挙げた部品はすべて一覧に載せます（ビルド済みの
アーカイブは `{"objects": [], "prebuilt": true}`）。`headers` は省略でき、省略するとヘッダがどの出力に入るかは分かりません。
`inputs` は変換プログラムが読んだファイルです。これが変わるとコマンドが知らせ、`psect build` はファイルを書き直すまで止まります。
それ以外の内容があれば、その名前を示すエラーで止まります。

読み取りは役割 `deps` のプラグインです。エントリポイントのグループ `printsect.deps` で追加でき、
`__init__(settings)` と `scan(project, *, log=None) -> Graph` が必要です。`deps: {type: <name>}` で選びます。
クラス属性 `SETTINGS`（キー -> 型、`make.py` と同じ形）があれば、printsect がそのキーを確かめます。
なければ読み取り側が自分で確かめます。

## 中身

| ファイル | 内容 |
|---|---|
| `cli.py` | コマンド。`configure()` で `psect` に組み込み、プローブの印を足せる |
| `graph.py` | `Graph` と、その問い合わせ（`which`、`roots_for_file` など）。索引は一度だけ作る |
| `make.py` | 標準の読み取り：`deps.command` を実行し、`makelog` と `includes` に渡す |
| `makelog.py` | `make -n` の出力をグラフにする |
| `imported.py` | `--from-graph` のグラフファイルを読んで確かめる |
| `includes.py` | `#include` をたどる |
| `paths.py` | パスをプロジェクトからの相対パスに速く直す |
| `search.py` | `Matcher`：文字列、グロブ、正規表現 |
| `tree.py` | ツリーと `which` を文字で表す |
| `markdown.py` | `DEPS.md` |
| `html.py`, `viewer.html` | ブラウザのページとそのテンプレート |
