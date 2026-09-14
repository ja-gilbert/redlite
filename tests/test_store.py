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


def test_removing_a_key_removes_its_expiry_with_it():
    # An expiry that outlives its key is a leak, a ghost for the sweeper, and a
    # timeout the next INCR on that name would inherit. DEL, GETDEL, and
    # FLUSHDB each go through one of these.
    store = KeyValueStore()

    def set_with_expiry():
        store.set(b"k", b"v")
        store.expire_at(b"k", store.now() + 10)

    set_with_expiry()
    store.delete(b"k")
    assert store.expiry(b"k") is None
    set_with_expiry()
    store.pop(b"k")
    assert store.expiry(b"k") is None
    set_with_expiry()
    store.clear()
    assert store.expiry(b"k") is None


def test_expiry_does_not_reap_so_a_dying_key_never_reads_as_persistent(clock):
    # PTTL asks __contains__ first and expiry() second, reading the clock for
    # each. If expiry() reaped too, a key that was live for the first question
    # and dead for the second would answer None, which PTTL reads as -1, "no
    # expiry": the opposite of the -2 Redis replies for a key that is gone.
    store = KeyValueStore(clock=clock)
    store.set(b"k", b"v")
    deadline = clock.now + 10
    store.expire_at(b"k", deadline)
    assert b"k" in store  # live, so PTTL gets past its first question
    clock.advance(11)  # the deadline passes between the two questions
    assert store.expiry(b"k") == deadline  # the deadline, not None
