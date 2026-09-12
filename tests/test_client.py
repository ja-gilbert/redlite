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


def test_malformed_request_gets_an_error_and_keeps_the_connection(server_port):
    # Bug: an unparseable first byte (e.g. an inline "PING\r\n" from telnet)
    # makes handle_request raise, which connection_handler doesn't catch --
    # the greenlet dies and the connection drops with no reply. The server
    # should answer with an error and stay open for the next command.
    sock = socket.create_connection(("127.0.0.1", server_port), timeout=3)
    sock.settimeout(3)
    fh = sock.makefile("rwb")

    fh.write(b"PING\r\n")  # inline text, not a RESP array
    fh.flush()
    reply = fh.readline()
    assert reply.startswith(b"-"), f"expected an error reply, got {reply!r}"

    # Connection still usable: a well-formed command must still work.
    fh.write(b"*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n")
    fh.flush()
    assert fh.readline() == b"+OK\r\n"  # SET replies :1 today (+OK comes in Gate 1a)
    sock.close()
