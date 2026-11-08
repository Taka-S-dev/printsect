# printsect

[English](README.md) | 日本語

C のバグを、プリントのプローブで絞り込むためのツールです。AI エージェントにも、人の手でも使えます。

printsect は小さなツールの集まりです。一度入れれば、各ツールを単独でも、`psect` からまとめても使えます。

| ツール | 単独で | printsect の中で | 役割 | 説明 |
|---|---|---|---|---|
| deps | `psect-deps` | `psect deps` | C のビルドで、各出力が何から作られるか：ソースとヘッダ → オブジェクト → アーカイブ → 出力。Makefile を `make -n` で読むので、何もビルドしません。ツリー、検索、「このファイルはどこに入るか」、ブラウザ用の画面。 | [deps](src/printsect/deps/README.ja.md) |
| probe | `psect-probe` | `psect probe` | C のソースにプリントのプローブを入れ、どこに・なぜ入れたかの一覧を持ち、自分が入れたものだけを取り除く。 | [probe](src/printsect/probe/README.ja.md) |
| console | `psect-console` | `psect console` | 対象機器の出力を行ごとに読み、コマンドを送り、特定の行を待つ。シリアルポートを自分で開くか、すでにポートを開いているプログラムを通して（TCP、そのコマンドライン、そのログファイル）。 | [console](src/printsect/console/README.ja.md) |
| map | `psect-map` | `psect map` | C のプロジェクトで、どのモジュールがどれを使っているか。ビルドもほかのプログラムも使わずソースから読み、エージェントが最初に読める短さで出す。 | [map](src/printsect/map/README.ja.md) |
| deploy | `psect-deploy` | `psect deploy` | ビルドを設定どおりに対象機器へ載せる（コマンド、FTP、または人の作業）。 | [deploy](src/printsect/deploy/README.ja.md) |

ツール同士をどう分けているか、ツールの足し方：[core](src/printsect/core/README.ja.md)。

`psect` はこれらをつなぎます。deps のツリーにプローブの場所を示し、`psect deploy` は新しいビルドが
起動したことをコンソールで確かめる、などです。各ツールは `python -m printsect.<tool>` でも動きます。

```sh
uv tool install printsect      # または: pipx install printsect

psect-deps tree                # このフォルダのプロジェクトのビルド
psect-deps which src/inc/net.h # このヘッダを変えると何が作り直されるか
psect-deps html                # ブラウザで見て検索する
```

設定はプロジェクトのルートの `printsect.yaml` に書きます。なくても各ツールは動きます。各ツールは、
自分の担当する部分に書き間違いや型の違いがあると、近い正しい名前を示して止まります。`psect check` は
全部をまとめて確かめます。状態は `.printsect/` に置き、git からは自動で除外されます。

## プロジェクトの設定

プロジェクトの情報を PC の外に出す必要はありません。設定はあなたが書き、printsect がその場で確かめます。

1. `psect init` で `printsect.yaml`（共有）と `printsect.local.yaml`（この PC：コンソール）を作ります。
   どちらにも、埋める順番にコメント付きの例が入っています。`--lang ja` で日本語のひな形になります。
2. 項目を埋めるたびに `psect doctor` を実行します。各部分を実際に試し（`make -n` でビルドを読む、
   コンソールにつなぐ、起動の目印の行を確かめる、ビルドと転送のコマンドを探す）、それぞれ ok、
   warn、ng と、次にやることを表示します。何もビルドせず、対象機器にファイルもコマンドも送らず、ファイルも
   変えません。ftp で転送する設定のときは、設定が承認済みなら、アドレス、ユーザー、パスワードを試すために
   対象機器にログインします。
3. すべて ok になったら、プローブを入れて `psect run` を実行します。

```
ok   deps       read the build: 4 output(s), 12 archive(s), 830 source(s), 410 header(s)
ok   console    tcp: reached, last line 1534
warn banner     app/init.c:41 is in app_init(); line 41 does not end a statement
                -> the banner line goes below LINE: choose the end of a statement ...
ng   build      no build.command
                -> the command you build with; {outs} becomes the outputs to rebuild ...
```

## ループ：psect run

