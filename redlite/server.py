"""The gevent TCP server and command dispatch."""

import logging
from collections.abc import Callable
from fnmatch import fnmatchcase

from gevent import socket
from gevent.pool import Pool
from gevent.server import StreamServer

from .protocol import (
    OK,
    PONG,
    CommandError,
    Disconnect,
    Error,
    ProtocolHandler,
    SimpleString,
    Value,
)

log = logging.getLogger(__name__)


def _wrong_args(command: str) -> CommandError:
    return CommandError(
        f"ERR wrong number of arguments for '{command.lower()}' command"
    )


def _parse_int(value: Value) -> int:
    if isinstance(value, (bytes, str, int)):
        try:
            return int(value)
        except ValueError:
            pass
    raise CommandError("ERR value is not an integer or out of range")


class Server:
    def __init__(
        self, host: str = "127.0.0.1", port: int = 31337, max_clients: int = 64
    ) -> None:
        self._pool = Pool(max_clients)
        self._server = StreamServer(
            (host, port), self.connection_handler, spawn=self._pool
        )

        self._protocol = ProtocolHandler()
        self._kv: dict[Value, Value] = {}

        self._commands = self.get_commands()

    def get_commands(self) -> dict[str, Callable[..., Value]]:
        return {
            "PING": self.ping,
            "ECHO": self.echo,
            "COMMAND": self.command,
            "GET": self.get,
            "SET": self.set,
            "DEL": self.delete,
            "FLUSHDB": self.flush,
            "FLUSHALL": self.flush,
            "MGET": self.mget,
            "MSET": self.mset,
            "INCR": self.incr,
            "DECR": self.decr,
            "INCRBY": self.incrby,
            "DECRBY": self.decrby,
            "EXISTS": self.exists,
            "DBSIZE": self.dbsize,
            "KEYS": self.keys,
            "APPEND": self.append,
            "STRLEN": self.strlen,
            "GETDEL": self.getdel,
            "QUIT": self.quit,
        }

    def get_response(self, data: Value) -> Value:
        if isinstance(data, bytes):
            data = data.split()  # a simple-string request: b"SET k v"
        if not isinstance(data, (list, tuple)):
            raise CommandError("ERR request must be a list or a simple string")

        if not data:
            raise CommandError("ERR missing command")

        command = data[0]
        if isinstance(command, bytes):
            command = command.decode("utf-8", "replace")
        if not isinstance(command, str):
            raise CommandError("ERR command name must be a string")
        command = command.upper()

        if command not in self._commands:
            raise CommandError(f"ERR unknown command '{command}'")

        try:
            return self._commands[command](*data[1:])
        except TypeError:
            raise _wrong_args(command)

    def ping(self) -> SimpleString:
        return PONG

    def echo(self, message: Value) -> Value:
        return message

    def command(self, *args: Value) -> list[Value]:
        # Real Redis would describe every command here. Clients only need an
        # array to proceed, so an empty one keeps redis-cli's handshake quiet
        return []

    def quit(self) -> None:
        raise Disconnect(reply=OK)

    def get(self, key: Value) -> Value:
        return self._kv.get(key)

    def set(self, key: Value, value: Value) -> SimpleString:
        self._kv[key] = value
        return OK

    def delete(self, *keys: Value) -> int:
        if not keys:
            raise _wrong_args("DEL")
        removed = 0
        for key in keys:
            if key in self._kv:
                del self._kv[key]
                removed += 1
        return removed

    def flush(self) -> SimpleString:
        self._kv.clear()
        return OK

    def mget(self, *keys: Value) -> list[Value]:
        return [self._kv.get(key) for key in keys]

    def mset(self, *items: Value) -> SimpleString:
        if len(items) % 2 != 0:
            raise _wrong_args("MSET")
        data = list(zip(items[::2], items[1::2]))
        for key, value in data:
            self._kv[key] = value
        return OK

    def _bytes_at(self, key: Value) -> bytes:
        """The string value at `key` (empty if missing). Any other type is a WRONGTYPE."""
        value = self._kv.get(key, b"")
        if not isinstance(value, bytes):
            raise CommandError(
                "WRONGTYPE Operation against a key holding the wrong kind of value"
            )
        return value

    def _incr_by(self, key: Value, delta: int) -> int:
        value = _parse_int(self._kv.get(key, b"0")) + delta
        self._kv[key] = str(value).encode()
        return value

    def incr(self, key: Value) -> int:
        return self._incr_by(key, 1)

    def decr(self, key: Value) -> int:
        return self._incr_by(key, -1)

    def incrby(self, key: Value, amount: Value) -> int:
        return self._incr_by(key, _parse_int(amount))

    def decrby(self, key: Value, amount: Value) -> int:
        return self._incr_by(key, -_parse_int(amount))

    def exists(self, *keys: Value) -> int:
        if not keys:
            raise _wrong_args("EXISTS")
        return sum(1 for key in keys if key in self._kv)

    def dbsize(self) -> int:
        return len(self._kv)

    def keys(self, pattern: bytes) -> list[bytes]:
        return [
            key
            for key in self._kv
            if isinstance(key, bytes) and fnmatchcase(key, pattern)
        ]

    def append(self, key: Value, value: bytes) -> int:
        new = self._bytes_at(key) + value
        self._kv[key] = new
        return len(new)

    def strlen(self, key: Value) -> int:
        return len(self._bytes_at(key))

    def getdel(self, key: Value) -> Value:
        return self._kv.pop(key, None)

    def connection_handler(self, conn: socket.socket, address: tuple[str, int]) -> None:
        host, port = address
        log.debug("client connected from %s:%s", host, port)

        # Convert "conn" (socket object) into a file-like object
        socket_file = conn.makefile("rwb")

        # Process client requests until client disconnects.
        while True:
            try:
                data = self._protocol.handle_request(socket_file)
                resp = self.get_response(data)
            except Disconnect as exc:
                if exc.reply is not None:
                    self._protocol.write_response(socket_file, exc.reply)
                break
            except CommandError as exc:
                resp = Error(exc.args[0])
            except Exception:
                log.exception("unhandled error serving %s:%s; closing", host, port)
                break

            self._protocol.write_response(socket_file, resp)

        log.debug("client disconnected from %s:%s", host, port)

    def start(self) -> None:
        self._server.start()
        log.info(
            "listening on %s:%s", self._server.server_host, self._server.server_port
        )

    def stop(self) -> None:
        self._server.stop()

    @property
    def port(self) -> int:
        port = self._server.server_port
        if port is None:
            raise RuntimeError("server is not listening on a TCP port")
        return port

    def run(self) -> None:
        self.start()
        self._server.serve_forever()
