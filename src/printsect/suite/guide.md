# printsect: how to find a bug with print probes

You narrow down a bug in C by putting print lines (probes) into the source, running
the program on the target, and reading which probes printed what. printsect keeps the
list of probes, builds, deploys, reads the console and counts the hits. You decide
where to look and what the results mean.

If `doctor` does not show every part ok, set the project up first (`guide` topic
`setup`).

## The loop

1. **Start from the symptom.** Ask what the person saw, and how to make it happen
   again. Read the code around it before touching anything.
2. **Say a hypothesis**, such as "rx() is called with len 0". Each probe tests one.
3. **Put probes in** with `probe_add`: always give `purpose` (what it is to find out)
   and `expect` (what a hit or a miss would mean). Print the values that decide the
   hypothesis as `name=value`: `printf("[{tag}] len=%d state=%d\n", len, st);`.
   A few probes at a time; a probe at the start of a branch says whether it runs.
4. **Check what gets rebuilt** with `deps_which` when the file is a header or shared.
5. **Run** with `run`. Read `hits` and `not_reached` in the answer:
   - a probe with hits shows its values: confirm or drop the hypothesis;
   - a probe not reached is a finding too: that code did not run;
   - `banner_seen: false` means the output may be from an old build: do not trust it;
   - `missed` lines mean some output was lost: counts may be low.
6. **Write what you learned** on the probe with `probe_note` ("len is 0 on every
   call after reconnect"), then narrow: move probes towards the cause, halving the
   space each time, and run again.
7. **Stop when the cause is shown by output**, not guessed. Tell the person the cause,
   the evidence (probe numbers, values, run numbers) and a fix to consider.
8. **Clean up:** `probe_clear`, then `probe_list` must show none of yours.

## Rules

- Never edit or delete probe lines yourself; `probe_clear` takes out exactly the
  agent's lines and leaves the file as it was. Lines marked without `-AI` are the
  person's: leave them.
- A failed build in `run` is usually a probe's code: fix it with `probe_clear` for that
  group and `probe_add` again, then run again.
- **Status `waiting_user` (exit 3)**: a person has to do something (load the build
  with their tool, unplug a cable, ...). Tell them exactly what the message says, wait
  for them to say it is done, then call `run` with `resume: true`. Never guess that it
  was done.
- Do not send commands that reset, erase or reflash the target without asking.
- Keep a handful of probes in at a time (the configured limit stops you anyway); take
  out the ones that answered their question.
- Use `console_read` with `since` set to the `last` number you saw, so you read only
  new lines.