デバッグの 1 周は、コマンド 1 つです。`psect run` は、プローブがソースどおりか確かめ、起動の目印
（`[PSECT-RUN n]` を出力する行。今回のビルドの出力を古いビルドの出力と区別するため）を入れ、ビルドし、
各出力を転送してコンソールに目印が出るまで待ち、問題を再現する手順を実行し、止める条件までコンソールを
読み、各プローブが出力したものを数えます：

```
run 3: ok at done (quiet 5s)
212 line(s) read; outputs: build/out/netdrv.out
#7    3 hits (lines 140-188)  net/rx.c:41  len 0 で来ているか
       len = 0
#8    not reached  net/rx.c:55  エラー処理を通るか
```

数えた結果は各プローブの記録に入り、すべてが `.printsect/runs/<n>/`（ビルドのログ、コンソールの行、
まとめ）に残ります。`psect hits [N]` で実行結果をもう一度見られます。`psect build --plan` は、今入って
いるプローブのためのビルドのコマンドを表示し、`psect build` はビルドだけを実行します。人の作業が必要な
手順（`ask:`）があり、端末の前に誰もいないときは、依頼の内容を示して終了コード 3 で止まります。
`psect run --resume` でその続きから再開します。

```yaml
build:
  steps:                       # グラフから埋められ、書いた順に実行される
    - run: make clean          # `for` なし：ここで一度だけ
    - for: archives            # プローブを入れたファイルを含むアーカイブごと（ビルド順）
      run: make -C {made_in}   # ビルドがそれを作ったフォルダ
    - for: outs                # それを含む出力ごと
      run: make {path}         # ビルドが付けた名前のままの出力
  artifacts: auto              # それらの出力を転送する。または一覧
run:
  banner: app/init.c:42        # 目印の行を入れる場所：新しいビルドが必ず実行するコード
  ready: "^> "                # そのあと、再現の前にこれ（プロンプト）を待つ
deploy:
  type: manual                 # または command、ftp、またはプラグイン
console:
  type: tcp                    # console ツールを参照
reproduce:
  - send: app_start
  - wait: "ready"
  - ask: ケーブルを抜いてください
stop:
  match: ["Exception", "assert"]
  quiet: 5                     # 出力がない秒数
  timeout: 120
```

待つことは毎回の手順に入っています。ビルドの各手順は終わるまで待ち、そのあと転送するすべての出力が
ビルド開始より新しいことを確かめます（作業を別の道具に渡してすぐ戻るビルドでも、古い出力を転送しません）。
転送は起動の目印を待ち、`run.ready` は対象機器がコマンドを受け付けるようになるまで待ちます。

## 複数の機器

対象機器によって違いがある（出力の置き場所、読み込み方、コンソールのポート）ときは、`devices:` に
機器ごとの名前で、違う部分だけを書きます。`psect use board-b` でプロジェクトで使う機器を選びます
（1 回だけなら `--device` か `PSECT_DEVICE`）。すべてのコマンドがその機器の設定で動き、`{device}` が
その名前になります。複数あるのにどれも選んでいなければ、ビルド、転送、実行は推測せずに止まります。

```yaml
devices:
  board-a:
    build: {artifact_path: "out/a/{name}"}
  board-b:
    build: {artifact_path: "out/b/{name}"}
    deploy: {type: manual, message: "{artifact_name} を B 用のローダーで読み込んでください"}
```

## AI エージェントに動かしてもらう

`psect mcp` は、MCP（標準入出力）でツールをエージェントに提供します。エージェントは自分でループを
回せます：プローブを入れ、`run` し、ヒットを読み、絞り込み、後片付けをします。MCP の各道具は、
コマンドラインの同じコマンドを `--json` 付きで実行するので、エージェントにできるのは人にできることだけです。
エージェントには、接続したときに手順書が 2 つ渡されます。誰でも読めます：`psect guide`（進め方：仮説、
プローブごとの目的、人の作業が必要な手順は人に返す）と `psect guide setup`（`doctor` がすべて ok に
なるまでの設定のしかた：プロジェクトから何を読み、何を推測せず人に聞き、最初のビルドの前に何を見せるか）。
`psect guide settings` は全設定項目を例付きで示します。手順書は `lang: ja` か `PSECT_LANG=ja` で日本語に
なります。エージェントはデバッグだけでなく設定もでき、人は、エージェントには分からないことにだけ答えます。

