from __future__ import annotations
import time
from typing import Any, Dict, Optional, Tuple


class SessionCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        if key in self._store:
            ts, value = self._store[key]
            if time.time() - ts < self._ttl:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time(), value)

    def has(self, key: str) -> bool:
        return self.get(key) is not None


cache = SessionCache()
