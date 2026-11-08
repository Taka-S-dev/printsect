# deploy — put a new build on the target

English | [日本語](README.ja.md)

`psect-deploy` gets a build onto the target the way the config says, and checks
that the new build is the one running. How it is done is swappable: a command, a
person, or a plugin for a particular target. It runs on its own; inside printsect
it is `psect deploy`.

## Command

`psect-deploy [--run N]` — `N` is the run number the build carries; the target
prints `[PSECT-RUN N]` when it starts, which is how a new build is told from an old
one still running. `psect deploy` watches for it on the console set up under
`console:` (see the console tool); `psect-deploy` alone asks a person instead.

| Exit | Meaning |
|---|---|
| 0 | The build is on the target |
| 1 | It failed |
| 2 | The config is wrong |
| 3 | A person has to do something first (see the message) |

Takes `--json` and `--root DIR`.

## Deployers

Pick one with `deploy: {type: …}`.

| Type | What it does | Settings |
|---|---|---|
| `none` (default) | Nothing: a program run on this PC | — |
| `manual` | A person transfers it (e.g. with a vendor tool). Goes on by itself when the run banner shows on the console, else asks or exits 3 | `message`, `until` (default: the run banner), `timeout` (600) |
| `command` | Runs commands on this PC, then waits for the run banner | `commands` (list), `timeout` (120), `confirm` (pattern, `""` to skip), `confirm_timeout` (60) |
| `ftp` | Puts the file on a target that runs an FTP server, sends `after` (the loading commands) to its console, then waits for the run banner | `host`, `port` (21), `user` (anonymous), `password_env` (`PSECT_FTP_PASSWORD`; a `PSECT_FTP_…` name), `path` (`{artifact_name}`), `passive` (true), `tls` (false), `tls_ca`, `tls_verify` (true), `timeout` (60), `after` (list), `after_timeout` (30), `confirm`, `confirm_timeout` |

In messages and commands, `{run}`, `{artifact}`, `{artifact_name}`, `{root}`, `{device}` and
`{prefix}` are filled in. The build file comes from `build: {artifact: …}`.

```yaml
deploy:
  type: manual
  message: "Load {artifact_name} with the vendor tool"
build:
  artifact: build/out/netdrv.out
```

The ftp password never goes in a settings file: a person sets the environment
variable named by `password_env`. FTP sends it in the clear, so keep to a closed
network or use `tls: true` with a server that speaks FTPS. Its certificate is checked;
give a self-signed one as `tls_ca` (a file in the project) (`tls_verify: false` skips the check and shows
when approving). `after` is sent on the
console `psect` links in; `psect-deploy` alone puts the file and exits 3 with the
commands for a person to type. `psect doctor` logs in (sending no file) once the
settings are approved.

```yaml
deploy:
  type: ftp
  host: 192.168.0.10
  user: target
  path: "/tmp/{artifact_name}"
  after:
    - "load /tmp/{artifact_name}"
```

## A deployer for your target

A deployer is a plugin of the role `deployer`: a class taking `settings`, with
`deploy(ctx) -> DeployResult`. Ship it in its own package with the entry point group
`printsect.deployer`; the same name as a builtin replaces it. A class attribute
`SETTINGS` (key -> type, as in `deployers.py`) makes printsect check its keys; without
it the deployer checks its own. `ctx` gives the root,
run number, artifact, the prefix, `ask(question)` to ask a person and
`wait_for(pattern, timeout)` to watch the console and `send(text, timeout)` to type
on it (each `None` when there is no console).

## Inside

| File | Holds |
|---|---|
| `cli.py` | The command |
| `base.py` | `DeployContext`, `DeployResult`, the `Deployer` protocol, the statuses |
| `deployers.py` | `none`, `manual`, `command` |
| `ftp.py` | `ftp` |
