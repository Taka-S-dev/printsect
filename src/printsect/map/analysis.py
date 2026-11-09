"""What can be worked out from the map: reach, design problems, changes, a first summary.

All of it comes from the names the map matched, so every answer inherits its limits. A
count here is how many (symbol, file) pairs were seen, not how much a thing matters.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Callable
from fnmatch import fnmatchcase
from typing import Any

from .model import NOT_SEEN, Map, _capped

SNAPSHOT_VERSION = 1
SHOWN = 5
MUTUAL_MIN = 5       # uses each way before two modules count as tangled
WIDE_SHARE = 0.25    # of the modules, before one counts as used by or using "many"
WIDE_MIN = 5
GROWTH = 1.5         # uses, against the last snapshot, before a module counts as grown
GROWTH_MIN = 5
REACH_MAX = 2000     # modules above which the widest reach is not worked out


def _links(code_map: Map) -> tuple[dict[str, list[tuple[str, list[str]]]],
                                   dict[str, list[tuple[str, list[str]]]]]:
    users, uses = defaultdict(list), defaultdict(list)
    for (user, definer), symbols in sorted(code_map.t.edges.items()):
        users[definer].append((user, symbols))
        uses[user].append((definer, symbols))
    return users, uses


def impact(code_map: Map, files: list[str], symbol: str | None, depth: int, up: bool,
           top: int) -> dict[str, Any]:
    """The files reached from `files`, nearest first. Down the map by default (who a
    change can reach), up it with `up` (what these files rest on)."""
    users, uses = _links(code_map)
    table = uses if up else users
    reached: dict[str, tuple[int, list[str]]] = {}
    queue: deque[str] = deque()
    for file in files:
        for other, symbols in table.get(file, []):
            names = symbols if symbol is None or up else [s for s in symbols if s == symbol]
            if names and other not in files and other not in reached:
                reached[other] = (1, names)
                queue.append(other)
    while queue:
        file = queue.popleft()
        level = reached[file][0]
        if depth and level >= depth:
            continue
        for other, symbols in table.get(file, []):
            if other not in files and other not in reached:
                reached[other] = (level + 1, symbols)
                queue.append(other)
    rows = [{"file": f, "module": code_map._module_of(f), "depth": d, "via": names[:SHOWN]}
            for f, (d, names) in reached.items()]
    rows.sort(key=lambda r: (r["depth"], r["module"], r["file"]))
    return {"from": files, "symbol": symbol, "direction": "up" if up else "down", "depth": depth,
            "modules": dict(Counter(r["module"] for r in rows).most_common()),
            "files": _capped(rows, top), "not_seen": "calls through a function pointer or "
            "inside a header's macro, so this can be too short"}


def _chain(table: dict[str, list[tuple[str, list[str]]]], starts: list[str],
           ends: set[str]) -> list[tuple[str, str, list[str]]] | None:
    """The shortest run of links from a start to an end; None when there is none."""
    came: dict[str, tuple[str, list[str]] | None] = dict.fromkeys(starts)
    queue = deque(starts)
    while queue:
        file = queue.popleft()
        if file in ends:
            steps = []
            while (step := came[file]) is not None:
                steps.append((step[0], file, step[1]))
                file = step[0]
            return steps[::-1]
        for other, symbols in table.get(file, []):
            if other not in came:
                came[other] = (file, symbols)
                queue.append(other)
    return None


def path(code_map: Map, starts: list[str], ends: list[str]) -> dict[str, Any]:
    """How the first set of files comes to use the second, shortest way first."""
    _, uses = _links(code_map)
    steps = _chain(uses, starts, set(ends))
    back = _chain(uses, ends, set(starts)) if steps is None else None
    return {"found": steps is not None,
            "steps": [{"from": a, "from_module": code_map._module_of(a), "to": b,
                       "to_module": code_map._module_of(b), "via": names[:SHOWN],
                       "more_via": max(0, len(names) - SHOWN)} for a, b, names in steps or []],
            "other_way_steps": None if back is None else len(back), "not_seen": NOT_SEEN}


def unresolved(code_map: Map, top: int) -> dict[str, Any]:
    """Every name left off the map and why, and the files it cannot read for certain."""
    definers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    statics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    entries = code_map.t.entries
    for file in code_map.t.files:
        for symbol, _kind, static, line in entries[file].defs:
            (statics if static else definers)[symbol].append({"file": file, "line": line})
    users: dict[str, list[str]] = defaultdict(list)
    for file in code_map.t.files:
        for symbol in entries[file].refs:
            if len(definers.get(symbol, ())) > 1 or (symbol not in definers and symbol in statics):
                users[symbol].append(file)

    def row(name: str, where: list[dict[str, Any]]) -> dict[str, Any]:
        return {"name": name, "defined": where[:SHOWN], "more_defined": max(0, len(where) - SHOWN),
                "users": len(users[name]), "used_by": users[name][:SHOWN]}

    def ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: (-r["users"], r["name"]))

    files: list[dict[str, Any]] = [
             {"file": f, "macro_made": e.macro_defs, "unbalanced": e.unbalanced,
              "too_long": e.too_long, "inside_if": e.inside_if}
             for f in code_map.t.files
             if (e := entries[f]).macro_defs or e.unbalanced or e.too_long or e.inside_if]
    files.sort(key=lambda r: (-(r["macro_made"] + r["too_long"] + r["inside_if"]), r["file"]))
    return {"same_name": _capped(ordered([row(n, w) for n, w in definers.items() if len(w) > 1]),
                                 top),
            "same_name_why": "defined in several files, and a file using it cannot be told "
                             "to mean one of them",
            "static_only": _capped(ordered([row(n, statics[n]) for n in users
                                            if n not in definers]), top),
            "static_only_why": "only static definitions, which C does not let another file reach",
            "files": _capped(files, top),
            "files_why": "macro_made: bodies whose name a macro makes; inside_if: definitions "
                         "under an #if; unbalanced: braces did not close, so what follows the "
                         "fault is not seen; too_long: declarations not read",
            "not_seen": NOT_SEEN}


def _between(code_map: Map) -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for (user, definer), symbols in code_map.t.edges.items():
        a, b = code_map._module_of(user), code_map._module_of(definer)
        if a != b:
            found[(a, b)] += len(symbols)
    return found


def layer_of(layers: list[str]) -> Callable[[str], int | None]:
    """A file's layer, top first, by the patterns of map.layers."""
    def layer(file: str) -> int | None:
        for i, pattern in enumerate(layers):
            if fnmatchcase(file, pattern):
                return i
        return None

    return layer


