"""Byte-bounded, thread-safe LRU cache for decoded bands and statistics."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Hashable, Optional


def _nbytes(value: Any) -> int:
    nb = getattr(value, "nbytes", None)
    if isinstance(nb, int):
        return nb
    return 1024  # small objects (dicts of stats): nominal charge


class ArrayCache:
    """LRU keyed by any hashable; evicts least-recently-used until under budget.

    A single value larger than the budget is still stored (and evicts
    everything else), so a huge band never fails to load; it simply owns the
    cache until something replaces it.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = int(max_bytes)
        self._items: "OrderedDict[Hashable, tuple[Any, int]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable) -> Optional[Any]:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return item[0]

    def put(self, key: Hashable, value: Any, nbytes: Optional[int] = None) -> None:
        size = int(nbytes) if nbytes is not None else _nbytes(value)
        with self._lock:
            if key in self._items:
                self._bytes -= self._items.pop(key)[1]
            self._items[key] = (value, size)
            self._bytes += size
            while self._bytes > self.max_bytes and len(self._items) > 1:
                _, (_, old_size) = self._items.popitem(last=False)
                self._bytes -= old_size

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._items

    def invalidate(self, predicate) -> int:
        """Drop every entry whose key satisfies ``predicate``; returns the count."""
        with self._lock:
            doomed = [k for k in self._items if predicate(k)]
            for k in doomed:
                self._bytes -= self._items.pop(k)[1]
            return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "n_items": len(self._items),
                "bytes": self._bytes,
                "max_bytes": self.max_bytes,
                "hits": self.hits,
                "misses": self.misses,
            }
