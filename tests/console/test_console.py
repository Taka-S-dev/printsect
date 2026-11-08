from __future__ import annotations

import json
import socket
import sys
import threading
import time

import pytest

from printsect.cli import main
from printsect.console import make_console, wait_for
from printsect.console.cli import main as console_main
from printsect.core.errors import ConfigError

# A stand-in for a program that holds the port and prints JSON: its screen is a file.
FAKE_TOOL = r'''
import json, sys
screen = sys.argv[1]
if sys.argv[2] == "read":
    lines = json.load(open(screen, encoding="utf-8"))
    print(json.dumps({"lines": [{"no": n, "text": t} for n, t in lines]}))
else:
    print(json.dumps({"ok": True, "output": "got " + sys.argv[3]}))
'''


@pytest.fixture
def screen(tmp_path):
    path = tmp_path / "screen.json"
    tool = tmp_path / "fake_tool.py"
    tool.write_text(FAKE_TOOL, encoding="utf-8")

    def show(*lines):
        path.write_text(json.dumps(list(lines)), encoding="utf-8")

    show()
    settings = {
        "type": "command",
        "read": [sys.executable, str(tool), str(path), "read"],
        "send": [sys.executable, str(tool), str(path), "send", "{text}"],
    }
    return show, settings


def test_command_console_reads_after_a_line_and_counts_lost_ones(screen):
    show, settings = screen
    show((1, "boot"), (2, "> "))
    c = make_console(settings)
    assert c.mark() == 2
    assert c.read(0)[0].text == "boot"
    show((5, "a"), (6, "b"))  # 3 and 4 scrolled away before this read
    assert [ln.text for ln in c.read(2)] == ["a", "b"]
    assert c.missed == 2
    assert c.send("ver") == "got ver"


def test_settings_are_checked_and_named(screen):
    _, settings = screen
    with pytest.raises(ConfigError, match="console.raed: not a setting"):
        make_console({**settings, "raed": "x"})
    with pytest.raises(ConfigError, match="no console is set up"):
        make_console({})


class FakeHolder:
    """A program holding the port that answers one JSON line per connection."""

    def __init__(self):
        self.lines = [(1, "boot"), (2, "> ")]
        self.sent = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                req = json.loads(conn.makefile("r", encoding="utf-8").readline())
                if "cmd" in req:
                    self.sent.append(req["cmd"])
                    ans = {"ok": True, "output": f"done {req['cmd']}"}
                else:
                    ans = {"ok": True, "lines": [{"no": n, "text": t} for n, t in self.lines]}
                conn.sendall((json.dumps(ans) + "\n").encode())


def test_tcp_console_talks_to_the_program_holding_the_port():
    holder = FakeHolder()
    c = make_console({"type": "tcp", "port": holder.port,
                      "read_request": {"screen": True, "lines": 500},
                      "send_request": {"cmd": "{text}"}})
    assert c.mark() == 2
    assert c.send("load app.out") == "done load app.out"
    assert holder.sent == ["load app.out"]
    holder.lines.append((3, "[PSECT-RUN 4]\r"))  # the target's own line end, kept as is
    line = wait_for(c, r"PSECT-RUN 4", 2, since=2)
    assert line.no == 3 and line.text == "[PSECT-RUN 4]"
    holder.sock.close()


def test_tcp_console_only_talks_to_this_pc():
    with pytest.raises(ConfigError, match="only this PC"):
        make_console({"type": "tcp", "host": "10.0.0.5", "port": 1, "read_request": {}})


def test_file_console_follows_a_log_and_a_restart(tmp_path):
    log = tmp_path / "term.log"
    log.write_bytes(b"one\r\ntwo\r\npart")
    c = make_console({"type": "file", "path": str(log)})
    assert c.mark() == 2  # "part" has no end of line yet
    with log.open("ab") as f:
        f.write(b"ial\nthree\n")
    assert [ln.text for ln in c.read(2)] == ["partial", "three"]
    log.write_bytes(b"new\n")  # the log was started again: numbering goes on
    assert [(ln.no, ln.text) for ln in c.read(4)] == [(5, "new")]


