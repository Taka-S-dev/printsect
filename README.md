# printsect

English | [日本語](README.ja.md)

Print-probe driven debugging for C, run by an AI agent or by hand.

printsect is a set of small tools. Install it once; use any tool on its own, or all
of them together through `psect`.

| Tool | On its own | Inside printsect | What it does | Guide |
|---|---|---|---|---|
| deps | `psect-deps` | `psect deps` | What each output of a C build is made from: sources and headers -> objects -> archives -> outputs. Reads the Makefile through `make -n`, so nothing is built. Tree, search, "what does this file go into", and a page for the browser. | [deps](src/printsect/deps/README.md) |
| probe | `psect-probe` | `psect probe` | Put print probes into C source, keep a list of where they are and why, and take out only the ones it put in. | [probe](src/printsect/probe/README.md) |
| console | `psect-console` | `psect console` | Read what the target prints, line by line, send it commands, and wait for a line. Opens a serial port itself, or goes through the program that already holds it (TCP, its command line, or its log file). | [console](src/printsect/console/README.md) |
| deploy | `psect-deploy` | `psect deploy` | Put the build on the target the way the config says (a command, FTP, or a person doing it). | [deploy](src/printsect/deploy/README.md) |

How the tools are kept apart, and how to add one: [core](src/printsect/core/README.md).

`psect` joins them up: the deps tree marks where probes are, `psect deploy` watches the
console for the new build, and so on. Each tool
also runs as `python -m printsect.<tool>`.

```sh
uv tool install printsect      # or: pipx install printsect

psect-deps tree                # the build of the project in this folder
psect-deps which src/inc/net.h # what a change to this header rebuilds
psect-deps html                # browse and search it in a browser
```

Settings go in `printsect.yaml` at the project root; every tool works without it.
Each tool stops on a misspelt or mistyped key in its own sections and names the
nearest real one; `psect check` checks every section at once. Its state goes in
`.printsect/`, which ignores itself for git.

## Setting up a project

Nothing about your project has to leave your PC: you fill in the settings and
printsect checks them where they are.

1. `psect init` writes `printsect.yaml` (shared) and `printsect.local.yaml` (this PC:
   the console), both with commented examples in the order to fill them in.
2. Fill in a section, then run `psect doctor`. It tries each part for real — reads
   the build with `make -n`, reaches the console, looks at the line chosen for the run
   banner, looks for the build and deploy commands — and for each one says ok, warn
   or ng, with what to do next. It builds nothing, sends nothing to the target and
   changes no file.
3. When everything is ok, put a probe in and `psect run`.

```
ok   deps       read the build: 4 output(s), 12 archive(s), 830 source(s), 410 header(s)
ok   console    tcp: reached, last line 1534
warn banner     app/init.c:41 is in app_init(); line 41 does not end a statement
                -> the banner line goes below LINE: choose the end of a statement ...
ng   build      no build.command
                -> the command you build with; {outs} becomes the outputs to rebuild ...
```

## The loop: psect run

One turn of debugging is one command. `psect run` checks the probes against the
source, puts in the run banner (a line printing `[PSECT-RUN n]`, so this build's
output is told from an old one's), builds, deploys each output and waits for the
banner on the console, runs the steps that reproduce the problem, reads the console
until a stop condition, and counts what each probe printed:

```
run 3: ok at done (quiet 5s)
212 line(s) read; outputs: build/out/netdrv.out
#7    3 hits (lines 140-188)  net/rx.c:41  len 0 reaches here?
       len = 0
#8    not reached  net/rx.c:55  the error path runs?
```

The counts go into each probe's record, and everything is kept in
`.printsect/runs/<n>/` (build log, console lines, summary). `psect hits [N]` shows a
run again. `psect build --plan` shows the build commands for the probes in now, and
`psect build` runs them without the rest. When a step needs a person (`ask:`) and
nobody is at the terminal, the run
stops with exit 3 and the request; `psect run --resume` carries on after it.

```yaml
build:
  steps:                       # filled in from the graph, run in the order written
    - run: make clean          # no `for`: once, here
    - for: archives            # each archive holding a probed file, in build order
      run: make -C {made_in}   # the folder the build made it in
    - for: outs                # each output holding one of them
      run: make {path}       # the output as the build names it
  artifacts: auto              # deploy those outputs; or a list
run:
  banner: app/init.c:42        # where the banner line goes: code every new build runs
  ready: "^> "                # then wait for this (the prompt) before reproducing
deploy:
  type: manual                 # or command, ftp, or a plugin
console:
  type: tcp                    # see the console tool
reproduce:
  - send: app_start
  - wait: "ready"
  - ask: Unplug the cable
stop:
  match: ["Exception", "assert"]
  quiet: 5                     # seconds with no output
  timeout: 120
```

Waiting is part of every turn: each build step runs to its end, and afterwards every
output to deploy must be newer than the build's start (a build that handed the work
to another tool and returned at once is caught, not deployed stale); the deploy waits
for the run banner; `run.ready` then waits for the target to take commands.

