# core — what every printsect tool shares

English | [日本語](README.ja.md)

Not a tool: the ground the tools stand on.

## The rule

- A tool (`deps`, `probe`, `deploy`, …) imports from `core` and from itself, never
  from another tool. That is what lets each one run alone.
- `core` imports no tool.
- Joining tools up (probe marks in the deps tree, warnings that need two tools, the
  `psect run` loop) happens only in `printsect/cli.py`, the `psect` command, and
  `printsect/suite/`, which may use every tool and is used by none. A tool offers
  hooks for it — `marks=` in `deps.cli.configure`, `after_add=` in
  `probe.cli.configure`, `wait_for=` in `deploy.cli.configure`.

`tests/test_layout.py` checks all of this, including that starting one tool loads
no other.

## Settings are checked

A tool declares the sections of `printsect.yaml` it reads as `SECTIONS` in its
`__init__.py` (key -> type, or a nested mapping) and passes them with
`parser.set_defaults(sections=SECTIONS)`. `core.cli.project()` then stops on an
unknown or mistyped key in those sections and in printsect's own (`CORE_SCHEMA`),
naming the nearest real key. Other tools' sections are left alone, so each tool runs
in a project set up for all of them. A section whose keys depend on a plugin
(`deps`, `deploy`) declares only `type`; the plugin's `SETTINGS` covers the rest.
`psect check` checks everything at once.

## Adding a tool

1. A folder `printsect/<tool>/` with `cli.py` holding `configure(...)`,
   `build_parser()` and `main()`, a `__main__.py`, `SECTIONS` in `__init__.py`, and
   a `README.md` in this shape with its `README.ja.md`.
2. A command `psect-<tool>` in `pyproject.toml`, and a line in `TOOLS`, `cmd_check` and
   `build_parser()` in `printsect/cli.py`.
3. Its tests in `tests/<tool>/`.

## Inside

| File | Holds |
|---|---|
| `cli.py` | `--json` / `--root`, JSON answers, exit codes (0 ok, 1 failed, 2 config, 3 waiting for a person) |
| `config.py` | Finding the project root; `printsect.yaml` + `printsect.local.yaml`; defaults; checking settings |
| `init_config.yaml`, `init_local.yaml` | What `psect init` writes: every section, commented, with made-up examples |
| `plugins.py` | Roles and their implementations: builtin or from entry points `printsect.<role>` |
| `errors.py` | `PsectError`, `ConfigError` |
| `fsutil.py` | Atomic writes; the `.printsect/` folder that ignores itself for git |
| `term.py` | Colour on or off, width of text on screen |
| `timeutil.py` | Time stamps |
