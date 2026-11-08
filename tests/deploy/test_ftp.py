"""The ftp deployer against a real FTP server on this PC (pyftpdlib)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("pyftpdlib")

from pyftpdlib.authorizers import DummyAuthorizer  # noqa: E402
from pyftpdlib.handlers import FTPHandler  # noqa: E402
from pyftpdlib.ioloop import IOLoop  # noqa: E402
from pyftpdlib.servers import ThreadedFTPServer  # noqa: E402

from printsect.core.errors import ConfigError  # noqa: E402
from printsect.deploy import FAILED, OK, WAITING_USER, DeployContext, make_deployer  # noqa: E402
from printsect.suite.approve import commands  # noqa: E402
from printsect.suite.doctor import _deploy  # noqa: E402

PASSWORD = "s3cret"


@pytest.fixture
def server(tmp_path):
    """An FTP server whose user `target` writes into tmp_path/remote."""
    remote = tmp_path / "remote"
    remote.mkdir()
    auth = DummyAuthorizer()
    auth.add_user("target", PASSWORD, str(remote), perm="elradfmw")

    class Handler(FTPHandler):
        pass

    Handler.authorizer = auth
    # Its own loop: the default is shared, and a server shutting down at the end of one
    # test would close the connections of the next.
    srv = ThreadedFTPServer(("127.0.0.1", 0), Handler, ioloop=IOLoop())
    port = srv.socket.getsockname()[1]
    thread = threading.Thread(target=srv.serve_forever, kwargs={"timeout": 0.1}, daemon=True)
    thread.start()
    yield remote, port
    srv.close_all()
    thread.join(timeout=5)


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setenv("PSECT_FTP_PASSWORD", PASSWORD)


def settings(port: int, **more) -> dict:
    return {"type": "ftp", "host": "127.0.0.1", "port": port, "user": "target",
            "timeout": 10, **more}


def ctx(tmp_path: Path, send=None, wait_for=None) -> DeployContext:
    artifact = tmp_path / "build" / "app.out"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_bytes(b"\x7fELF new build")
    return DeployContext(root=tmp_path, run=5, artifact=artifact, prefix="PSECT",
                         wait_for=wait_for, send=send, log=lambda s: None)


def test_puts_the_build_on_the_target(tmp_path, server, password):
    remote, port = server
    result = make_deployer(settings(port, confirm="")).deploy(ctx(tmp_path))
    assert result.status == OK, result.message
    assert (remote / "app.out").read_bytes() == b"\x7fELF new build"


def test_loads_it_on_the_console_then_waits_for_the_banner(tmp_path, server, password):
    remote, port = server
    sent, watched = [], []

    def send(text, timeout):
        assert (remote / "app.out").exists()  # put before it is loaded
        sent.append(text)
        return "ok"

    def wait_for(pattern, timeout):
        watched.append(pattern)
        return "[PSECT-RUN 5] up"

    d = make_deployer(settings(port, after=["load {artifact_name}", "start {run}"]))
    result = d.deploy(ctx(tmp_path, send=send, wait_for=wait_for))
    assert result.status == OK, result.message
    assert sent == ["load app.out", "start 5"]
    assert watched == [r"\[PSECT-RUN 5\]"]
    assert result.details["after"][0] == {"command": "load app.out", "output": "ok"}


def test_without_a_console_a_person_types_the_loading_commands(tmp_path, server, password):
    remote, port = server
    result = make_deployer(settings(port, after=["load {artifact_name}"])).deploy(ctx(tmp_path))
    assert result.status == WAITING_USER
    assert "load app.out" in result.message
    assert (remote / "app.out").exists()


def test_the_password_comes_only_from_the_environment(tmp_path, server, monkeypatch):
    monkeypatch.delenv("PSECT_FTP_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="PSECT_FTP_PASSWORD"):
        make_deployer(settings(server[1])).deploy(ctx(tmp_path))


def test_a_wrong_password_fails(tmp_path, server, monkeypatch):
    monkeypatch.setenv("PSECT_FTP_PASSWORD", "wrong")
    result = make_deployer(settings(server[1], confirm="")).deploy(ctx(tmp_path))
    assert result.status == FAILED


def test_a_password_key_in_the_settings_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="password"):
        make_deployer({"type": "ftp", "host": "h", "password": "x"})


def test_the_password_variable_must_be_one_meant_for_it():
    # Otherwise settings could name any secret in the environment and send it away.
    with pytest.raises(ConfigError, match="PSECT_FTP_"):
        make_deployer({"type": "ftp", "host": "h", "user": "u",
                       "password_env": "AWS_SECRET_ACCESS_KEY"})
    make_deployer({"type": "ftp", "host": "h", "password_env": "PSECT_FTP_BOARD_B"})


def test_tls_checks_the_certificate_unless_told_not_to(project):
    import ssl

    d = make_deployer({"type": "ftp", "host": "h", "tls": True})
    assert d._tls_context(p_root := Path(".")).verify_mode == ssl.CERT_REQUIRED
    off = make_deployer({"type": "ftp", "host": "h", "tls": True, "tls_verify": False})
    assert off._tls_context(p_root).verify_mode == ssl.CERT_NONE
    # Only a literal false turns it off; approval and the deployer read it the same way.
    from printsect.deploy.ftp import verifies

    assert verifies({"tls_verify": None}) and verifies({}) and not verifies({"tls_verify": False})
    p = project({}, config="deploy: {type: ftp, host: h, tls: true, tls_verify: false}\n")
    assert any("certificate NOT checked" in c for c in commands(p))


def test_the_trusted_certificate_is_part_of_the_approval(project, tmp_path):
    from printsect.suite.approve import approve, digest, unapproved

    ca = tmp_path / "certs" / "target.pem"
    ca.parent.mkdir()
    ca.write_text("first", encoding="utf-8")
    p = project({}, config="deploy: {type: ftp, host: h, tls: true, tls_ca: certs/target.pem}\n")
    assert any("sha256" in c for c in commands(p))
    approve(p, digest(p))
    assert not unapproved(p)
    ca.write_text("swapped", encoding="utf-8")
    assert unapproved(p)


def test_a_network_path_for_the_certificate_is_never_read(project):
    from printsect.deploy.ftp import read_ca

    outside = Path.cwd().anchor + "ca.pem"
    for path in ("//server/share/ca.pem", "\\\\server\\share\\ca.pem", outside, "../ca.pem",
                 "certs/C:ca.pem", "C:ca.pem", "ca.pem:stream"):
        settings = {"type": "ftp", "host": "h", "tls": True, "tls_ca": path}
        with pytest.raises(ConfigError, match="inside the project"):
            read_ca(settings, Path("."))
    # not read either when nothing would use it
    assert read_ca({"tls": False, "tls_ca": "//server/x"}, Path(".")) is None


def test_the_certificate_is_not_reached_through_a_link(tmp_path):
    from printsect.deploy.ftp import read_ca

    (tmp_path / "real.pem").write_text("x", encoding="utf-8")
    try:
        (tmp_path / "link.pem").symlink_to(tmp_path / "real.pem")
    except OSError:
        pytest.skip("this PC does not let us make a link")
    settings = {"type": "ftp", "host": "h", "tls": True, "tls_ca": "link.pem"}
    with pytest.raises(ConfigError, match="link"):
        read_ca(settings, tmp_path)
    assert read_ca({**settings, "tls_ca": "real.pem"}, tmp_path)[1] == b"x"


def test_doctor_logs_in_once_approved_and_not_before(project, server, password):
    _, port = server
    p = project({}, config=f"deploy: {{type: ftp, host: 127.0.0.1, port: {port}, "
                           "user: target}\n")
    assert _deploy(p, has_console=False, run_commands=False).status == "warn"
    found = _deploy(p, has_console=False, run_commands=True)
    assert found.status == "ok", found.message
    assert "logged in" in found.message


def test_approval_lists_where_it_puts_and_what_it_types(project):
    p = project({}, config="deploy: {type: ftp, host: 10.0.0.2, user: target, "
                           "path: '/tmp/{artifact_name}', after: ['load x']}\n")
    listed = commands(p)
    assert ("deploy: put to target@10.0.0.2:21 /tmp/{artifact_name} "
            "(password from $PSECT_FTP_PASSWORD, unencrypted)") in listed
    assert "deploy.after (sent to the target): load x" in listed
