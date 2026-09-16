"""A client that reuses the server's protocol handler."""

from typing import Self

from gevent import socket

from .protocol import CommandError, Error, ProtocolHandler, Value


def _decode(value: Value) -> Value:
    """Recursively convert bytes to str. Other types pass through untouched."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        return {_decode(k): _decode(v) for k, v in value.items()}
    return value


class Client:
    def __init__(
        self, host: str = "127.0.0.1", port: int = 31337, decode_responses: bool = False
    ) -> None:
        self._protocol = ProtocolHandler()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect((host, port))
        self._socket_file = self._socket.makefile("rwb")
        self._decode_responses = decode_responses

    def close(self) -> None:
        self._socket_file.close()
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(self, key: Value) -> Value:
        return self.execute("GET", key)

    def set(self, key: Value, value: Value) -> Value:
        return self.execute("SET", key, value)

    def delete(self, *keys: Value) -> Value:
        return self.execute("DEL", *keys)

    def flush(self) -> Value:
        return self.execute("FLUSHDB")

    def mget(self, *keys: Value) -> Value:
        return self.execute("MGET", *keys)

    def mset(self, *items: Value) -> Value:
        return self.execute("MSET", *items)

    def execute(self, *args: Value) -> Value:
        self._protocol.write_response(self._socket_file, args)
        resp = self._protocol.handle_request(self._socket_file)
        if isinstance(resp, Error):
            message = resp.message
            if isinstance(message, bytes):
                message = message.decode("utf-8", "replace")
            raise CommandError(message)
        return _decode(resp) if self._decode_responses else resp
