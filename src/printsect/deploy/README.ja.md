# deploy — 新しいビルドを対象機器に載せる

[English](README.md) | 日本語

`psect-deploy` は、設定どおりにビルドを対象機器へ載せ、動いているのが新しいビルドであることを確かめます。
やり方は差し替えられます：コマンド、人の作業、または特定の対象機器向けのプラグイン。単独で動きます。
printsect の中では `psect deploy` です。

## コマンド

`psect-deploy [--run N]` — `N` はビルドが持つ実行の番号です。対象機器は起動時に `[PSECT-RUN N]` を出力し、
それで新しいビルドを、まだ動いている古いビルドと区別します。`psect deploy` は、`console:` に設定した
コンソールでそれを待ちます（console ツールを参照）。`psect-deploy` 単独では、代わりに人に尋ねます。

| 終了コード | 意味 |
|---|---|
| 0 | ビルドが対象機器に載った |
| 1 | 失敗した |
| 2 | 設定が間違っている |
| 3 | 先に人が何かをする必要がある（メッセージを参照） |

`--json` と `--root DIR` を受け付けます。

## 転送のしかた

`deploy: {type: …}` で選びます。

| 種類 | 内容 | 設定 |
|---|---|---|
| `none`（既定） | 何もしない：この PC で動かすプログラム | — |
| `manual` | 人が転送する（例：メーカーの道具で）。コンソールに起動の目印が出ると自動で進み、出なければ尋ねるか終了コード 3 | `message`、`until`（既定：起動の目印）、`timeout`（600） |
| `command` | この PC でコマンドを実行し、起動の目印を待つ | `commands`（一覧）、`timeout`（120）、`confirm`（パターン。`""` で確認しない）、`confirm_timeout`（60） |
| `ftp` | FTP サーバーが動く対象機器にファイルを置き、`after`（読み込みのコマンド）をコンソールに送り、起動の目印を待つ | `host`、`port`（21）、`user`（anonymous）、`password_env`（`PSECT_FTP_PASSWORD`。`PSECT_FTP_…` の名前）、`path`（`{artifact_name}`）、`passive`（true）、`tls`（false）、`tls_ca`、`tls_verify`（true）、`timeout`（60）、`after`（一覧）、`after_timeout`（30）、`confirm`、`confirm_timeout` |

メッセージとコマンドの中では、`{run}`、`{artifact}`、`{artifact_name}`、`{root}`、`{device}`、`{prefix}` が
埋められます。ビルドのファイルは `build: {artifact: …}` から取ります。

```yaml
deploy:
  type: manual
  message: "{artifact_name} をメーカーの道具で読み込んでください"
build:
  artifact: build/out/netdrv.out
```

ftp のパスワードは設定ファイルに書きません：`password_env` が指す環境変数を人が設定します。FTP は
パスワードを暗号化せずに送るので、閉じたネットワークで使うか、FTPS を話すサーバーなら `tls: true` に
します。サーバーの証明書は確かめます。自己署名の証明書は `tls_ca`（プロジェクトの中のファイル）に渡します（`tls_verify: false` で
確かめなくなり、承認の一覧にそう出ます）。`after` は `psect` がつないだコンソールに送ります。`psect-deploy` 単独では、ファイルを置いたあと、
人が打つコマンドを示して終了コード 3 で止まります。`psect doctor` は、設定が承認済みならログインだけを
試します（ファイルは送りません）。

```yaml
deploy:
  type: ftp
  host: 192.168.0.10
  user: target
  path: "/tmp/{artifact_name}"
  after:
    - "load /tmp/{artifact_name}"
```

## 自分の対象機器向けの転送

転送は役割 `deployer` のプラグインです：`settings` を受け取り、`deploy(ctx) -> DeployResult` を持つクラスです。
エントリポイントのグループ `printsect.deployer` で、別のパッケージとして配ります。標準のものと同じ名前なら、
それを置き換えます。クラス属性 `SETTINGS`（キー -> 型、`deployers.py` と同じ形）があれば、printsect がその
キーを確かめます。なければ転送側が自分で確かめます。`ctx` は、ルート、実行の番号、出力、プレフィックス、
人に尋ねる `ask(question)`、コンソールを見張る `wait_for(pattern, timeout)`、コンソールに打ち込む
`send(text, timeout)` を渡します（コンソールがなければどれも `None`）。

## 中身

| ファイル | 内容 |
|---|---|
| `cli.py` | コマンド |
| `base.py` | `DeployContext`、`DeployResult`、`Deployer` の約束事、状態 |
| `deployers.py` | `none`、`manual`、`command` |
| `ftp.py` | `ftp` |
