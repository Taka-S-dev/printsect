# printsect: setting up a project

The goal is `doctor` showing every part ok. You write the settings with your own file
editing; printsect checks them where they are (`check`, `doctor`). Nothing about the
project has to leave this PC. Every key, with examples: `guide` topic `settings`.

Two files, both at the project root:

- `printsect.yaml` — shared with everyone on the project: deps, build, run, deploy,
  reproduce, stop, devices.
- `printsect.local.yaml` — this PC only, not committed: the console (ports, programs).

printsect keeps its own state in `.printsect/` (the dependency graph `deps.json` and
`DEPS.md`, the probe list `probes.json` and `PROBES.md`, one folder per run). It
ignores itself for git; leave it alone.

Tool names here are the MCP tools. On the command line each is a `psect` command:
`deps_find` is `psect deps find`, `deps_which` is `psect deps which`, `build_plan` is
`psect build --plan`, `probe_add` is `psect probe add`, and so on.

## Steps

1. **`init`** writes both files with every section commented, in this order. It
   never overwrites a file.
2. **deps — how the build is read.** Find how people build: the README, the
   Makefiles, build scripts. If they run plain `make` in the project root, the
   default `deps.command` (`make -n -B -w`, in `.`) is already right. Otherwise set it
   to their make call with `-n -B -w` added (keep its `-f`, `-C`, targets and
   variables) and `deps.cwd` to where it runs. `doctor` then counts outputs, archives
   and sources: 0 sources means the wrong folder or target. To be sure, `deps_find` a
   source file you know is built: it must show the output it goes into. Reading the
   build runs make, and make runs `$(shell ...)` in Makefiles even with `-n`, so for you
   it waits until the person has run `psect approve`; ask them once deps is set.
3. **run.banner — where a new build shows itself.** Find code every new build runs
   once when it starts: the module's init or entry function (what the loader or the
   startup calls). Confirm with `deps_which` that its file is built into the outputs
   that get deployed. Pick a line inside it that ends a statement, early, before
   anything that can fail. `doctor` checks the line.
   - **No such function in sight** (the entry may be named in a linker script, a
     loader table or the person's head): ask the person which function runs at load,
     and leave `run.banner` unset until they say.
   - **Several outputs deployed together:** there is one banner per run. Put it in
     code that every deployed output runs at start (shared code), or, if none is
     shared, in the output the person loads last, and tell them only that one is
     proven new.
4. **build — how outputs are made.** Find the commands people use, and any clean
   they do first. Then:
   - If making an output already remakes what it needs (make usually does), one rule
     for the outputs is enough: `for: outs`, `run: make {path}`. Add `for: archives`
     rules only when archives are built by separate commands.
   - `{path}` is the output as the build names it (`build/out/netdrv.out`); `{name}` is
     only its file name, which make knows only when it runs in that folder. When
     unsure, try the command by hand with `make -n` first: the target must exist.
   - A step without `for` runs once where it stands: put a clean there if they clean.
   - Leave `artifacts: auto`.
   - Show the plan before any probe is in with `build_plan` and `all: true` (as if
     every source held a probe). Have the person agree to those commands before the
     first build: they run for real.
   - Commands you write into the settings (build, deploy, a non-stock `deps.command`,
     a command console) run for you only after the person approves them by running
     `psect approve` themselves; you cannot. Until then `build_plan`, `run`, the deps
     and console tools refuse and name the commands, and `doctor` names them too.
5. **deploy — how a build gets onto the target.** Ask. Their own tool by hand:
   `type: manual` with a `message` saying what to load. A command here:
   `type: command` with its `commands`. A target with an FTP server: `type: ftp`
   with `host`, `user`, `path` and the loading commands as `after`; ask the person to
   set the password in the variable `password_env` names (`PSECT_FTP_PASSWORD`), never
   write it in a settings file. A program run on this PC: `type: none`.
   `doctor` warns until deploy is set.
6. **console — how printsect reads the target.** Ask which program shows the console
   and how it is reached (its TCP port, its command line, its log file, or the serial
   port itself when nothing else holds it). Write it in `printsect.local.yaml`.
   `doctor` reaches it and shows the last line number.
7. **run.ready, reproduce, stop.** Ask what the target prints when it can take
   commands (the prompt), how the problem is made to happen, and what output means it
   is over (an exception text, a quiet time).
8. **devices — only when targets differ.** Ask what differs (where outputs are kept,
   how they are loaded, which port). Name each device with only those settings.
   Choose one with `use` only when the person said which.
9. **Prove it end to end:** with `doctor` all ok, put one probe in the banner's
   function (it is sure to run), `run`, and see it hit. Then take it out.

## Rules

- **Ask, never guess:** ports, programs, how to deploy, which device, which function
  runs at load when it is not plain to see, and any command that resets, erases,
  flashes or restarts the target. A guessed port or loader command can do damage
  that a question would not.
- `doctor` all ok is not the end of setup by itself: every step above that says ask
  must have been answered by the person, or left unset on purpose and said so.
- Do not change the project's own build files (Makefiles, scripts) to suit printsect;
  describe the build as it is.
- Shared settings in `printsect.yaml`, this PC's in `printsect.local.yaml`.
- After each change: `check` names misspelt keys with the nearest real one; `doctor`
  says what to do next. Fix what it says, one part at a time.
- `probe_add` puts the probe below the line given; a group is an optional name for
  probes to list or take out together.
