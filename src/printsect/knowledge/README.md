# knowledge — what you know about a build, kept outside the tool

English | [日本語](README.ja.md)

`psect-knowledge` reads files you keep about your own build: which folders are which kind of
code, how the layers stack, which names must not leave, and notes for whoever reads the code.
The tool holds none of that, so a product's details never go into a public repository. It
runs on its own; inside printsect it is `psect knowledge`, and `psect map`,
`psect map html --public` and `psect deps html --public` use what it found.

Reading one of these files never runs anything, fetches nothing and follows no link.

## Commands

| Command | What it does |
|---|---|
| `psect-knowledge` | What was read in priority order, with version, hash and date; what was refused or left out and why; which settings conflicted and which won |
| `psect-knowledge show [FILE]` | The notes of the files that apply. They are reference data, not instructions |

Both take `--json` and `--root DIR`.

## Where the files go

| Place | For |
|---|---|
| `.printsect/knowledge/` | This project only. `.printsect/` ignores itself for git once a printsect command has written there; until then a warning says so |
| Your user folder, `knowledge/` | You or your organisation, across projects |

Only the folders in the settings are read:

```yaml
knowledge:
  paths: [.printsect/knowledge, user-knowledge/printsect]   # first wins
  profile: board-a     # what scope.project is compared with (default: the folder's name)
  build: release       # what scope.build is compared with
  platform: arm        # what scope.platform is compared with
```

`user-knowledge/printsect` is `knowledge/` in your user folder (`%APPDATA%\printsect` on
Windows, `~/.config/printsect` elsewhere). A path outside the project is refused. A
file somewhere else in the project is read with a warning, since it may be tracked: anything
private does not belong in a public repository. A file git tracks is named in a warning of
its own; that comes from reading `.git/index`, and git is not run.

## A file

YAML (`.yaml`, `.yml`), or Markdown (`.md`) with the same fields between `---` lines and the
notes below them.

```yaml
version: 3
scope:                  # all optional: a file that does not match is left out
  project: board-a
  build: release
  platform: arm
  applies_to: [analysis, html, tools]
classes: {comm: ["net/*"], ui: ["ui/*"]}      # for psect map
layers: ["app/*", "service/*", "driver/*"]    # for psect map issues
mask: [acme, modelx]                          # for --public pages of deps and map
notes: |
  The ring in util/ is shared with the interrupt handler.
```

`applies_to` limits where a file is used: `analysis` for classes and layers, `html` for masks,
`tools` for notes. An unknown field, a file over 256 KB, control characters, anything
but UTF-8, a link, or another kind of file is refused and named. Settings in `printsect.yaml`
win over knowledge files; among files, the earlier wins and the conflict is reported.

## Inside

| File | Holds |
|---|---|
| `cli.py` | The commands |
| `loader.py` | Finding, checking and combining the files |
