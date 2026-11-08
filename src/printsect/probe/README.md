# probe — print probes in C source, and a list of them

English | [日本語](README.ja.md)

`psect-probe` puts debug lines (probes) into C source, remembers each one — where,
why, what it showed — and takes out exactly the ones it put in, leaving the file
as it was. It runs on its own; inside printsect it is `psect probe`.

```c
    if (len <= 0) {
        printf("[PSECT-AI 1 rx] len=%d\n", len); /* PSECT-AI:rx #1 */
```

## Commands

| Command | What it does |
|---|---|
| `psect-probe add FILE LINE` | A probe below LINE (0 = top). `-t` its code, `-g` group, `-p` purpose, `-e` what a hit or miss would mean |
| `psect-probe list [GROUP]` | The probes in the source with purpose and last result. `--quickfix` for editors, `--check` exits 1 if any is in |
| `psect-probe verify` | Check the list against the source: moved, gone, or unknown marks |
| `psect-probe clear [GROUP]` | Take out the agent's probes. `--orphans` also takes out agent marks the list does not know |
| `psect-probe hits LOG` | Count what each probe printed in a log file (`-` for stdin): hits, line numbers, and the `name=value` pairs it showed |
| `psect-probe note N TEXT` | Add to a probe's record |

Every command takes `--json` and `--root DIR`.

## How a probe is kept

- **The mark is the truth.** Every probe line ends with `/* PSECT-AI:group #n */`.
  Line numbers in the list are only hints, refreshed by `verify`, so editing the
  file around probes is fine.
- **`-AI` means printsect put it in.** Only those lines are ever taken out. Marks
  without `-AI` (`/* PSECT #3 */`) are yours and stay. A probe line edited by hand
  is left in and reported.
- **Numbers are never reused**, so `#7` in an old log still means one probe.
- **Files come back byte for byte.** The encoding (UTF-8 or CP932, with or without
  BOM), each line's ending, and whether the file ends in a newline are kept. A file
  that would not read back the same is refused, writes are atomic, and a file
  changed by someone else since it was read is not overwritten.
- **Placement is checked.** The indent follows the line above; `\` continuations in
  macros stay last; a line that is not the end of a statement gets a warning; the
  enclosing function is recorded.

## Settings (`printsect.yaml`, all optional)

```yaml
marker:
  prefix: PSECT            # the mark and the printed tag: PSECT-AI / [PSECT-AI 7 grp]
probe:
  template: 'printf("[{tag}] %s:%d\n", __FILE__, __LINE__);'   # when -t is not given
  encodings: [utf-8, cp932]
  extensions: [.c, .h, .cc, .cpp, .cxx, .hpp, .hh, .inc]
limits:
  active_probes: 20        # refuse more than this many at once
```

In a probe's code, `{tag}` becomes `PSECT-AI 7 group`, `{n}` the number and
`{where}` `file.c:61`.

## Files it writes

| File | For |
|---|---|
| `.printsect/probes.json` | The list: number, file, line hint, group, purpose, expectation, status, results, notes |
| `.printsect/PROBES.md` | The same as a table for people, rewritten on every change |

## Inside

| File | Holds |
|---|---|
| `cli.py` | The commands; `marks()` gives other tools the probes by file |
| `ops.py` | Add, verify, clear, note; the run banner; writing a run's results into the list |
| `marker.py` | Reading and writing the mark and the printed tag |
| `ledger.py` | The list and `PROBES.md` |
| `textfile.py` | Reading and writing a source file without changing other lines |
| `cscan.py` | A light look at C: enclosing function, end of statement |
| `hits.py` | Counting each probe's hits and the values it printed |
