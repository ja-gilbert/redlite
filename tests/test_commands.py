"""Command dispatch and storage

These call get_response directly on a Server that is constructed but never
started, so no port is bound and no socket is involved. They pin what the
commands *promise* as a key-value store, and that bad input turns into an
error rather than crashing the connection.
"""

import pytest

from redlite import CommandError, Disconnect, KeyValueStore, Server
from redlite.protocol import OK, PONG


@pytest.fixture
def server(clock):
    # Constructed, not started - no port is bound. The fake clock is what
    # lets the TTL tests move time instead of sleeping.
    return Server(port=0, store=KeyValueStore(clock=clock))


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


def test_flushdb_empties_the_store_and_returns_count(server):
    run(server, b"MSET", b"a", b"1", b"b", b"2")
    assert run(server, b"FLUSHDB") == OK
    assert run(server, b"GET", b"a") is None


def test_flushall_is_alias_for_flushdb(server):
    # Redis has both; with a single database they do the same thing.
    run(server, b"SET", b"k", b"v")
    assert run(server, b"FLUSHALL") == OK
    assert run(server, b"GET", b"k") is None


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


@pytest.mark.parametrize(
    "argv, reply",
    [
        ([b"PING"], PONG),
        ([b"ECHO", b"hello"], b"hello"),
        ([b"COMMAND", b"DOCS"], []),  # redis-cli sends this on connect
        ([b"COMMAND"], []),  # older clients send it bare
    ],
)
def test_handshake_commands_reply_as_redis_close(server, argv, reply):
    assert run(server, *argv) == reply


def test_quit_disconnects_with_ok_reply(server):
    with pytest.raises(Disconnect) as info:
        run(server, b"QUIT")
    assert info.value.reply == OK


def test_error_follows_redis_conventions(server):
    # The first word is an error code clients switch on; the wording is
    # what you grep for. Both need to match real Redis outputs
    with pytest.raises(CommandError, match=r"^ERR unknown command 'BOGUS'$"):
        run(server, b"BOGUS")
    with pytest.raises(
        CommandError, match=r"^ERR wrong number of arguments for 'get' command$"
    ):
        run(server, b"GET")


def test_incr_starts_from_zero_and_stores_string(server):
    assert run(server, b"INCR", b"hits") == 1
    assert run(server, b"INCR", b"hits") == 2
    assert run(server, b"GET", b"hits") == b"2"  # normal string value, not special type


def test_counter_family_shares_one_key(server):
    run(server, b"SET", b"n", b"10")
    assert run(server, b"INCRBY", b"n", b"5") == 15
    assert run(server, b"DECR", b"n") == 14
    assert run(server, b"DECRBY", b"n", b"4") == 10
    assert run(server, b"INCRBY", b"n", b"-10") == 0


def test_non_int_val_or_amount_is_error_and_leaves_store_alone(server):
    run(server, b"SET", b"word", b"hello")
    with pytest.raises(
        CommandError, match=r"^ERR value is not an integer or out of range$"
    ):
        run(server, b"INCR", b"word")
    with pytest.raises(
        CommandError, match=r"^ERR value is not an integer or out of range$"
    ):
        run(server, b"INCRBY", b"word", b"abc")
    assert run(server, b"GET", b"word") == b"hello"


def test_exist_counts_how_many_keys_are_present(server):
    run(server, b"MSET", b"a", b"1", b"b", b"2")
    assert run(server, b"EXISTS", b"a", b"b", b"nope") == 2
    assert run(server, b"EXISTS", b"nope") == 0
    with pytest.raises(CommandError):
        run(server, b"EXISTS")


def test_dbsize_tracks_num_keys(server):
    assert run(server, b"DBSIZE") == 0
    run(server, b"MSET", b"a", b"1", b"b", b"2")
    assert run(server, b"DBSIZE") == 2
    run(server, b"DEL", b"a")
    assert run(server, b"DBSIZE") == 1


