"""The data of the page that shows the map to a person, and the page itself.

The numbers on its summary and problem tabs come from the same functions as the commands,
worked out here for each way of grouping, so the page and `psect map` agree. Only the
links are kept file by file, so the page can filter them by class and regroup them.
The page holds paths relative to the project unless links to an editor were asked for.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from functools import cache
from importlib.resources import files as package_files
from typing import Any

from ..core.cli import script_json
from ..core.masking import Masker
from .analysis import issues, layer_of, summary, unresolved
from .index import FileEntry
from .model import DEFAULT_CLASSES, PRODUCT, Map, Tables

EVERYTHING = 1 << 30
DIRECTORY = "directory"


def first_uses(indexer: Any, project: Any, tables: Tables) -> dict[tuple[str, str], int]:
    """The first line of each use on the map, when the indexer can say."""
    reader = getattr(indexer, "first_lines", None)
    if reader is None:
        return {}
    wanted: dict[str, set[str]] = {}
    for (user, _definer), symbols in tables.edges.items():
        wanted.setdefault(user, set()).update(symbols)
    found: dict[tuple[str, str], int] = {}
    for file, names in wanted.items():
        for name, line in reader(project, file, names).items():
            found[(file, name)] = line
    return found


def masked(masker: Masker, tables: Tables, groupings: dict[str, dict[str, list[str]]],
           classes: dict[str, str], layers: list[str], lines: dict[tuple[str, str], int],
           marks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """What a page is made from, with the names taken out before anything is worked out
    from it: whatever the page then derives has no way to hold a real name."""
    file, symbol = masker.file, masker.symbol

    def group(name: str) -> str:
        return name if name in DEFAULT_CLASSES or name == PRODUCT else masker.text(name)

    entries = {file(f): FileEntry(
        defs=[(symbol(n), k, st, line) for n, k, st, line in e.defs],
        refs=[symbol(r) for r in e.refs], macro_defs=e.macro_defs, unbalanced=e.unbalanced,
        too_long=e.too_long, inside_if=e.inside_if) for f, e in tables.entries.items()}
    done = Tables(
        edges={(file(a), file(b)): [symbol(s) for s in names]
               for (a, b), names in tables.edges.items()},
        lines={(file(f), symbol(s)): n for (f, s), n in tables.lines.items()},
        files=sorted(file(f) for f in tables.files), omitted=tables.omitted,
        inside_if=tables.inside_if, entries=entries, confirmed=tables.confirmed,
        hidden={group(c): n for c, n in tables.hidden.items()})
    for f, n in tables.unsure_by_file.items():
        done.unsure_by_file[file(f)] = n
    rank = layer_of(layers)
    ranks = {file(f): rank(f) for f in tables.files}
    return {"tables": done, "rank": ranks.get,
            # Each output by itself: a path can hold the character they are joined with.
            "groupings": {key: {file(f): "+".join(masker.module(m) for m in modules)
                                for f, modules in owner.items()}
                          for key, owner in groupings.items()},
            "classes": {file(f): group(c) for f, c in classes.items()},
            "lines": {(file(f), symbol(s)): n for (f, s), n in lines.items()},
            "marks": {file(f): [{"n": m.get("n")} for m in found] for f, found in marks.items()}}


def project_text(data: dict[str, Any]) -> str:
    """The names on a page that came from the project, for the check of a public copy.
    Everything else on it is worked out from these, or is the page's own vocabulary."""
    names = [data["name"], *data["files"], *data["symbols"], *data["classes"]["names"]]
    for grouped in data["groupings"].values():
        names += grouped["names"]
    return "\n".join(names)


def page_data(*, name: str, generated: str, tables: Tables, groupings: dict[str, Any],
              default: str, classes: dict[str, str], layers: list[str], limits: list[str],
              lines: dict[tuple[str, str], int], marks: dict[str, list[dict[str, Any]]],
              changes: dict[str, Any], root: str, public: str = "",
              rank: Callable[[str], int | None] | None = None) -> dict[str, Any]:
    files = tables.files
    number = {f: i for i, f in enumerate(files)}
    symbols: dict[str, int] = {}

    def symbol(text: str) -> int:
        return symbols.setdefault(text, len(symbols))

    defs = [[number[f], symbol(n), kind, int(static), line]
            for f in files for n, kind, static, line in tables.entries[f].defs]
    edges = [[number[user], number[definer],
              [x for s in names for x in (symbol(s), lines.get((user, s), 0))]]
             for (user, definer), names in tables.edges.items()]
    class_names = sorted(set(classes.get(f, "product") for f in files))
    maps = {key: Map(tables, owner) for key, owner in groupings.items()}

    def grouped(code_map: Map) -> dict[str, Any]:
        modules = sorted({code_map._module_of(f) for f in files})
        at = {m: i for i, m in enumerate(modules)}
        return {"names": modules, "of": [at[code_map._module_of(f)] for f in files]}

    return {
        "name": name, "generated": generated, "root": root, "files": files,
        "symbols": list(symbols), "defs": defs, "edges": edges, "limits": limits,
        "default": default, "changes": changes, "public": public,
        "classes": {"names": class_names,
                    "of": [class_names.index(classes.get(f, "product")) for f in files]},
        "groupings": {key: grouped(m) for key, m in maps.items()},
        "views": {key: {"summary": summary(m, classes, None, EVERYTHING),
                        "issues": issues(m, layers, EVERYTHING, rank)}
                  for key, m in maps.items()},
        "unresolved": unresolved(maps[default], EVERYTHING),
        "probes": [[number[f], [m.get("n") for m in found]]
                   for f, found in sorted(marks.items()) if f in number],
    }


def render(data: dict[str, Any], name: str) -> str:
    return (_template().replace("__TITLE__", html.escape(f"{name} - code map"))
            .replace("__DATA__", script_json(data)))  # last: nothing in it can act as a marker


@cache
def _template() -> str:
    return package_files(__package__).joinpath("viewer.html").read_text(encoding="utf-8")


__all__ = ["DIRECTORY", "first_uses", "masked", "page_data", "project_text", "render"]
