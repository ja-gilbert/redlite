"""The client and server together, over a real socket.

These use a live server (see conftest) and exercise the full stack:
client encodes a request, server parses/executes/replies, client decodes.
The point is the seams the lower-layer tests can't reach -- real bytes on a
real connection, decode_responses, and shared state across connections.
"""

import socket

import pytest

from redlite import Client, CommandError


def test_set_and_get_round_trip_over_a_socket(client):
    assert client.set("k", "v") == b"OK"
    assert client.get("k") == b"v"


def test_decode_responses_returns_str(client_decoded):
    client_decoded.set("greet", "hello")
    assert client_decoded.get("greet") == "hello"
    client_decoded.mset("a", "1", "b", "2")
    assert client_decoded.mget("a", "b") == ["1", "2"]


def test_default_client_is_binary_safe(client):
    client.set("bin", "a\r\nb")
    assert client.get("bin") == b"a\r\nb"


def test_decode_responses_raises_on_non_utf8_data(client_decoded):
    # This is why decode_responses defaults to False: you can always hand
    # back bytes, but decoding arbitrary binary can only fail.
    client_decoded.set("png", b"\x89PNG\r\n")
    with pytest.raises(UnicodeDecodeError):
        client_decoded.get("png")


def test_two_clients_share_the_same_store(client, server_port):
    client.set("shared", "yes")
    other = Client(port=server_port)
    assert other.get("shared") == b"yes"


def test_error_reply_becomes_a_raised_exception(client):
    with pytest.raises(CommandError):
        client.execute("BOGUS")


def test_inline_command_over_a_raw_socket(server_port):
    # The plain-text form: no RESP framing, just words and a newline.
    sock = socket.create_connection(("127.0.0.1", server_port), timeout=3)
    sock.settimeout(3)
    fh = sock.makefile("rwb")

    fh.write(b"SET k v\r\n")
    fh.flush()
    assert fh.readline() == b"+OK\r\n"

    fh.write(b"GET k\r\n")
    fh.flush()
    assert fh.readline() == b"$1\r\n"
    assert fh.readline() == b"v\r\n"
    sock.close()


def test_unknown_inline_command_gets_an_error_and_keeps_the_connection(server_port):
    sock = socket.create_connection(("127.0.0.1", server_port), timeout=3)
    sock.settimeout(3)
    fh = sock.makefile("rwb")

    fh.write(b"BOGUS\r\n")
    fh.flush()
    reply = fh.readline()
    assert reply.startswith(b"-"), f"expected an error reply, got {reply!r}"

    fh.write(b"SET k v\r\n")  # connection must still be usable
    fh.flush()
    assert fh.readline() == b"+OK\r\n"
    sock.close()


def test_quit_replies_ok_then_server_closes(server_port):
    sock = socket.create_connection(("127.0.0.1", server_port), timeout=3)
    sock.settimeout(3)
    fh = sock.makefile("rwb")

    fh.write(b"QUIT\r\n")
    fh.flush()
    assert fh.readline() == b"+OK\r\n"
    assert fh.readline() == b""  # EOF: server hung up, not us
    sock.close()


def test_close_releases_connection(server_port):
    c = Client(port=server_port)
    c.close()
    with pytest.raises((OSError, ValueError)):
        c.get("k")  # the socket is really gone, not just flagged


def test_client_works_as_context_manager(server_port):
    with Client(port=server_port) as c:
        assert c.set("k", "v") == b"OK"
    with pytest.raises((OSError, ValueError)):
        c.get("k")  # closed on leaving the block
