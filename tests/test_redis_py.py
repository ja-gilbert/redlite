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


def test_redis_py_ping_returns_pong(r):
    assert r.ping() is True  # redis-py checks the reply is exactly PONG


def test_redis_py_data_commands_round_trip(r):
    assert r.set("k", "v") is True  # redis-py checks the reply is exactly OK
    assert r.get("k") == b"v"
    assert r.mset({"a": "1", "b": "2"}) is True
    assert r.mget("a", "b", "missing") == [b"1", b"2", None]
    assert r.delete("k") == 1
    assert r.get("k") is None
    # a server error must reach the real client as ResponseError, not as data
    with pytest.raises(redis.ResponseError):
        r.execute_command("BOGUS")
