"""The key-value store: one dict, plus the clock that decides when keys will expire."""

import random
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

    def _live(self, key: Value) -> bool:
        """Whether `key` exists, deleting it first if its expiry has passed.

        This is lazy expiry: every method that touches a key goes through
        here (expiry() is the one exception, and says why), so an expired
        key is never seen, and it is freed by whichever command trips over
        it. Keys nothing touches are the sweeper's job.
        """
        when = self._expires.get(key)
        # Strictly after, as Redis's keyIsExpired: a key that expires this
        # very instant is still here (EXPIRE 0, by contrast, deletes at once).
        if when is not None and self.now() > when:
            del self._data[key]
            del self._expires[key]
        return key in self._data

    def get(self, key: Value, default: Value = None) -> Value:
        if not self._live(key):
            return default
        return self._data[key]

    def set(self, key: Value, value: Value, keep_ttl: bool = False) -> None:
        self._live(key)  # an expired key must not lend keep_ttl its old expiry
        self._data[key] = value
        # A fresh write drops the expiry, as Redis's SET does. Commands that
        # edit the value in place (i.e. INCR, APPEND) pass keep_ttl=True
        if not keep_ttl:
            self._expires.pop(key, None)

    def delete(self, key: Value) -> bool:
        """Remove `key`. True if it was there."""
        if not self._live(key):
            return False
        del self._data[key]
        self._expires.pop(key, None)
        return True

    def pop(self, key: Value) -> Value:
        """Remove `key` and return its value, or None if it wasn't there."""
        if not self._live(key):
            return None
        self._expires.pop(key, None)
        return self._data.pop(key)

    def clear(self) -> None:
        self._data.clear()
        self._expires.clear()

    def expire_at(self, key: Value, when: float) -> bool:
        """Make `key` expire at `when`, in now()'s seconds. False if no key.

        A time already in the past deletes the key on the spot, which is what
        Redis does for EXPIRE with a timeout of zero or less.
        """
        if not self._live(key):
            return False
        if when <= self.now():
            self.delete(key)
        else:
            self._expires[key] = when
        return True

    def expiry(self, key: Value) -> float | None:
        """When `key` expires, in now()'s seconds, or None if it never does."""
        # The one key method that does not reap. PTTL asks __contains__ first;
        # if a second reap here caught the deadline passing in between, PTTL
        # would answer -1 ("no expiry") for a key Redis says is gone (-2).
        return self._expires.get(key)

    def persist(self, key: Value) -> bool:
        """Remove `key`'s expiry. True if it had one."""
        if not self._live(key):
            return False
        return self._expires.pop(key, None) is not None

    def sweep(self, sample: int = 20, budget: float = 0.025) -> int:
        """One cycle of active expiry, done the way Redis does it.

        Look at up to `sample` random keys that have an expiry, free the dead
        ones, and go again while more than a quarter of them were dead: the
        sample says the rest of the keyspace is probably like this. Stop
        after `budget` seconds regardless, so a mass expiry cannot stall the
        server. Returns how many keys were freed.
        """
        stop_at = time.monotonic() + budget
        freed = 0
        # One O(n) snapshot of the candidates per cycle: a fresh list() every
        # round would spend the whole budget copying instead of freeing keys.
        # Each round takes its sample out of the snapshot, moving the last
        # entry over the one it took, so no key is looked at twice in a cycle.
        pool = list(self._expires)
        while pool and time.monotonic() < stop_at:
            keys = []
            for _ in range(min(sample, len(pool))):
                i = random.randrange(len(pool))
                keys.append(pool[i])
                pool[i] = pool[-1]
                pool.pop()
            dead = sum(1 for key in keys if not self._live(key))
            freed += dead
            if dead * 4 <= len(keys):
                break
        return freed

    def __contains__(self, key: Value) -> bool:
        return self._live(key)

    def __len__(self) -> int:
        # Deliberately raw: like Redis's DBSIZE, this counts expired keys the
        # sweeper has not reached yet. Checking each one would make it O(n).
        return len(self._data)

    def __iter__(self) -> Iterator[Value]:
        # A snapshot, so caller may delete keys while iterating
        return (key for key in list(self._data) if self._live(key))
