"""Consoles that ship with printsect. Anything tool-specific belongs in settings or a plugin.

    serial   open the port here (pyserial: pip install printsect[serial])
    tcp      ask a local program that holds the port, one JSON line per request
    command  ask a program through its command line, which prints JSON
    file     follow a log file that a terminal program writes
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
from typing import Any

from ..core.errors import ConfigError, PsectError
from ..core.plugins import register
from .base import JsonKeys, Line, Numbered, fill

KEY_SETTINGS = {"lines_key": str, "no_key": str, "text_key": str, "output_key": str}


def _quote(text: str) -> str:
    return subprocess.list2cmdline([text]) if os.name == "nt" else shlex.quote(text)


@register("console", "command")
class CommandConsole:
    """`read` prints JSON with numbered lines; `send` sends {text} and prints the answer."""

    SETTINGS = {"read": (str, list), "send": (str, list), "timeout": (int, float),
                **KEY_SETTINGS}

    def __init__(self, settings: dict[str, Any]) -> None:
        if not settings.get("read"):
            raise ConfigError("console.read: the command that prints the console lines as JSON")
        self.settings = settings
        self.keys = JsonKeys.from_settings(settings)
        self.timeout = float(settings.get("timeout", 30))
        self._numbered = Numbered()

    @property
    def missed(self) -> int:
        return self._numbered.missed

    def _run(self, key: str, **values: str) -> str:
        template = self.settings[key]
        if isinstance(template, list):  # words: no shell, no quoting needed
            args: Any = fill(template, **values)
            shell = False
        else:
            args = fill(template, **{k: _quote(v) for k, v in values.items()})
            shell = True
        try:
            done = subprocess.run(args, shell=shell, capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise PsectError(f"console.{key} took over {self.timeout:g}s") from exc
        except OSError as exc:
            raise ConfigError(f"console.{key}: cannot run it: {exc}") from exc
        out = done.stdout.decode("utf-8", errors="replace")
        if done.returncode != 0:
            err = done.stderr.decode("utf-8", errors="replace").strip().splitlines()[-3:]
            raise PsectError(f"console.{key} failed (exit {done.returncode}): {' '.join(err)}")
        return out

    def _lines(self, since: int) -> list[Line]:
        text = self._run("read", since=str(since))
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PsectError(f"console.read did not print JSON: {exc}") from exc
        return self.keys.lines_of(data, "console.read")

    def mark(self) -> int:
        return max((ln.no for ln in self._lines(0)), default=0)

    def read(self, since: int) -> list[Line]:
        return self._numbered.after(self._lines(since), since)

    def send(self, text: str, timeout: float | None = None) -> str:
        if not self.settings.get("send"):
            raise ConfigError("console.send is not set, so this console cannot send commands")
        out = self._run("send", text=text)
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return out  # plain text answers are fine
        return str(data.get(self.keys.output, "")) if isinstance(data, dict) else out

    def close(self) -> None:
        pass


@register("console", "tcp")
class TcpConsole:
    """A local program holding the port answers one JSON line per connection.

    `read_request` and `send_request` are the JSON to send; {text} and {since} in their
    strings are filled in. The answers keep lines and output where `*_key` say.
    """

    SETTINGS = {"host": str, "port": int, "read_request": dict, "send_request": dict,
                "timeout": (int, float), **KEY_SETTINGS}

    def __init__(self, settings: dict[str, Any]) -> None:
        if "port" not in settings or "read_request" not in settings:
            raise ConfigError("console: the tcp console needs port and read_request")
        self.host = str(settings.get("host", "127.0.0.1"))
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            raise ConfigError("console.host: only this PC (127.0.0.1) is allowed")
        self.port = int(settings["port"])
        self.settings = settings
        self.keys = JsonKeys.from_settings(settings)
        self.timeout = float(settings.get("timeout", 30))
        self._numbered = Numbered()

    @property
    def missed(self) -> int:
        return self._numbered.missed

    def _ask(self, request: Any, timeout: float) -> Any:
        try:
            with socket.create_connection((self.host, self.port), timeout=2.0) as s:
                s.settimeout(timeout)
                s.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
                answer = s.makefile("r", encoding="utf-8").readline()
        except OSError as exc:
            raise PsectError(f"console: nothing answers on {self.host}:{self.port} ({exc}); "
                             "is the program that holds the port running?") from exc
        try:
            return json.loads(answer)
        except json.JSONDecodeError as exc:
            raise PsectError(f"console: the answer was not JSON: {answer[:200]!r}") from exc

    def _lines(self, since: int) -> list[Line]:
        data = self._ask(fill(self.settings["read_request"], since=str(since)), self.timeout)
        return self.keys.lines_of(data, "console.read_request")

    def mark(self) -> int:
        return max((ln.no for ln in self._lines(0)), default=0)

    def read(self, since: int) -> list[Line]:
        return self._numbered.after(self._lines(since), since)

    def send(self, text: str, timeout: float | None = None) -> str:
        request = self.settings.get("send_request")
        if not request:
            raise ConfigError("console.send_request is not set, so this console cannot send")
        data = self._ask(fill(request, text=text), timeout or self.timeout)
        if isinstance(data, dict) and data.get("ok") is False and data.get("error"):
            raise PsectError(f"console: {data['error']}")
        return str(data.get(self.keys.output, "")) if isinstance(data, dict) else str(data)

    def close(self) -> None:
        pass


@register("console", "file")
class FileConsole:
    """Follows a log file another program writes (lines are numbered from its start)."""

    SETTINGS = {"path": str, "encoding": str}

    def __init__(self, settings: dict[str, Any]) -> None:
        if not settings.get("path"):
            raise ConfigError("console.path: the log file to follow")
        self.path = str(settings["path"])
        self.encoding = str(settings.get("encoding", "utf-8"))
        self.missed = 0
        self._offset = 0
        self._partial = b""
        self._lines: list[Line] = []

    def _pull(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return  # not there yet
        if size < self._offset:  # truncated or replaced: start over, keep numbering
            self._offset, self._partial = 0, b""
        with open(self.path, "rb") as handle:
            handle.seek(self._offset)
            data = handle.read()
        self._offset += len(data)
        *whole, self._partial = (self._partial + data).split(b"\n")
        n = len(self._lines)
        for i, raw in enumerate(whole, start=n + 1):
            text = raw.rstrip(b"\r").decode(self.encoding, errors="replace")
            self._lines.append(Line(i, text))

    def mark(self) -> int:
        self._pull()
        return len(self._lines)

    def read(self, since: int) -> list[Line]:
        self._pull()
        return self._lines[since:]

    def send(self, text: str, timeout: float | None = None) -> str:
        raise PsectError("the file console only reads; send commands from the program "
                         "that writes the log")

    def close(self) -> None:
        pass


@register("console", "serial")
class SerialConsole:
    """Opens the port here. Only when no other program holds it (a port has one owner)."""

    SETTINGS = {"port": str, "baudrate": int, "encoding": str, "eol": str, "prompt": str,
                "quiet": (int, float), "timeout": (int, float)}

    def __init__(self, settings: dict[str, Any]) -> None:
        if not settings.get("port"):
            raise ConfigError("console.port: e.g. COM3 or /dev/ttyUSB0")
        try:
            import serial  # pyserial
        except ImportError as exc:
            raise ConfigError("the serial console needs pyserial: pip install "
                              "'printsect[serial]'") from exc
        self.encoding = str(settings.get("encoding", "utf-8"))
        self.eol = str(settings.get("eol", "\r"))
        self.prompt = re.compile(settings["prompt"]) if settings.get("prompt") else None
        self.quiet = float(settings.get("quiet", 0.5))
        self.timeout = float(settings.get("timeout", 10))
        self.missed = 0
        try:
            self._port = serial.serial_for_url(str(settings["port"]),
                                               baudrate=int(settings.get("baudrate", 115200)),
                                               timeout=0.1)
        except (serial.SerialException, ValueError) as exc:
            raise PsectError(f"cannot open {settings['port']}: {exc}. If another program "
                             "holds the port, use its tcp or command console") from exc
        self._lines: list[Line] = []
        self._tail = ""  # the line being printed, not ended yet (often the prompt)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                data = self._port.read(4096)
            except Exception:  # the port went away
                return
            if not data:
                continue
            *whole, buf = (buf + data).split(b"\n")
            with self._lock:
                for raw in whole:
                    text = raw.rstrip(b"\r").decode(self.encoding, errors="replace")
                    self._lines.append(Line(len(self._lines) + 1, text))
                self._tail = buf.decode(self.encoding, errors="replace")

    def mark(self) -> int:
        with self._lock:
            return len(self._lines)

    def read(self, since: int) -> list[Line]:
        with self._lock:
            return self._lines[since:]

    def send(self, text: str, timeout: float | None = None) -> str:
        start = self.mark()
        self._port.write((text + self.eol).encode(self.encoding))
        limit = time.monotonic() + (timeout or self.timeout)
        last_change, seen = time.monotonic(), start
        while time.monotonic() < limit:
            time.sleep(0.05)
            lines = self.read(start)
            with self._lock:
                tail = self._tail
            if self.prompt and self.prompt.search(tail):
                break
            if len(lines) + start != seen:
                seen, last_change = len(lines) + start, time.monotonic()
            elif lines and not self.prompt and time.monotonic() - last_change >= self.quiet:
                break
        return "\n".join(ln.text for ln in self.read(start))

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._port.close()
