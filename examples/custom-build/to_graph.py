#!/usr/bin/env python3
"""Turn this example's own build description into the file `psect-deps --from-graph` reads.

A build make cannot describe needs only something like this, in any language: nothing about
the build goes into printsect.

    python to_graph.py build.json
    psect-deps --from-graph build.json
"""

import csv
import json
import sys


def main(out: str) -> None:
    objects, archives, outs = {}, {}, {}
    with open("targets.csv", newline="", encoding="utf-8") as handle:
        table = csv.DictReader(handle)
        for row in table:
            built, origin = [], f"targets.csv:{table.line_num}"
            for source in row["sources"].split(";"):
                name = "build/" + source.rsplit("/", 1)[-1].replace(".c", ".o")
                objects[name] = {"source": source, "origin": origin}
                built.append(name)
            if row["kind"] == "archive":
                archives[f"build/lib{row['target']}.a"] = {"objects": built, "origin": origin}
            else:
                outs[f"build/{row['target']}.out"] = {
                    "objects": built, "origin": origin,
                    "archives": [f"build/lib{x}.a" for x in row["uses"].split(";")],
                }
    graph = {
        "version": 1, "objects": objects, "archives": archives, "outs": outs,
        "made": {"modules/beta/table.c": {"from": ["modules/beta/table.def"], "by": "build.sh"}},
        "copies": {"target:/opt/app.out": "build/app.out"},
        "inputs": ["targets.csv", "build.sh"],
    }
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(graph, handle, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
