from __future__ import annotations
import time
from typing import Any, Dict, Optional, Tuple


class SessionCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._store: Dict[str, Tuple[float, Any, Optional[int]]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        stored_at, value, ttl = entry
        if time.time() - stored_at < (self._ttl if ttl is None else ttl):
            return value
        self._store.pop(key, None)
        return None

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """ttl_seconds overrides the cache-wide lifetime for just this entry."""
        self._store[key] = (time.time(), value, ttl_seconds)

    def has(self, key: str) -> bool:
        return self.get(key) is not None


cache = SessionCache()
