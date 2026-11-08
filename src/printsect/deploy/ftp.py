"""The ftp deployer: put the build on a target that runs an FTP server, then load it.

    deploy:
      type: ftp
      host: 192.168.0.10
      user: target                       # default: anonymous
      password_env: PSECT_FTP_PASSWORD   # the password is read from this variable
      path: /tmp/{artifact_name}         # where the file goes on the target
      after:                             # console commands once it is there (loading)
        - 'load /tmp/{artifact_name}'

The password never goes in a settings file: an agent may write those, and they are
shared. A person puts it in the environment variable named by password_env.
FTP sends it unencrypted, so use it on a closed network, or set tls: true for a
server that speaks FTPS (explicit TLS); its certificate is checked (tls_ca for
a self-signed one).

`after` needs a console to send to, which `psect deploy` and `psect run` link in.
Without one (psect-deploy alone) the file is put and the commands are handed to a
person to type.
"""

from __future__ import annotations

import ftplib
import os
import re
import ssl
import stat
from pathlib import Path, PurePath, PureWindowsPath
from typing import Any

from ..core.errors import ConfigError
from ..core.plugins import register
from .base import FAILED, OK, WAITING_USER, DeployContext, DeployResult

DEFAULT_PASSWORD_ENV = "PSECT_FTP_PASSWORD"
# Only a variable meant for this: settings name it, and an agent may write settings,
# so naming any other secret in the environment would send it to the host.
PASSWORD_ENV = re.compile(r"PSECT_FTP_[A-Z0-9_]*\Z")
ANONYMOUS_PASSWORD = "printsect@"


def verifies(settings: dict[str, Any]) -> bool:
    """Whether the server's certificate is checked: off only for a literal false, so a
    missing or odd value (null, "no") never turns it off. Approval shows the same."""
    return settings.get("tls_verify") is not False


MAX_CA_BYTES = 1 << 20


def uses_ca(settings: dict[str, Any]) -> bool:
    """Whether tls_ca is read at all: only when TLS is on and the certificate is checked."""
    return bool(settings.get("tls")) and verifies(settings) and bool(settings.get("tls_ca"))


def read_ca(settings: dict[str, Any], root: Path) -> tuple[Path, bytes] | None:
    """tls_ca, from the project root: the certificate trusted for the target, read once.

    It is read before anything is approved (to show and sign it), so only a plain file
    inside the project, reached through no link: a network path, or a link or junction
    leading to one, would hand that server this PC's login just by being opened. Each
    part is looked at without following it. The deployer uses these same bytes.
    """
    if not uses_ca(settings):
        return None
    text = str(settings["tls_ca"])
    parts = PurePath(text).parts
    # ":" anywhere: a drive inside a part (certs/C:x) or a file stream (x:stream), read
    # the Windows way whatever this PC is, since a project can move between them.
    if (not parts or ":" in text or PurePath(text).anchor or PureWindowsPath(text).anchor
            or text.startswith(("\\\\", "//"))
            or any(p in ("..", ".") or PureWindowsPath(p).anchor for p in parts)):
        raise ConfigError(f"tls_ca: {text!r} must be a path inside the project, from its "
                          "root; copy the certificate there (not a network path)")
    path = root
    try:
        for part in parts:
            parent, path = path, path / part
            if path.parent != parent or path.name != part:  # only ever one name more
                raise ConfigError(f"tls_ca: {text!r} must be a path inside the project")
            info = os.lstat(path)  # never follows a link
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ConfigError(f"tls_ca: {text!r} goes through a link ({path.name}); "
                                  "put the certificate itself in the project")
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError(f"tls_ca: {text!r} is not a file")
        if info.st_size > MAX_CA_BYTES:
            raise ConfigError(f"tls_ca: {text!r} is too large for a certificate")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            return path, handle.read(MAX_CA_BYTES + 1)[:MAX_CA_BYTES]
    except OSError as exc:
        raise ConfigError(f"tls_ca: {text!r} cannot be read: {exc.strerror or exc}") from exc


