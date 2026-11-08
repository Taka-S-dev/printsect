# console — read what the target prints, and send it commands

English | [日本語](README.ja.md)

`psect-console` reads the target's console line by line, sends it commands, and waits
for a line to appear. Every line gets a number; keep the last number you read and ask
for what came after, so nothing is read twice and lost lines are noticed. It runs on
its own; inside printsect it is `psect console`, and `psect deploy` uses it to see
that a new build is the one running.

## Commands

| Command | What it does |
|---|---|
| `psect-console mark` | The number of the last line printed so far |
| `psect-console read [--since N] [--for SEC]` | The lines after line N; `--for` keeps reading new ones |
| `psect-console send TEXT [--timeout SEC]` | Send a command, print what the target answered |
| `psect-console wait PATTERN [--since N] [--timeout SEC]` | Wait until a line matches (exit 1 when it does not in time) |

Every command takes `--json` and `--root DIR`. When lines scrolled away before they
were read, `read` says how many.

## Reaching the console

A serial port has one owner at a time. If a terminal program already holds it, reach
the console through that program; open the port here only when nothing else does.
Pick one with `console: {type: …}`.

| Type | Use when | Settings |
|---|---|---|
| `serial` | Nothing else holds the port. Needs `pip install 'printsect[serial]'` | `port` (`COM3`, `/dev/ttyUSB0`), `baudrate` (115200), `encoding`, `eol` (`"\r"`), `prompt` (a regex; else an answer ends after `quiet` seconds of silence, 0.5), `timeout` |
| `tcp` | The program holding the port answers JSON over a local TCP connection, one line per request | `port`, `host` (this PC only), `read_request`, `send_request`, `timeout`, and the keys below |
| `command` | The program has a command line that prints JSON | `read`, `send` (strings run by the shell, or lists of words), `timeout`, and the keys below |
| `file` | The program writes a log file (most terminal programs can) | `path`, `encoding`. Read only |

For `tcp` and `command`, the answer to a read holds the last lines with their numbers,
`{"lines": [{"no": 812, "text": "..."}]}`, and the answer to a send holds the output,
`{"output": "..."}`. Other names are set with `lines_key`, `no_key`, `text_key` and
`output_key`. In requests and commands, `{since}` and `{text}` are filled in.

```yaml
# printsect.local.yaml: settings for the program on your PC (do not commit)
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

How often lines are read matters only for `tcp` and `command`, whose program keeps a
limited number of lines: ask for more lines than the target prints between reads.

## Other ways to reach a console

A console is a plugin of the role `console`, added through the entry point group
`printsect.console`. It needs `__init__(settings)`, `missed`, `mark()`, `read(since)`,
`send(text, timeout)` and `close()`; a class attribute `SETTINGS` (key -> type) makes
printsect check its keys.

## Inside

| File | Holds |
|---|---|
| `cli.py` | The commands |
| `base.py` | `Line`, the `Console` protocol, `wait_for`, help for JSON answers |
| `consoles.py` | `serial`, `tcp`, `command`, `file` |
