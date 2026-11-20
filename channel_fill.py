"""State and scheduling for one explicitly requested /encher run."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Iterator, TypeVar

T = TypeVar("T")


@dataclass
class FillRun:
    destination: int
    target: int = 50
    published: int = 0
    attempted: int = 0
    skipped: int = 0
    source_errors: int = 0
    phase: str = "a recolher ofertas"
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    seen: set[str] = field(default_factory=set)


def interleave(sources: dict[str, list[T]], rng=None) -> Iterator[T]:
    """Shuffle offers within each source; take one per source per shuffled round.

Avoid repeating the previous source at round boundaries whenever alternatives
remain. Empty sources drop out; remaining sources supply the rest of the batch.
Never mutate the caller's candidate lists.
"""
    rng = rng or random.SystemRandom()
    pools = {name: list(items) for name, items in sources.items() if items}
    for items in pools.values():
        rng.shuffle(items)
    previous = None
    while pools:
        order = list(pools)
        rng.shuffle(order)
        if len(order) > 1 and order[0] == previous:
            order[0], order[1] = order[1], order[0]
        for name in order:
            yield pools[name].pop()
            previous = name
            if not pools[name]:
                del pools[name]
