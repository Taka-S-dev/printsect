"""Loops among modules: the ones that use each other, directly or through others."""

from __future__ import annotations

from collections import deque
from collections.abc import Collection, Mapping, Sequence


def loops(uses: Mapping[str, Sequence[str]]) -> list[list[str]]:
    """Each group of two or more nodes that can all reach each other, names sorted.

    Tarjan's strongly connected components, without recursion: an archive-grouped map
    of a large build can be deeper than Python's stack.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def enter(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)

    for root in sorted(uses):
        if root in index:
            continue
        enter(root)
        work = [(root, iter(uses.get(root, ())))]
        while work:
            node, following = work[-1]
            for other in following:
                if other not in index:
                    enter(other)
                    work.append((other, iter(uses.get(other, ()))))
                    break
                if other in on_stack:
                    low[node] = min(low[node], index[other])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    group: list[str] = []
                    while not group or group[-1] != node:
                        group.append(stack.pop())
                        on_stack.discard(group[-1])
                    if len(group) > 1:
                        found.append(sorted(group))
    return found


def round_trip(start: str, uses: Mapping[str, Sequence[str]],
               group: Collection[str]) -> list[str]:
    """One shortest way from `start` back to itself inside `group`: [start, a, b] for
    start -> a -> b -> start. Among equally short ones, `uses` order decides."""
    came_from: dict[str, str] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for other in uses.get(node, ()):
            if other == start:
                path = [node]
                while path[-1] != start:
                    path.append(came_from[path[-1]])
                return path[::-1]
            if other in group and other not in came_from:
                came_from[other] = node
                queue.append(other)
    return [start]
