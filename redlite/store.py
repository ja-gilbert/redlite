"""The key-value store: one dict, plus the clock that decides when keys will expire."""

import time
from collections.abc import Callable, Iterator

from .protocol import Value


class KeyValueStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._data: dict[Value, Value] = {}
        # Absolute expiry times, in the clock's seconds, for keys that have
        # one. Kept beside data like Redis's db->expires, so the sweeper
        # can sample only keys that can actually expire.
        self._expires: dict[Value, float] = {}
        self._clock = clock

    def now(self) -> float:
        """Seconds since the epoch, as injected clock sees them.

        Every expiry decision will read time through here, so tests can
        hand in a clock they control and don't have to sleep().
        """
        return self._clock()

    def get(self, key: Value, default: Value = None) -> Value:
        return self._data.get(key, default)

    def set(self, key: Value, value: Value, keep_ttl: bool = False) -> None:
        self._data[key] = value
        # A fresh write drops the expiry, as Redis's SET does. Commands that
        # edit the value in place (i.e. INCR, APPEND) pass keep_ttl=True
        if not keep_ttl:
            self._expires.pop(key, None)

    def delete(self, key: Value) -> bool:
        """Remove `key`. True if it was there."""
        if key not in self._data:
            return False
        del self._data[key]
        self._expires.pop(key, None)
        return True

    def pop(self, key: Value) -> Value:
        """Remove `key` and return its value, or None if it wasn't there."""
        self._expires.pop(key, None)
        return self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()
        self._expires.clear()

    def expire_at(self, key: Value, when: float) -> bool:
        """Make `key` expire at `when`, in now()'s seconds. False if no key.

        A time already in the past deletes the key on the spot, which is what
        Redis does for EXPIRE with a timeout of zero or less.
        """
        if key not in self._data:
            return False
        if when <= self.now():
            self.delete(key)
        else:
            self._expires[key] = when
        return True

    def expiry(self, key: Value) -> float | None:
        """When `key` expires, in now()'s seconds, or None if it never does."""
        return self._expires.get(key)

    def persist(self, key: Value) -> bool:
        """Remove `key`'s expiry. True if it had one."""
        return self._expires.pop(key, None) is not None

    def __contains__(self, key: Value) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[Value]:
        # A snapshot, so caller may delete keys while iterating
        return iter(list(self._data))