def test_wait_for_sees_a_line_that_comes_later(tmp_path):
    log = tmp_path / "term.log"
    log.write_bytes(b"old [PSECT-RUN 4]\n")
    c = make_console({"type": "file", "path": str(log)})

    def later():
        time.sleep(0.3)
        with log.open("ab") as f:
            f.write(b"[PSECT-RUN 4] started\n")

    threading.Thread(target=later).start()
    line = wait_for(c, r"\[PSECT-RUN 4\]", 3)  # from now: the old line does not count
    assert line is not None and line.text.endswith("started")
    assert wait_for(c, "never", 0.3) is None


def test_serial_console_on_a_loopback_port():
    pytest.importorskip("serial")
    c = make_console({"type": "serial", "port": "loop://", "eol": "\n", "quiet": 0.2})
    try:
        assert c.send("hello") == "hello"  # a loopback answers with what was sent
        assert c.mark() == 1 and c.read(0)[0].text == "hello"
    finally:
        c.close()


def test_cli_read_wait_send(project, screen, capsys):
    show, settings = screen
    show((1, "boot"), (2, "[PSECT-AI 3 rx] len=0"))
    cfg = "console:\n" + "".join(f"  {k}: {json.dumps(v)}\n" for k, v in settings.items())
    p = project({}, config=cfg)
    root = ["--root", str(p.root)]
    assert console_main(["read", "--since", "1", *root]) == 0
    assert capsys.readouterr().out.strip() == "2: [PSECT-AI 3 rx] len=0"
    assert console_main(["mark", "--json", *root]) == 0
    assert json.loads(capsys.readouterr().out)["last"] == 2
    assert console_main(["wait", "PSECT-AI 3", "--since", "0", "--timeout", "1", *root]) == 0
    assert console_main(["wait", "nope", "--timeout", "0.3", *root]) == 1
    capsys.readouterr()
    assert main(["console", "send", "ver", *root]) == 0
    assert capsys.readouterr().out.strip() == "got ver"


def test_psect_deploy_goes_on_when_the_console_shows_the_new_build(project, tmp_path, capsys):
    log = tmp_path / "term.log"
    log.write_bytes(b"")
    p = project({}, config=(
        f"console:\n  type: file\n  path: {json.dumps(str(log))}\n"
        "deploy:\n  type: manual\n  timeout: 5\n"))

    def target_restarts():
        time.sleep(0.5)
        with log.open("ab") as f:
            f.write(b"[PSECT-RUN 7] app.out loaded\n")

    threading.Thread(target=target_restarts).start()
    assert main(["deploy", "--run", "7", "--json", "--root", str(p.root)]) == 0
    assert "loaded" in json.loads(capsys.readouterr().out)["details"]["confirmed_by"]


def test_check_reads_console_settings_without_opening_the_port(project, capsys):
    p = project({}, config="console:\n  type: serial\n  port: COM99\n  baud: 9600\n")
    assert main(["check", "--root", str(p.root)]) == 2
    assert "console.baud: not a setting (did you mean baudrate?)" in capsys.readouterr().out


def test_psect_deploy_sees_a_banner_printed_while_its_commands_run(project, tmp_path, capsys):
    # The command deployer's own command makes the target print the banner at once:
    # it must still count, because the console was noted before the deploy began.
    log = tmp_path / "term.log"
    log.write_bytes(b"old\n")
    writer = tmp_path / "load.py"
    writer.write_text(f"open({str(log)!r}, 'a').write('[PSECT-RUN 9] up' + chr(10))\n",
                      encoding="utf-8")
    p = project({}, config=(
        f"console:\n  type: file\n  path: {json.dumps(str(log))}\n"
        "deploy:\n  type: command\n"
        f"  commands: [{json.dumps(sys.executable + ' ' + str(writer))}]\n"
        "  confirm_timeout: 2\n"))
    assert main(["deploy", "--run", "9", "--root", str(p.root)]) == 0
    assert "ok" in capsys.readouterr().out
