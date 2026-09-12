"""A client that reuses the server's protocol handler."""

from gevent import socket

from .protocol import CommandError, Error, ProtocolHandler


def _decode(value):
    """Recursively convert bytes to str. Other types pass through untouched."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        return {_decode(k): _decode(v) for k, v in value.items()}
    return value


class Client:
    def __init__(self, host="127.0.0.1", port=31337, decode_responses=False):
        self._protocol = ProtocolHandler()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect((host, port))
        self._fh = self._socket.makefile("rwb")
        self._decode_responses = decode_responses

    def get(self, key):
        return self.execute("GET", key)

    def set(self, key, value):
        return self.execute("SET", key, value)

    def delete(self, *keys):
        return self.execute("DEL", *keys)

    def flush(self):
        return self.execute("FLUSHDB")

    def mget(self, *keys):
        return self.execute("MGET", *keys)

    def mset(self, *items):
        return self.execute("MSET", *items)

    def execute(self, *args):
        self._protocol.write_response(self._fh, args)
        resp = self._protocol.handle_request(self._fh)
        if isinstance(resp, Error):
            message = resp.message
            if isinstance(message, bytes):
                message = message.decode("utf-8", "replace")
            raise CommandError(message)
        return _decode(resp) if self._decode_responses else resp
