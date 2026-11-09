# deps — what each output of a C build is made from

English | [日本語](README.ja.md)

`psect-deps` reads a build and answers: which sources and headers go into which
object, archive and output — and so what has to be rebuilt when a file changes.
It runs on its own; inside printsect it is `psect deps` and also marks where
probes are.

```
build/out/netdrv.out
├── build/obj/netdrv_part.o
│   ├── build/lib/libnet.a
│   │   ├── rx.o ← net/rx.c
│   │   │   └── util/ring.h
│   │   └── tx.o ← net/tx.c
│   └── build/lib/libutil.a
│       └── ring.o ← util/ring.c
└── netdrv_ctors.o ← gen/ctors.c
```

## Commands

| Command | What it does |
|---|---|
| `psect-deps` | Read the build into `.printsect/deps.json` and `DEPS.md` |
| `psect-deps tree [NAME…]` | The tree above. `-s` source names, `--headers` every header, `--probed` only branches with a probe (in `psect deps`) |
| `psect-deps tree -f PATTERN` | Keep only the way to matching files: text, `*.h`, or `--regex` |
| `psect-deps which FILE` | What a file is built into (first 20 ways; `--all` for more) |
| `psect-deps find PATTERN` | Files whose path matches; `--quickfix` gives `file:1: chain` for editors |
| `psect-deps html` | One self-contained page to browse and search in a browser |

Every command takes `--json` and `--root DIR`. Only `psect-deps` itself reads the
build; `tree`, `which`, `find` and `html` answer from what it saved and never run make,
because even `make -n` runs a Makefile's `$(shell …)`. When a Makefile or the config has
changed since, they say so; run `psect-deps` again.

Try it: `psect-deps --root examples/demo`, then `psect-deps tree --root examples/demo`.

## How it reads a build

1. **Make prints what it would run.** `make -n -B -w` lists every command of a full
   build without running any: variables, `include`, pattern rules and sub-makes are
   already expanded. (`$(shell …)` and `$(MAKE)` lines do run; the sub-make gets
   `-n` too.)
2. **Commands are told apart by their arguments,** not the tool's name, so any
   cross toolchain works: `-c` and a source is a compile; an archive followed by
   objects with no `-o` is an archive step; `-o` with objects or archives as input is
   a link (`ld -r` partial links too). `-l` is found through `-L` and the archives
   the build makes. Folders come from `Entering directory` lines and `cd` in recipes.
3. **Headers come from following `#include`** with each compile's `-I`, `-iquote`
   and `-include`, because Makefiles rarely list headers. Only headers inside the
   project count. `#if` is not evaluated, so the answer may be a little too wide,
   never too narrow.

Large projects are fine: about a million lines (8,000 sources, 3,000 headers) read
in about 6 seconds, and each command answers in 0.5 to 2 seconds. That is with the
files already read once by the OS; the very first read of freshly written files can
take tens of seconds on Windows, while virus scanning looks at each one. See
`benchmarks/deps/`.

## Settings (`printsect.yaml`, all optional)

```yaml
deps:
  command: make -n -B -w   # what prints the build; e.g. make -f build.mk -n -B -w all
  cwd: .                   # where to run it
  timeout: 300
  headers: true            # false: Makefile only (.c -> .o -> .a -> out), faster
  # source_extensions / object_extensions / archive_extensions, ignore_tools,
  # watch_names / watch_suffixes: rarely needed
```

Where make cannot run here (only inside a vendor tool's shell), save its output
there and read the file: `make -n -B -w > make.log`, then `psect-deps --from-log make.log`.

## Files it writes

| File | For |
|---|---|
| `.printsect/deps.json` | The graph: `outs`, `archives`, `objects` (each with `source`, `headers`) |
| `.printsect/DEPS.md` | The tree and each header's outputs, to read in an editor |
| `.printsect/deps.html` | The page from `psect-deps html`. Holds paths relative to the project only, fetches nothing, safe to hand on |

## Other build systems

The reader is a plugin of the role `deps`. A package can add one through the entry
point group `printsect.deps`; it needs `__init__(settings)` and
`scan(project, *, log=None) -> Graph`. Pick it with `deps: {type: <name>}`. A class
attribute `SETTINGS` (key -> type, as in `make.py`) makes printsect check its keys;
without it the reader checks its own.

## Inside

| File | Holds |
|---|---|
| `cli.py` | The commands; `configure()` lets `psect` mount them and add probe marks |
| `graph.py` | The `Graph` and its questions (`which`, `roots_for_file`, …), with indexes built once |
| `make.py` | The builtin reader: runs `deps.command`, then `makelog` and `includes` |
| `makelog.py` | Turning `make -n` output into a graph |
| `includes.py` | Following `#include` |
| `paths.py` | Making paths relative to the project, fast |
| `search.py` | `Matcher`: text, glob or regex |
| `tree.py` | The tree and `which` as text |
| `markdown.py` | `DEPS.md` |
| `html.py`, `viewer.html` | The browser page and its template |
