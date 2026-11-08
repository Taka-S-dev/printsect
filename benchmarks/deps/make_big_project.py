"""Make a large made-up C project for measuring `psect deps` at scale.

    python benchmarks/deps/make_big_project.py OUT_DIR [--sources 8000] [--headers 3000]

Writes the sources and headers (each source about 125 lines, so 8000 sources is about
a million lines) and `make.log`, which is what `make -n -B -w` would print for it:
one archive per module, outputs built from several archives. Then:

    psect deps --from-log OUT_DIR/make.log --root OUT_DIR
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

BODY = "".join(
    f"int f{i}(int x)\n{{\n    if (x > {i}) {{\n        return x - {i};\n    }}\n"
    "    return x;\n}\n\n"
    for i in range(15)
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--sources", type=int, default=8000)
    ap.add_argument("--headers", type=int, default=3000)
    ap.add_argument("--modules", type=int, default=60)
    ap.add_argument("--outs", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    root = Path(a.out).resolve()
    mods = [f"mod{m:02d}" for m in range(a.modules)]

    # Headers: a shared set in inc/, the rest spread over the modules.
    shared = [f"inc/h{i:04d}.h" for i in range(a.headers // 5)]
    local = {m: [] for m in mods}
    for i in range(a.headers - len(shared)):
        m = mods[i % len(mods)]
        local[m].append(f"{m}/h{i:04d}.h")
    every = shared + [h for hs in local.values() for h in hs]
    for i, h in enumerate(every):
        # Each header includes a few earlier shared ones, so closures are realistic.
        pool = shared[: max(1, min(i, len(shared)))]
        inc = rnd.sample(pool, k=min(3, len(pool)))
        text = "".join(f'#include "{Path(x).name}"\n' for x in inc if x != h)
        path = root / h
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#pragma once\n{text}int g_{i};\n", encoding="utf-8")

    log = [f"make: Entering directory '{root.as_posix()}'"]
    per_mod = a.sources // len(mods)
    for m in mods:
        log.append(f"make -C {m}")
        log.append(f"make[1]: Entering directory '{(root / m).as_posix()}'")
        objs = []
        for s in range(per_mod):
            src = root / m / f"s{s:04d}.c"
            picks = rnd.sample(local[m], k=min(8, len(local[m])))
            picks += rnd.sample(shared, k=6)
            inc = "".join(f'#include "{Path(x).name}"\n' for x in picks)
            src.write_text(inc + "\n" + BODY, encoding="utf-8")
            obj = f"../build/obj/{m}/s{s:04d}.o"
            objs.append(obj)
            log.append(f"xcc -O2 -I../inc -I. -DMOD=\\\"{m}\\\" -c s{s:04d}.c -o {obj}")
        log.append(f"xar crus ../build/lib/lib{m}.a " + " ".join(objs))
        log.append(f"make[1]: Leaving directory '{(root / m).as_posix()}'")
    for o in range(a.outs):
        use = rnd.sample(mods, k=8)
        libs = " ".join(f"-l{m}" for m in use)
        log.append(f"xld -r -o build/out/app{o:02d}.out -Lbuild/lib {libs}")
    log.append(f"make: Leaving directory '{root.as_posix()}'")
    (root / "make.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    (root / ".printsect").mkdir(exist_ok=True)
    lines = per_mod * len(mods) * (BODY.count("\n") + 15)
    print(f"{per_mod * len(mods)} sources, {len(every)} headers, about {lines:,} lines -> {root}")


if __name__ == "__main__":
    main()
