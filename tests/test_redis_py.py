"""The real redis-py client library against redlite

These aren't unit tests, rather a conformance check. If a change breaks what
redis-py expects on the wire, this fails in CI before redis-cli lets you know.
"""

import pytest
import redis


@pytest.fixture
def r(server_port):
    # redis-py >= 8 defaults to RESP3 and opens with HELLO; redlite is RESP2.
    client = redis.Redis(host="127.0.0.1", port=server_port, protocol=2)
    client.flushdb()
    yield client
    client.close()


def test_redis_py_connects_and_pings(r):
    assert r.ping() is True


def test_redis_py_data_commands_round_trip(r):
    assert r.set("k", "v") is True  # only True if we replied +OK
    assert r.get("k") == b"v"
    assert r.mset({"a": "1", "b": "2"}) is True
    assert r.mget("a", "b", "missing") == [b"1", b"2", None]
    assert r.delete("k") == 1
    assert r.get("k") is None


def test_redis_py_raises_on_server_errors(r):
    with pytest.raises(redis.ResponseError):
        r.execute_command("BOGUS")
