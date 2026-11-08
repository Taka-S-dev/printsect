"""Time each `psect deps` command on a project made by make_big_project.py.

    python benchmarks/deps/time_deps.py PROJECT_DIR
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
state = root / ".printsect"
runs = [
    ["deps", "--from-log", str(root / "make.log")],
    ["deps", "tree", "--ascii"],
    ["deps", "tree", "-f", "s0042", "--ascii"],
    ["deps", "which", "inc/h0003.h", "--ascii"],
    ["deps", "find", "*.h"],
    ["deps", "html", "--no-open"],
]
for args in runs:
    start = time.perf_counter()
    done = subprocess.run([sys.executable, "-m", "printsect", *args, "--root", str(root)],
                          capture_output=True, text=True, encoding="utf-8")
    took = time.perf_counter() - start
    lines = len(done.stdout.splitlines())
    status = "ok" if done.returncode == 0 else f"exit {done.returncode}: {done.stderr[-200:]}"
    print(f"{took:6.2f}s  {lines:6d} lines  psect {' '.join(args[:3])}  {status}")
for name in ("deps.json", "DEPS.md", "deps.html"):
    path = state / name
    if path.exists():
        print(f"{path.stat().st_size / 1e6:8.1f} MB  {name}")
