"""LRU-style cache for tool call results.

Keys are (tool_name, sorted_args_tuple). Entries expire after
300 seconds to avoid stale results if the drawing changes.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    result: Any
    timestamp: float
    tool_name: str
    args_key: str


class ToolCache:
    """Cache for tool call results.

    Keys are (tool_name, normalized_args). Entries expire after
    300 seconds to avoid stale results.
    """

    def __init__(self, max_size: int = 256, ttl: float = 300.0):
        self._cache: dict[str, CacheEntry] = {}
        self._max_size = max_size
        self._ttl = ttl
        self._hits = 0
        self._misses = 0

    def _make_key(self, tool_name: str, args: dict) -> str:
        """Create a deterministic cache key from tool name and args."""
        # Sort args for deterministic key; exclude large binary values
        clean_args = {
            k: v
            for k, v in sorted(args.items())
            if not isinstance(v, (bytes, bytearray)) and (not isinstance(v, str) or len(v) < 1000)
        }
        raw = json.dumps({"tool": tool_name, "args": clean_args}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, tool_name: str, args: dict) -> Any | None:
        key = self._make_key(tool_name, args)
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        if time.time() - entry.timestamp > self._ttl:
            del self._cache[key]
            self._misses += 1
            return None
        self._hits += 1
        return entry.result

    def put(self, tool_name: str, args: dict, result: Any) -> None:
        if len(self._cache) >= self._max_size:
            # Evict oldest entry
            oldest_key = min(self._cache, key=lambda k: self._cache[k].timestamp)
            del self._cache[oldest_key]
        key = self._make_key(tool_name, args)
        self._cache[key] = CacheEntry(
            result=result,
            timestamp=time.time(),
            tool_name=tool_name,
            args_key=key,
        )

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def clear(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0
