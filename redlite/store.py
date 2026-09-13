"""The key-value store: one dict, plus the clock that decides when keys will expire."""

import time
from collections.abc import Callable, Iterator

from .protocol import Value


class KeyValueStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._data: dict[Value, Value] = {}
        self._clock = clock

    def now(self) -> float:
        """Seconds since the epoch, as injected clock sees them.

        Every expiry decision will read time through here, so tests can
        hand in a clock they control and don't have to sleep().
        """
        return self._clock()

    def get(self, key: Value, default: Value = None) -> Value:
        return self._data.get(key, default)

    def set(self, key: Value, value: Value) -> None:
        self._data[key] = value

    def delete(self, key: Value) -> bool:
        """Remove `key`. True if it was there."""
        if key not in self._data:
            return False
        del self._data[key]
        return True

    def pop(self, key: Value) -> Value:
        """Remove `key` and return its value, or None if it wasn't there."""
        return self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __contains__(self, key: Value) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[Value]:
        # A snapshot, so caller may delete keys while iterating
        return iter(list(self._data))