@register("deployer", "ftp")
class FtpDeploy:
    SETTINGS = {
        "host": str, "port": int, "user": str, "password_env": str, "path": str,
        "passive": bool, "tls": bool, "tls_ca": str, "tls_verify": bool,
        "timeout": (int, float), "after": list,
        "after_timeout": (int, float), "confirm": str, "confirm_timeout": (int, float),
    }

    def __init__(self, settings: dict[str, Any]) -> None:
        if not settings.get("host"):
            raise ConfigError("deploy type 'ftp' needs host: the target's address")
        self.settings = settings
        self.host = str(settings["host"])
        self.port = int(settings.get("port", 21))
        self.user = str(settings.get("user", "anonymous"))
        self.password_env = str(settings.get("password_env", DEFAULT_PASSWORD_ENV))
        if not PASSWORD_ENV.match(self.password_env):
            raise ConfigError(f"password_env: {self.password_env!r} is not a PSECT_FTP_... "
                              "variable; the password is read only from one of those")
        self.path = str(settings.get("path", "{artifact_name}"))
        self.passive = bool(settings.get("passive", True))
        self.tls = bool(settings.get("tls", False))
        self.tls_verify = verifies(settings)
        self.timeout = float(settings.get("timeout", 60))
        self.after = [str(c) for c in settings.get("after") or []]

    def _password(self) -> str:
        if self.user == "anonymous":
            return ANONYMOUS_PASSWORD  # no secret to read, so none is read
        password = os.environ.get(self.password_env)
        if password is None:
            raise ConfigError(f"the FTP password for {self.user}@{self.host} is read from "
                              f"the environment variable {self.password_env}, which is not "
                              "set; a person sets it (never in a settings file)")
        return password

    def _tls_context(self, root: Path) -> ssl.SSLContext:
        """Checks the server's certificate, so TLS also stops a man in the middle. A
        target with a self-signed one gives it as tls_ca; tls_verify: false turns the
        check off (still encrypted, not authenticated) and shows when approving."""
        ca = read_ca(self.settings, root)  # these bytes are what was approved
        context = ssl.create_default_context(
            cadata=ca[1].decode("ascii", "replace") if ca else None)
        if not self.tls_verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _connect(self, root: Path) -> ftplib.FTP:
        ftp: ftplib.FTP = (ftplib.FTP_TLS(context=self._tls_context(root), timeout=self.timeout)
                           if self.tls else ftplib.FTP(timeout=self.timeout))
        ftp.connect(self.host, self.port)
        ftp.login(self.user, self._password())
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()  # the data, not only the login, goes encrypted
        ftp.set_pasv(self.passive)
        return ftp

    def check(self, root: Path) -> str:
        """Log in and log out, sending no file: for psect doctor."""
        try:
            ftp = self._connect(root)
        except ftplib.all_errors as exc:  # OSError (ssl too) and FTP replies
            raise ConfigError(f"ftp {self.user}@{self.host}:{self.port}: {exc}") from exc
        try:
            where = ftp.pwd()
        finally:
            _quit(ftp)
        return f"logged in to {self.host} as {self.user} (at {where})"

    def deploy(self, ctx: DeployContext) -> DeployResult:
        if ctx.artifact is None:
            return DeployResult(FAILED, "nothing to put: no build output (build.artifact)")
        if not ctx.artifact.is_file():
            return DeployResult(FAILED, f"{ctx.artifact} is not there; build it first")
        remote = ctx.fill(self.path)
        ctx.log(f"ftp: {ctx.artifact.name} -> {self.user}@{self.host}:{remote}")
        try:
            ftp = self._connect(ctx.root)
            try:
                with open(ctx.artifact, "rb") as handle:
                    ftp.storbinary(f"STOR {remote}", handle)
            finally:
                _quit(ftp)
        except ConfigError:
            raise
        except ftplib.all_errors as exc:  # OSError and the FTP replies
            return DeployResult(FAILED, f"ftp to {self.host} failed: {exc}")
        details: dict[str, Any] = {"put": remote}

        commands = [ctx.fill(c) for c in self.after]
        if commands and ctx.send is None:
            listed = "; ".join(commands)
            return DeployResult(WAITING_USER, f"{ctx.artifact.name} is on the target at "
                                f"{remote}; now type on its console: {listed}", details)
        answers = []
        for command in commands:
            ctx.log(f"console: {command}")
            assert ctx.send is not None
            answers.append({"command": command, "output": ctx.send(
                command, float(self.settings.get("after_timeout", 30)))[-2000:]})
        details["after"] = answers
        from .deployers import _confirm

        return _confirm(ctx, self.settings, DeployResult(OK, "put and loaded", details))


def _quit(ftp: ftplib.FTP) -> None:
    try:
        ftp.quit()
    except ftplib.all_errors:
        ftp.close()