```sh
# Claude Code で、プロジェクトのフォルダにて
claude mcp add printsect -- psect mcp --root .
```

ほかの MCP クライアントも、同じように `psect mcp --root <プロジェクト>` を起動します。

## プロジェクトのコマンドを実行すること

設定の中には、printsect が実行するコマンドがあります：`deps.command`（既定は `make -n -B -w`）と、
`command` 型の転送の `commands` です。`make -n` は何もビルドしませんが、make は Makefile の
`$(shell …)` と、再帰呼び出しの `$(MAKE)` の行は実行します。なので `printsect.yaml` は Makefile と同じ
ように扱ってください：自分が書いていないプロジェクトでは、`psect deps` や `psect deploy` の前に中身を
読みます。ツリーを表示するだけで、プロジェクトが設定したコマンドが動くことはありません。頼まずに動くのは
既定の `make -n -B -w` だけです。

エージェントは設定を書けるので、コマンドを書いてから、それを実行するよう頼むこともできてしまいます。
それはできないようにしてあります：設定が実行するもの（deps、build、deploy、console、reproduce の各部分を
書かれたとおりに、そして `make -n` でも `$(shell ...)` を実行する Makefile）は、人が `psect approve` で
承認するまで、エージェント（MCP）のためには実行されません。このコマンドはすべてを、制御文字を見える形にして
一覧し、端末で尋ねます。質問を飛ばすオプションはなく、エージェントには提供されません。中身が少しでも
変われば承認し直しです。それまでエージェントの道具は、待っているものを示して断り、`psect doctor` も
それを一覧します。

承認はプロジェクトの外、あなたの設定フォルダ（`%APPDATA%\printsect` か `~/.config/printsect`）に、
そこにある鍵で署名して保存します。プロジェクトと一緒に持ち込まれたり、プロジェクトのファイルを書き換えて
作られたりすることはありません。あなたとして PC のどこにでも書き込めるエージェントなら、意図して偽造する
ことはできます。エージェントのファイル操作はプロジェクトの中に限ってください。

make がどのファイルを読むかを推測する代わりに、承認はプロジェクトのすべてのファイルを対象にします。
例外は、C と C++ のソースとヘッダ（プローブを入れる場所）、生成された `.d` の依存ファイル、バイナリの
ビルド出力（`.o`、`.a`、`.out` など）です。Makefile、それがどう `include` するにせよ読み込むもの、
スクリプト、設定が含まれます。`.d` が対象外になるのは、普通の `target: prerequisites` の規則だけを
含むときに限ります。ソースやヘッダでも、make の `$(`／`${` の書き方を含むもの、または対象のファイルの
`include` の行に名前が出てくるもの（さらにその先も）は対象になります。Makefile から変数やワイルドカード
だけでたどり着き、`$` を含まないソースやヘッダは、make を実行しない限り見分けられません。これが残る
限界です。リンクしたフォルダもたどります。対象のファイルを変える、足す、消すと、承認し直しです。ビルドが
書くほかのファイルは `deps.approval_ignore`（パターン）で外せますが、その設定自体も承認の対象です。
対象外なのは、バイナリのビルド出力と、ツールチェーンの Makefile などプロジェクトの外のファイルです
（`deps.command` の中のそうしたパスは、承認するときに一覧に出ます）。

承認は安全装置であって、隔離（サンドボックス）ではありません。ビルドそのもの（Makefile や、そこから
呼ばれるスクリプト）を編集できるエージェントは、次のビルドで、承認したビルドが実行する内容を、どんな一覧にも
現れない形で変えられます。なので、エージェントに編集させるのはソースと printsect の設定だけにしてください。
Claude Code なら、権限の設定で `Makefile`、`*.mk`、ビルド用のスクリプトの編集を禁止します。

`examples/demo` の架空のプロジェクトで試せます：

```sh
psect-deps tree --root examples/demo
```