def test_keys_return_matching_glob(server):
    run(server, b"MSET", b"user:1", b"x", b"user:2", b"y", b"hits", b"z")
    assert sorted(run(server, b"KEYS", b"user:*")) == [b"user:1", b"user:2"]
    assert sorted(run(server, b"KEYS", b"*")) == [b"hits", b"user:1", b"user:2"]
    assert run(server, b"KEYS", b"nomatch*") == []


def test_append_extends_value_and_strlen_measures(server):
    assert run(server, b"STRLEN", b"k") == 0  # missing key has length 0
    assert run(server, b"APPEND", b"k", b"hello") == 5  # missing key: append to empty
    assert run(server, b"APPEND", b"k", b" world") == 11
    assert run(server, b"GET", b"k") == b"hello world"
    assert run(server, b"STRLEN", b"k") == 11


def test_getdel_returns_Value_and_removes_key(server):
    run(server, b"SET", b"k", b"v")
    assert run(server, b"GETDEL", b"k") == b"v"
    assert run(server, b"GET", b"k") is None
    assert run(server, b"GETDEL", b"k") is None  # already gone


def test_string_commands_on_non_string_are_wrongtype(server):
    # Python client can store an int or a list; REDIS calls this WRONGTYPE.
    # Without this, len() blows up into arity guard and lied about the cause.
    run(server, b"SET", b"n", 5)
    with pytest.raises(CommandError, match=r"^WRONGTYPE"):
        run(server, b"STRLEN", b"n")
    with pytest.raises(CommandError, match=r"^WRONGTYPE"):
        run(server, b"APPEND", b"n", b"x")


def test_server_uses_injected_store():
    store = KeyValueStore()
    server = Server(port=0, store=store)
    run(server, b"SET", b"k", b"v")
    assert store.get(b"k") == b"v"  # same object, not a copy


def test_ttl_distinguishes_a_missing_key_from_a_key_with_no_expiry(server):
    # Redis's two negative replies: -2 is "no such key", -1 is "no expiry".
    assert run(server, b"TTL", b"nope") == -2
    run(server, b"SET", b"k", b"v")
    assert run(server, b"TTL", b"k") == -1
    assert run(server, b"PTTL", b"k") == -1


def test_expire_and_pexpire_set_a_countdown_that_ttl_and_pttl_report(server, clock):
    run(server, b"SET", b"k", b"v")
    assert run(server, b"EXPIRE", b"k", b"10") == 1
    assert run(server, b"TTL", b"k") == 10
    clock.advance(4)
    assert run(server, b"PTTL", b"k") == 6000
    assert run(server, b"PEXPIRE", b"k", b"2500") == 1  # replaces the 6s left
    assert run(server, b"PTTL", b"k") == 2500
    assert run(server, b"TTL", b"k") == 3  # 2.5s rounds up, as in Redis (not to even)
    clock.advance(0.2)
    assert run(server, b"PTTL", b"k") == 2300  # nearest ms, not truncated
    assert run(server, b"TTL", b"k") == 2


def test_persist_removes_the_expiry(server):
    run(server, b"SET", b"k", b"v")
    run(server, b"EXPIRE", b"k", b"10")
    assert run(server, b"PERSIST", b"k") == 1
    assert run(server, b"TTL", b"k") == -1
    assert run(server, b"PERSIST", b"k") == 0  # nothing left to remove
    assert run(server, b"PERSIST", b"nope") == 0


def test_expire_on_a_missing_key_is_zero_and_a_bad_timeout_is_an_error(server):
    assert run(server, b"EXPIRE", b"nope", b"10") == 0
    run(server, b"SET", b"k", b"v")
    with pytest.raises(
        CommandError, match=r"^ERR value is not an integer or out of range$"
    ):
        run(server, b"EXPIRE", b"k", b"soon")
    # Redis keeps expiry times as signed 64-bit milliseconds and refuses what
    # would not fit, so a TTL or PTTL reply always fits a RESP integer.
    with pytest.raises(
        CommandError, match=r"^ERR invalid expire time in 'expire' command$"
    ):
        run(server, b"EXPIRE", b"k", b"9223372036854775807")
    assert run(server, b"TTL", b"k") == -1  # the bad calls changed nothing


