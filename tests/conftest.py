"""Shared fixtures.

The protocol and command tests need no network -- they drive the objects
directly. Only the client tests need a live server, so the server fixture
lives here and starts one real gevent server in a subprocess (the same way
the server actually runs, monkey-patched), on an OS-assigned port.
"""

import socket
import subprocess
import sys
import time

import pytest

from redlite import Client

# Run the server exactly as production does: patch first, then serve.
# port=0 lets the OS pick a free port; we print it so the fixture can read it.
_SERVER_SRC = (
    "from gevent import monkey; monkey.patch_all()\n"
    "import gevent\n"
    "from redlite import Server\n"
    "s = Server(port=0)\n"
    "s.start()\n"
    "print(s.port, flush=True)\n"
    "while True:\n"
    "    gevent.sleep(3600)\n"
)


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
        [sys.executable, "-c", _SERVER_SRC],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server exited on startup:\n" + proc.stderr.read())
        port = int(line.strip())
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
