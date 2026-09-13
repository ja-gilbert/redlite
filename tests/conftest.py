"""Shared fixtures.

The protocol and command tests need no network -- they drive the objects
directly. Only the client tests need a live server, so the server fixture
lives here and starts one real gevent server in a subprocess (the same way
the server actually runs, monkey-patched), on an OS-assigned port.
"""

import re
import socket
import subprocess
import sys
import time

import pytest

from redlite import Client

# Start the server the way a user does -- through the real entry point -- so
# the tests also cover __main__ and argparse. port=0 lets the OS pick a free
# port; the server logs which one it got, and the fixture reads it back.
_SERVER_CMD = [sys.executable, "-m", "redlite", "--port", "0"]
_LISTENING = re.compile(r"listening on 127\.0\.0\.1:(\d+)")


def _wait_until_accepting(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"server on port {port} never started accepting")


@pytest.fixture(scope="session")
def server_port():
    """A live redlite server in a subprocess. Yields its port."""
    proc = subprocess.Popen(
        _SERVER_CMD,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stderr.readline()  # logging goes to stderr
        match = _LISTENING.search(line)
        if not match:
            proc.terminate()
            raise RuntimeError(
                "server did not announce a port; output was:\n"
                + line
                + proc.stderr.read()
            )
        port = int(match.group(1))
        _wait_until_accepting(port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def client(server_port):
    """A client that returns raw bytes. Flushed so each test starts empty."""
    c = Client(port=server_port)
    c.flush()
    yield c
    c.close()


@pytest.fixture
def client_decoded(server_port):
    """A client with decode_responses=True. Flushed before each test."""
    c = Client(port=server_port, decode_responses=True)
    c.flush()
    yield c
    c.close()


class FakeClock:
    """Stands in for time.time(). Tests move it with advance() instead of sleeping."""

    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()
