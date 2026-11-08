# console — 対象機器の出力を読み、コマンドを送る

[English](README.md) | 日本語

`psect-console` は、対象機器のコンソールを行ごとに読み、コマンドを送り、特定の行が出るのを待ちます。
各行には番号が付きます。最後に読んだ番号を覚えておき、それより後を頼めば、同じ行を二度読まず、
読み損ねた行にも気づけます。単独で動きます。printsect の中では `psect console` で、`psect deploy` は
これを使って、動いているのが新しいビルドかを確かめます。

## コマンド

| コマンド | 内容 |
|---|---|
| `psect-console mark` | ここまでに出た最後の行の番号 |
| `psect-console read [--since N] [--for SEC]` | N 行目より後の行。`--for` で新しい行を読み続ける |
| `psect-console send TEXT [--timeout SEC]` | コマンドを送り、対象機器の応答を表示する |
| `psect-console wait PATTERN [--since N] [--timeout SEC]` | 一致する行が出るまで待つ（時間内に出なければ終了コード 1） |

どのコマンドも `--json` と `--root DIR` を受け付けます。読む前に流れて消えた行があれば、`read` がその数を
知らせます。

## コンソールへの接続

シリアルポートを開けるのは同時に 1 つのプログラムだけです。端末のプログラムがすでにポートを開いて
いるなら、そのプログラムを通して読みます。ここでポートを開くのは、ほかに開いているものがないときだけです。
`console: {type: …}` で選びます。

| 種類 | 使うとき | 設定 |
|---|---|---|
| `serial` | ほかにポートを開いているものがない。`pip install 'printsect[serial]'` が必要 | `port`（`COM3`、`/dev/ttyUSB0`）、`baudrate`（115200）、`encoding`、`eol`（`"\r"`）、`prompt`（正規表現。なければ `quiet` 秒の無出力で応答の終わり、0.5）、`timeout` |
| `tcp` | ポートを開いているプログラムが、この PC の TCP で、1 リクエストに 1 行の JSON で答える | `port`、`host`（この PC のみ）、`read_request`、`send_request`、`timeout`、下のキー |
| `command` | そのプログラムに、JSON を出力するコマンドラインがある | `read`、`send`（シェルで実行する文字列、または単語の一覧）、`timeout`、下のキー |
| `file` | そのプログラムがログファイルを書く（たいていの端末プログラムはできる） | `path`、`encoding`。読むだけ |

`tcp` と `command` では、読み取りの応答は番号付きの最後の何行か `{"lines": [{"no": 812, "text": "..."}]}`、
送信の応答は出力 `{"output": "..."}` を持ちます。名前が違うときは `lines_key`、`no_key`、`text_key`、
`output_key` で指定します。リクエストとコマンドの中では、`{since}` と `{text}` が埋められます。

```yaml
# printsect.local.yaml：この PC のプログラムの設定（コミットしない）
console:
  type: tcp
  port: 8765
  read_request: {screen: true, lines: 500}
  send_request: {cmd: "{text}"}
```

```yaml
console:
  type: command
  read: [mytool, screen, --json, --lines, "500"]
  send: [mytool, send, "{text}", --json]
```

読む頻度が問題になるのは `tcp` と `command` だけです。そのプログラムが持っている行数には限りがあるので、
読む間に対象機器が出力する行より多い行数を頼んでください。

## ほかの接続方法

コンソールは役割 `console` のプラグインで、エントリポイントのグループ `printsect.console` で追加します。
`__init__(settings)`、`missed`、`mark()`、`read(since)`、`send(text, timeout)`、`close()` が必要です。
クラス属性 `SETTINGS`（キー -> 型）があれば、printsect がそのキーを確かめます。

## 中身

| ファイル | 内容 |
|---|---|
| `cli.py` | コマンド |
| `base.py` | `Line`、`Console` の約束事、`wait_for`、JSON の応答を扱う補助 |
| `consoles.py` | `serial`、`tcp`、`command`、`file` |
