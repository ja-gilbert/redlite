"""The RESP codec: bytes on the wire <-> Python objects.

These drive ProtocolHandler directly through a BytesIO, so there's no
server and no socket. They are deliberately only what the live client and
redis-py tests cannot see: RESP shapes redlite never puts on a socket in
this suite (negative integers, nested arrays, maps), byte-exact framing the
server's own parser would forgive (the $-1 vs $0 null/empty distinction, a
str value, an error frame), and an exhausted stream raising Disconnect
rather than returning garbage. Plain bulk strings, flat arrays, +OK and
inline commands over a socket belong to the client tests.
"""

from io import BytesIO

import pytest

from redlite import Disconnect, Error, ProtocolHandler


@pytest.fixture
def proto():
    return ProtocolHandler()


def parse(proto, raw):
    return proto.handle_request(BytesIO(raw))


def serialize(proto, obj):
    buf = BytesIO()
    proto._write(buf, obj)
    return buf.getvalue()


@pytest.mark.parametrize(
    "value",
    [
        -7,  # TTL/PTTL reply -1/-2; no socket test carries a negative int
        [[1], b"hi"],  # an int inside an array: elements are not all bulk strings
        {b"k": b"v"},  # RESP map: no redlite command replies with one
    ],
    ids=["negative-int", "nested-array", "map"],
)
def test_value_survives_serialize_then_parse(proto, value):
    assert parse(proto, serialize(proto, value)) == value


def test_null_and_empty_string_are_distinct_on_the_wire(proto):
    # Round-tripping cannot catch these being swapped, but redis-cli can:
    # $-1 is "no value", $0 is "the empty string". Must not collapse.
    assert serialize(proto, None) == b"$-1\r\n"
    assert serialize(proto, b"") == b"$0\r\n\r\n"
    assert parse(proto, b"$-1\r\n") is None
    assert parse(proto, b"$0\r\n\r\n") == b""


def test_str_is_sent_as_a_bulk_string(proto):
    assert serialize(proto, "hi") == b"$2\r\nhi\r\n"


def test_error_is_not_serialized_as_array(proto):
    # Error is a namedtuple, so it *is* a tuple; the handler must treat it
    # as a RESP error (-...) and not fall into the list/tuple branch.
    assert serialize(proto, Error(b"boom")) == b"-boom\r\n"
    assert parse(proto, b"-boom\r\n") == Error(b"boom")


def test_empty_read_raises_disconnect(proto):
    with pytest.raises(Disconnect):
        parse(proto, b"")


def test_non_resp_line_is_parsed_as_inline_command(proto):
    # Anything not starting with a RESP type byte is a plain text command
    # line split on whitespace: what telnet and redis-benchmark send.
    assert parse(proto, b"SET foo bar\r\n") == [b"SET", b"foo", b"bar"]
    assert parse(proto, b"PING\r\n") == [b"PING"]