def issues(code_map: Map, layers: list[str], top: int,
           rank: Callable[[str], int | None] | None = None) -> dict[str, Any]:
    """Where the design looks strained. These are places to look, not verdicts. `rank`
    says a file's layer when its name is no longer the one the patterns were written for."""
    between = _between(code_map)
    used_by: dict[str, set[str]] = defaultdict(set)
    uses: dict[str, set[str]] = defaultdict(set)
    for a, b in between:
        uses[a].add(b)
        used_by[b].add(a)
    modules = set(code_map.owner.values())
    many = max(WIDE_MIN, int(len(modules) * WIDE_SHARE))
    mutual: list[dict[str, Any]] = [{"a": a, "b": b, "a_uses_b": n, "b_uses_a": between[(b, a)]}
              for (a, b), n in between.items()
              if a < b and min(n, between.get((b, a), 0)) >= MUTUAL_MIN]
    mutual.sort(key=lambda r: -min(r["a_uses_b"], r["b_uses_a"]))
    shared: list[dict[str, Any]] = [{"module": m, "used_by": len(s)}
                                    for m, s in used_by.items() if len(s) >= many]
    shared.sort(key=lambda r: (-r["used_by"], r["module"]))
    sprawling: list[dict[str, Any]] = [{"module": m, "uses": len(s)}
                                       for m, s in uses.items() if len(s) >= many]
    sprawling.sort(key=lambda r: (-r["uses"], r["module"]))

    layer = rank or layer_of(layers)
    backwards: list[dict[str, Any]] = []
    if layers:
        for (user, definer), symbols in sorted(code_map.t.edges.items()):
            low, high = layer(user), layer(definer)
            if low is not None and high is not None and low > high:
                backwards.append({"user": user, "used": definer, "symbols": symbols[:SHOWN]})
    thin = [{"from": a, "to": b} for (a, b), n in sorted(between.items()) if n == 1]
    overview = code_map.overview(top)
    return {"cycles": overview["cycles"], "mutual": _capped(mutual, top),
            "shared": _capped(shared, top), "sprawling": _capped(sprawling, top),
            "against_layers": _capped(backwards, top), "layers_set": bool(layers),
            "single_use_links": _capped(thin, top), "many_means": many,
            "omitted": overview["omitted"], "inside_if": overview["inside_if"]}


def snapshot(code_map: Map) -> dict[str, Any]:
    everything = 1 << 30
    view = code_map.overview(everything)
    return {"version": SNAPSHOT_VERSION, "files": code_map.t.files,
            "edges": [[e["from"], e["to"], e["count"]] for e in view["edges"]["shown"]],
            "cycles": [c["modules"]["shown"] for c in view["cycles"]["shown"]],
            "modules": {m["name"]: m["in"] + m["out"] for m in view["modules"]["shown"]},
            "unresolved": view["omitted"]["same_name_refs"] + view["omitted"]["static_refs"]}


