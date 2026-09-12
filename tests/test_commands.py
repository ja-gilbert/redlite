"""Command dispatch and storage

These call get_response directly on a Server that is constructed but never
started, so no port is bound and no socket is involved. They pin what the
commands *promise* as a key-value store, and that bad input turns into an
error rather than crashing the connection.
"""

import pytest

from redlite import CommandError, Server


@pytest.fixture
def server():
    return Server(port=0)  # constructed, not started - no bindings


def run(server, *parts):
    return server.get_response(list(parts))


def test_set_then_get_returns_value(server):
    run(server, b"SET", b"k", b"v")
    assert run(server, b"GET", b"k") == b"v"


def test_get_missing_key_returns_none(server):
    assert run(server, b"GET", b"absent") is None


def test_del_reports_whether_key_existed(server):
    run(server, b"SET", b"k", b"v")
    assert run(server, b"DEL", b"k") == 1
    assert run(server, b"DEL", b"k") == 0


def test_del_takes_many_keys_and_counts_only_those_that_existed(server):
    run(server, b"MSET", b"a", b"1", b"b", b"2", b"c", b"3")
    assert run(server, b"DEL", b"a", b"c", b"nope") == 2
    assert run(server, b"GET", b"a") is None
    assert run(server, b"GET", b"b") == b"2"  # untouched


def test_del_with_no_keys_is_an_error(server):
    # Going variadic means zero keys no longer trips the arity guard, so
    # DEL alone would silently return 0. Redis errors; so should we.
    with pytest.raises(CommandError):
        run(server, b"DEL")


def test_mget_returns_none_for_missing_keys_in_place(server):
    run(server, b"MSET", b"a", b"1", b"b", b"2")
    assert run(server, b"MGET", b"a", b"x", b"b") == [b"1", None, b"2"]


def test_flush_empties_the_store_and_returns_count(server):
    run(server, b"MSET", b"a", b"1", b"b", b"2")
    assert run(server, b"FLUSH") == 2
    assert run(server, b"GET", b"a") is None


def test_command_names_are_notcase_sensitive(server):
    run(server, b"set", b"k", b"v")
    assert run(server, b"GeT", b"k") == b"v"


def test_unknown_command_raises_rather_than_crashing(server):
    with pytest.raises(CommandError):
        run(server, b"BOGUS")


def test_wrong_number_of_arguments_raises_rather_than_crashing(server):
    with pytest.raises(CommandError):
        run(server, b"GET")  # too few
    with pytest.raises(CommandError):
        run(server, b"GET", b"a", b"b")  # too many


def test_mset_with_odd_argument_count_is_an_error(server):
    # Bug: an odd arg count silently drops the trailing key. A caller who
    # miscounts should get an error, not a half-applied write.
    with pytest.raises(CommandError):
        run(server, b"MSET", b"a", b"1", b"b")
    assert run(server, b"GET", b"a") is None  # nothing was stored