def test_expire_with_a_timeout_in_the_past_deletes_the_key(server):
    # Redis deletes right away rather than storing an already-passed expiry.
    run(server, b"SET", b"k", b"v")
    run(server, b"EXPIRE", b"k", b"10")
    assert run(server, b"EXPIRE", b"k", b"0") == 1
    assert run(server, b"GET", b"k") is None
    run(server, b"INCR", b"k")  # INCR keeps expiries, so a leaked one would show
    assert run(server, b"TTL", b"k") == -1


def test_set_drops_the_expiry_but_incr_and_append_keep_it(server):
    # Redis: SET writes a brand-new value, so the old expiry goes with the old
    # value. INCR and APPEND edit the value in place and leave the expiry alone.
    run(server, b"SET", b"n", b"1")
    run(server, b"EXPIRE", b"n", b"10")
    run(server, b"INCR", b"n")
    run(server, b"APPEND", b"n", b"0")
    assert run(server, b"TTL", b"n") == 10
    run(server, b"SET", b"n", b"5")
    assert run(server, b"TTL", b"n") == -1


def test_set_with_ex_or_px_sets_val_and_expiry_together(server):
    assert run(server, b"SET", b"k", b"v", b"EX", b"10") == OK
    assert run(server, b"GET", b"k") == b"v"
    assert run(server, b"TTL", b"k") == 10
    # Options are case-insensitive, and a new expiry replaces old ones.
    assert run(server, b"SET", b"k", b"v", b"px", b"1500") == OK
    assert run(server, b"PTTL", b"k") == 1500


def test_set_nx_creates_and_xx_overwrites_only(server):
    # Redis replies nil, not an error, when the condition says "don't write".
    # NX with an expiry is the lock: a refused write, with or without
    # its own expiry, must leave the holder's value and expiry alone
    assert run(server, b"SET", b"k", b"v", b"EX", b"10", b"NX") == OK
    assert run(server, b"SET", b"k", b"other", b"EX", b"5", b"NX") is None
    assert run(server, b"SET", b"k", b"other", b"NX") is None
    assert run(server, b"GET", b"k") == b"v"
    assert run(server, b"TTL", b"k") == 10
    assert run(server, b"SET", b"nope", b"v", b"XX") is None
    assert run(server, b"EXISTS", b"nope") == 0
    assert run(server, b"SET", b"k", b"other", b"XX") == OK
    assert run(server, b"GET", b"k") == b"other"


def test_set_keepttl_changes_the_value_but_not_expiry(server):
    run(server, b"SET", b"k", b"v", b"EX", b"10")
    assert run(server, b"SET", b"k", b"v2", b"KEEPTTL") == OK
    assert run(server, b"GET", b"k") == b"v2"
    assert run(server, b"TTL", b"k") == 10


def test_set_rejects_bad_options_before_writing(server):
    run(server, b"SET", b"k", b"old")
    # Unlike EXPIRE 0, SET ... EX 0 is refused outright, as is a deadline
    # past int64: Redis checks the options before it touches the key.
    for amount in (b"0", b"9223372036854775807"):
        with pytest.raises(
            CommandError, match=r"^ERR invalid expire time in 'set' command$"
        ):
            run(server, b"SET", b"k", b"new", b"EX", amount)
    with pytest.raises(
        CommandError, match=r"^ERR value is not an integer or out of range$"
    ):
        run(server, b"SET", b"k", b"new", b"EX", b"soon")
    # An unknown option, pairs that conflict, and one missing its amount.
    for bad in (
        [b"BOGUS"],
        [b"NX", b"XX"],
        [b"EX", b"10", b"PX", b"5"],
        [b"EX", b"10", b"KEEPTTL"],
        [b"EX"],
    ):
        with pytest.raises(CommandError, match=r"^ERR syntax error$"):
            run(server, b"SET", b"k", b"new", *bad)
    assert run(server, b"GET", b"k") == b"old"
    assert run(server, b"TTL", b"k") == -1
