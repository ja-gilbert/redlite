"""The key-value store on its own: no server, protocol, or socket.

The command tests already pin get/set/delete and others through the server,
so these only pin what the server cannot see: clock seam, which is the
reason the store is its own object (expiry needs a time source tests can move
without sleeping), and the promise that iterating is safe to delete under.
"""

import time

from redlite import KeyValueStore


def test_iteration_is_a_snapshot_so_keys_can_be_deleted():
    store = KeyValueStore()
    store.set(b"a", b"1")
    store.set(b"b", b"2")
    for key in store:  # a live dict view would raise "changed size during iteration"
        store.delete(key)
    assert len(store) == 0


def test_now_comes_from_injected_clock(clock):
    store = KeyValueStore(clock=clock)
    assert store.now() == clock.now
    clock.advance(30)
    assert store.now() == clock.now  # moved by test, not wall clock


def test_default_clock_is_wall_time():
    # Wall time, not monotonic: We need to persist expires as absolute
    # PEXPIREAT timestamps, and those have to mean the same thing after a
    # restart. Redis makes the same choice
    before = time.time()
    now = KeyValueStore().now()
    assert before <= now <= time.time()