## Several devices

When targets differ — where their outputs are kept, how they are loaded, which port
their console is on — name each under `devices:` with only what differs. `psect use
board-b` chooses one for the project (`--device` or `PSECT_DEVICE` for one command);
everything then runs with that device's settings, and `{device}` is its name. With
several set up and none chosen, build, deploy and run stop instead of guessing.

```yaml
devices:
  board-a:
    build: {artifact_path: "out/a/{name}"}
  board-b:
    build: {artifact_path: "out/b/{name}"}
    deploy: {type: manual, message: "Load {artifact_name} with the B loader"}
```

## Letting an AI agent drive it

`psect mcp` serves the tools to an agent over MCP (standard input and output), so the
agent runs the loop itself: put probes in, `run`, read the hits, narrow down, clean up.
Each MCP tool is the same command as on the command line, with `--json`, so the agent
can do what a person can and nothing more. Two guides go to the agent as the server's
instructions, and anyone can read them: `psect guide` (the way to work: hypotheses, a
purpose for every probe, handing steps that need a person back to them) and `psect
guide setup` (setting a project up until `doctor` is all ok: what to read from the
project, what to ask the person rather than guess, what to show them before a first
build). `psect guide settings` lists every key with examples. So an agent can set a
project up as well as debug it, with the person answering only what it cannot know.

```sh
# Claude Code, in the project folder
claude mcp add printsect -- psect mcp --root .
```

Any other MCP client starts `psect mcp --root <project>` the same way.

## Running commands from a project

Some settings are commands that printsect runs: `deps.command` (by default
`make -n -B -w`) and the `command` deployer's `commands`. `make -n` builds nothing,
but make still runs `$(shell …)` in Makefiles and recursive `$(MAKE)` lines. So treat
`printsect.yaml` like a Makefile: in a project you did not write, read it before
running `psect deps` or `psect deploy`. Showing a tree never runs a command the
project set; only the stock `make -n -B -w` runs without being asked for.

An agent can write the settings, so it could write a command and then ask for it to
run. It cannot: what the settings would run — the deps, build, deploy, console and
reproduce sections as written, and the Makefiles that even `make -n` runs
`$(shell ...)` from — runs for an agent (over MCP) only after a person approved it
with `psect approve`. That command lists everything with control characters made
visible and asks at a terminal; it has no flag to skip the question and is never
offered to the agent. Changing anything in it needs approving again; until then the
agent's tools refuse and name what is waiting, and `psect doctor` lists it.

The approval is kept outside the project, in your own settings folder (`%APPDATA%\printsect`
or `~/.config/printsect`), signed with a key kept there, so it cannot come with a
project or be made by editing project files. An agent allowed to write anywhere as
you could still forge one on purpose; keep its file access to the project.

Rather than guess which files make reads, approval covers every file in the project
except C and C++ sources and headers (where probes go), generated `.d` dependency
files and binary build outputs (`.o`, `.a`, `.out` and the like): Makefiles,
whatever they include and however, scripts, settings. A `.d` file is left out only
while it holds nothing but plain `target: prerequisites` rules, and a source or header
is covered all the same when it holds make's `$(`/`${` syntax or a covered file names
it on an `include` line (followed in turn). A source or header that a Makefile reaches
only through a variable or wildcard and that holds no `$` is the case this cannot see
without running make. Linked folders are followed. Changing, adding or removing a covered file needs approving again. Other
files the build writes can be left out with `deps.approval_ignore` (patterns), which
is itself part of what is approved. Not covered: binary build outputs, and files
outside the project such as a toolchain's own makefiles (paths to them in
`deps.command` are listed when approving).

Approval is a guard, not a sandbox. An agent that may edit the build itself — the
Makefiles, the scripts they call — can change what an approved build runs on the next
build, beyond what any listing can show. So let the agent edit sources and the
printsect settings only: in Claude Code, deny edits to `Makefile`, `*.mk` and build
scripts in the permission settings.

Try it on the made-up project in `examples/demo`:

```sh
psect-deps tree --root examples/demo
```
