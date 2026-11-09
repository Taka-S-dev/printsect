# map — which module of a C project uses which

English | [日本語](README.ja.md)

`psect-map` reads the C sources of a project and answers the first question about
code you do not know: what are its parts, which part uses which, and where are the
entrances. It is short enough for an AI agent to read before it starts. It runs on its
own; inside printsect it is `psect map`, which also groups by the build's archives and
marks where probes are.

It reads files and runs nothing: no compiler, no make, no index program. So it works
before anything is set up and needs no approval.

```
948 source file(s), 22 module(s) by directory, counts are (symbol, using file) pairs
crypto/evp/  core  files 61  in 1519 (16)  out 358 (12)
crypto/bn/  core  files 33  in 1054 (14)  out 19 (4)
uses (user -> used):
  crypto/ec/ -> crypto/bn/  313
left out: same name in several files 80 symbol(s) (391 use(s)); static elsewhere 4833
```

`in 1054 (14)` means 1054 uses from 14 other modules. `start` uses others and nothing
uses it, `base` is only used, `core` is used by three or more and uses others.

`cycles` lists the modules that use each other, directly or through others, with one
shortest way round each and the symbols on it. It says `cycles: none` when there are none.

## Commands

| Command | What it does |
|---|---|
| `psect-map` | The whole picture: modules with most users first, and who uses whom |
| `psect-map focus MODULE` | One module, archive or any folder: who uses it and through which symbols, what it uses, what is inside, and each file's place (`entry` = used from outside) |

Both take `--top N` (rows per list, 30 by default; what is cut is counted), `--by-dir`
(group by folder even when archives are known), `--json` and `--root DIR`. Each symbol
comes with the file and line that define it.

## What is counted

- A use is a (symbol, file using it) pair, not a line: ten calls from one file count once.
- Only functions and variables defined in source files, used from source files. Headers
  are mostly declarations; counting them only shows that everything includes the public ones.
- Nothing is guessed. A name defined in several files, that the using file cannot be
  told to mean, is left out. So is a name whose only definitions are `static` in other
  files, which C does not let it reach. The answer always says how many were left out,
  and how many paths `map.exclude` took out.
- A definition inside an `#if` counts even though a build may leave it out; the answer
  says how many there are.

Modules are the top folders, with the heaviest split so that a big folder is shown by
its parts; a folder that gave up a part stands for what remains. Under `psect map`,
once `psect deps` has read the build, a module is an archive, and files the build does
not use are left out, which also settles most names defined for several platforms.

## What it cannot see

It reads the shape of the code, without a compiler:

- A call through a function pointer has no name to match.
- `#if` is not evaluated, except `#if 0`: both branches count, so a cycle may join
  code that is never built together.
- A function or variable whose name a macro makes is not found (counted as "named by a macro").
- A call made inside a header's macro is seen as the macro's name, not the function's.
- A local variable with the same name as another file's global counts as a use of it.

Measured on OpenSSL 1.1.1q against a map made from a GNU Global index of the same
tree, leaving out test folders: it found 97% of that map's uses, and 97% of what it
found was in that map. Of the 284 uses only that map had, 197 were one name, `a`, matched
by name alone. Of the 358 only this tool found, reading the source showed 350 in use (230
calls, 120 as a name) and 8 only as a struct member or on a preprocessor line.

## Settings (`printsect.yaml`, all optional)

```yaml
map:
  exclude: ["third_party/*", "tests/*"]    # patterns over paths from the project root
  extensions: [.c, .cc, .cpp, .cxx]
  encodings: [utf-8, cp932]
```

Folders starting with `.` are skipped, links are not followed, and a file over 4 MB
(usually a generated table) or a declaration over 64 KB is counted and left out.

Speed, with files the OS has already read once: the 948 source files of OpenSSL 1.1.1q
took 1.6 seconds the first time and 0.3 after; about a million lines (8,000 files) took 11
seconds the first time and 3 after. Only changed files are read again. Files not read since
they were written take longer on Windows, while virus scanning looks at each one: OpenSSL
took 7 seconds right after being copied.

## A different index

The index is a plugin of the role `indexer`. A package can add one through the entry
point group `printsect.indexer`; it needs `__init__(settings)` and
`index(project, log) -> Index`. Pick it with `map: {type: <name>}`. A class attribute
`SETTINGS` (key -> type, as in `index.py`) makes printsect check its keys.

## Writes

| File | Use |
|---|---|
| `.printsect/map.json` | What each file defines and uses, so only changed files are read again |

## Inside

| File | Holds |
|---|---|
| `cli.py` | The commands; `configure()` lets `psect` mount them and add archives and probe marks |
| `scanner.py` | One file's definitions and the names it uses |
| `index.py` | The builtin indexer: walking the project, and the saved index |
| `model.py` | From the index to modules, roles, and the two answers |
| `cycles.py` | Finding the loops among modules |
