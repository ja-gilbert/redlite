"""The RESP codec: bytes on the wire <-> Python objects.

These drive ProtocolHandler directly through a BytesIO, so there's no
server and no socket. They pin the *promises* of the protocol layer:
values survive a round trip unchanged (including binary data), the wire
format matches the RESP spec where a client would notice, and malformed
input raises rather than returning garbage.
"""

from io import BytesIO

import pytest

from redlite import CommandError, Disconnect, Error, ProtocolHandler


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
        b"foobar",
        b"",  # empty bulk string
        42,
        -7,
        None,
        [b"a", b"b"],
        [[1], b"hi"],  # nested array
        {b"k": b"v"},
        b"a\r\nb",  # embedded CRLF: the whole reason RESP beats readline()
    ],
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


def test_str_is_sent_as_utf8_bulk_string(proto):
    assert serialize(proto, "hi") == b"$2\r\nhi\r\n"


def test_error_is_not_serialized_as_array(proto):
    # Error is a namedtuple, so it *is* a tuple; the handler must treat it
    # as a RESP error (-...) and not fall into the list/tuple branch.
    assert serialize(proto, Error(b"boom")) == b"-boom\r\n"
    assert parse(proto, b"-boom\r\n") == Error(b"boom")


def test_empty_read_raises_disconnect(proto):
    with pytest.raises(Disconnect):
        parse(proto, b"")


def test_unknown_type_byte_raises_command_error(proto):
    with pytest.raises(CommandError):
        parse(proto, b"@nope\r\n")