def _valid(old: Any) -> bool:
    """A snapshot can arrive with the project: its shape is checked before it is used."""
    def names(items: Any) -> bool:
        return (isinstance(items, list)
                and all(isinstance(x, str) and x.isprintable() for x in items))

    return (isinstance(old, dict) and old.get("version") == SNAPSHOT_VERSION
            and names(old.get("files")) and isinstance(old.get("unresolved"), int)
            and isinstance(old.get("edges"), list) and all(
                isinstance(e, list) and len(e) == 3 and names(e[:2]) and isinstance(e[2], int)
                for e in old["edges"])
            and isinstance(old.get("cycles"), list) and all(names(c) for c in old["cycles"])
            and isinstance(old.get("modules"), dict) and names(list(old["modules"]))
            and all(isinstance(v, int) for v in old["modules"].values()))


def difference(old: dict[str, Any], new: dict[str, Any], top: int) -> dict[str, Any]:
    before = {(a, b): n for a, b, n in old["edges"]}
    after = {(a, b): n for a, b, n in new["edges"]}
    added = [{"from": a, "to": b, "count": n} for (a, b), n in sorted(after.items())
             if (a, b) not in before]
    gone = [{"from": a, "to": b, "count": n} for (a, b), n in sorted(before.items())
            if (a, b) not in after]
    grown = [{"module": m, "was": old["modules"].get(m, 0), "now": n}
             for m, n in sorted(new["modules"].items())
             if n - old["modules"].get(m, 0) >= GROWTH_MIN
             and n >= old["modules"].get(m, 0) * GROWTH]
    seen = {tuple(c) for c in old["cycles"]}
    return {"new_links": _capped(added, top), "gone_links": _capped(gone, top),
            "grown": _capped(grown, top),
            "new_cycles": _capped([c for c in new["cycles"] if tuple(c) not in seen], top),
            "unresolved": {"was": old["unresolved"], "now": new["unresolved"]},
            "files_added": _capped(sorted(set(new["files"]) - set(old["files"])), top),
            "files_removed": _capped(sorted(set(old["files"]) - set(new["files"])), top)}


def _reach(between: Counter[tuple[str, str]]) -> dict[str, int]:
    """For each module, how many others use it, directly or through others."""
    users: dict[str, set[str]] = defaultdict(set)
    for a, b in between:
        users[b].add(a)
    if len(users) > REACH_MAX:
        return {}
    found = {}
    for module in users:
        seen = {module}
        stack = [module]
        while stack:
            for other in users.get(stack.pop(), ()):
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        found[module] = len(seen) - 1
    return found


def summary(code_map: Map, classes: dict[str, str], near: list[str] | None,
            top: int) -> dict[str, Any]:
    """What to read first. Counts stand beside what they cover, so a big number is not
    taken for an important one."""
    view = code_map.overview(1 << 30)
    rows = view["modules"]["shown"]

    def best(key: str, keep: Any = None) -> list[dict[str, Any]]:
        chosen = [r for r in rows if r[key] and (keep is None or keep(r))]
        chosen.sort(key=lambda r: (-r[key], r["name"]))
        return [{"module": r["name"], key: r[key], "role": r["role"]} for r in chosen[:SHOWN]]

    reach = _reach(_between(code_map))
    unsure: Counter[str] = Counter()
    for file, n in code_map.t.unsure_by_file.items():
        unsure[code_map._module_of(file)] += n
    outside: Counter[str] = Counter()
    for (user, definer), symbols in code_map.t.edges.items():
        kind = classes.get(definer, "product")
        if classes.get(user, "product") == "product" and kind != "product":
            outside[kind] += len(symbols)
    counted = Counter(classes.get(f, "product") for f in code_map.t.files)
    result: dict[str, Any] = {
        "scope": {"files": view["files"], "modules": view["modules"]["total"],
                  "grouped_by": view["grouped_by"]},
        "central": best("in", lambda r: r["role"] == "core") or best("in"),
        "entrances": best("out", lambda r: r["role"] == "start"),
        "depends_most": best("uses"),
        "widest_reach": [{"module": m, "reach": n} for m, n in
                         sorted(reach.items(), key=lambda kv: (-kv[1], kv[0]))[:SHOWN]],
        "most_unresolved": [{"module": m, "uses": n} for m, n in
                            sorted(unsure.items(), key=lambda kv: (-kv[1], kv[0]))[:SHOWN]],
        "classes": dict(counted.most_common()),
        "product_uses_of_others": dict(outside.most_common()),
        "cycles": view["cycles"]["total"],
        "omitted": view["omitted"], "inside_if": view["inside_if"],
        "note": "counts are (symbol, using file) pairs: a measure of contact, not of importance",
    }
    if near:
        result["way_in"] = way_in(code_map, near)
    return result


def way_in(code_map: Map, targets: list[str]) -> list[str]:
    """The shortest chain of uses from a file nothing else uses down to a target."""
    users, _ = _links(code_map)
    came: dict[str, str | None] = dict.fromkeys(targets)
    queue = deque(targets)
    while queue:
        file = queue.popleft()
        above = users.get(file, [])
        if not above:
            chain = [file]
            while (step := came[chain[-1]]) is not None:
                chain.append(step)
            return chain
        for other, _symbols in above:
            if other not in came:
                came[other] = file
                queue.append(other)
    return []
