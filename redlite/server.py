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
from .store import KeyValueStore

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


def _parse_set_options(
    options: tuple[Value, ...],
) -> tuple[int | None, bool, bytes | None]:
    """SET's trailing options, as (expiry in ms, keep old expiry, NX or XX).

    Mirrors Redis: options combine in any order case, EX and PX exclude
    each other and KEEPTTL, NX excludes XX, and anything else is a syntax
    error. The expiry amount is checked last, after the syntax, just like Redis.
    """
    expire: tuple[bytes, Value] | None = None  # (EX or PX, the raw amount)
    keep_ttl = False
    condition: bytes | None = None
    i = 0
    while i < len(options):
        arg = options[i]
        token = arg.upper() if isinstance(arg, bytes) else b""
        if token in (b"EX", b"PX") and not keep_ttl and i + 1 < len(options):
            if expire is not None and expire[0] != token:
                raise CommandError("ERR syntax error")
            expire = (token, options[i + 1])
            i += 2
        elif token == b"KEEPTTL" and expire is None:
            keep_ttl = True
            i += 1
        elif token in (b"NX", b"XX") and condition in (None, token):
            condition = token
            i += 1
        else:
            raise CommandError("ERR syntax error")
    if expire is None:
        return None, keep_ttl, condition
    unit, amount = expire
    ms = _parse_int(amount)
    if ms <= 0:
        raise CommandError("ERR invalid expire time in 'set' command")
    return ms * (1000 if unit == b"EX" else 1), keep_ttl, condition


class Server:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 31337,
        max_clients: int = 64,
        store: KeyValueStore | None = None,
    ) -> None:
        self._pool = Pool(max_clients)
        self._server = StreamServer(
            (host, port), self.connection_handler, spawn=self._pool
        )

        self._protocol = ProtocolHandler()
        # `is not None`, not `or`: an empty store has len() 0, so
        # `store or KeyValueStore()` would quietly replace an injected one.
        self._store = store if store is not None else KeyValueStore()

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
            "EXPIRE": self.expire,
            "PEXPIRE": self.pexpire,
            "TTL": self.ttl,
            "PTTL": self.pttl,
            "PERSIST": self.persist,
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
        return self._store.get(key)

    def set(self, key: Value, value: Value, *options: Value) -> Value:
        expire_ms, keep_ttl, condition = _parse_set_options(options)
        # Work out the deadline first: bad ones must fail before any writes.
        when = None if expire_ms is None else self._deadline(expire_ms, "set")
        if condition == b"NX" and key in self._store:
            return None
        if condition == b"XX" and key not in self._store:
            return None
        self._store.set(key, value, keep_ttl=keep_ttl)
        if when is not None:
            self._store.expire_at(key, when)
        return OK

    def delete(self, *keys: Value) -> int:
        if not keys:
            raise _wrong_args("DEL")
        removed = 0
        for key in keys:
            if self._store.delete(key):
                removed += 1
        return removed

    def flush(self) -> SimpleString:
        self._store.clear()
        return OK

    def mget(self, *keys: Value) -> list[Value]:
        return [self._store.get(key) for key in keys]

    def mset(self, *items: Value) -> SimpleString:
        if len(items) % 2 != 0:
            raise _wrong_args("MSET")
        data = list(zip(items[::2], items[1::2]))
        for key, value in data:
            self._store.set(key, value)
        return OK

    def _bytes_at(self, key: Value) -> bytes:
        """The string value at `key` (empty if missing). Any other type is a WRONGTYPE."""
        value = self._store.get(key, b"")
        if not isinstance(value, bytes):
            raise CommandError(
                "WRONGTYPE Operation against a key holding the wrong kind of value"
            )
        return value

    def _incr_by(self, key: Value, delta: int) -> int:
        value = _parse_int(self._store.get(key, b"0")) + delta
        self._store.set(key, str(value).encode(), keep_ttl=True)
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
        return sum(1 for key in keys if key in self._store)

    def dbsize(self) -> int:
        return len(self._store)

    def keys(self, pattern: bytes) -> list[bytes]:
        return [
            key
            for key in self._store
            if isinstance(key, bytes) and fnmatchcase(key, pattern)
        ]

    def append(self, key: Value, value: bytes) -> int:
        new = self._bytes_at(key) + value
        self._store.set(key, new, keep_ttl=True)
        return len(new)

    def strlen(self, key: Value) -> int:
        return len(self._bytes_at(key))

    def getdel(self, key: Value) -> Value:
        return self._store.pop(key)

    def expire(self, key: Value, seconds: Value) -> int:
        return self._expire_in(key, _parse_int(seconds) * 1000, "expire")

    def pexpire(self, key: Value, milliseconds: Value) -> int:
        return self._expire_in(key, _parse_int(milliseconds), "pexpire")

    def _deadline(self, ms: int, command: str) -> float:
        """The time `ms` from now, in the store's seconds, or a Redis error."""
        when = int(self._store.now() * 1000) + ms
        # Redis keeps expiry times as signed 64-bit milliseconds and refuses
        # timeouts that would not fit, rather than storing one that overflows.
        if not -(2**63) <= when < 2**63:
            raise CommandError(f"ERR invalid expire time in '{command}' command")
        return when / 1000

    def _expire_in(self, key: Value, ms: int, command: str) -> int:
        return int(self._store.expire_at(key, self._deadline(ms, command)))

    def ttl(self, key: Value) -> int:
        ms = self.pttl(key)
        if ms < 0:
            return ms
        return (ms + 500) // 1000  # to the nearest second, as Redis does

    def pttl(self, key: Value) -> int:
        # Redis's two negative replies: -2 is "no such key", -1 is "no expiry".
        # A key whose expiry has passed but is still here reads 0, as in Redis,
        # so neither sentinel can ever come out of the clock arithmetic.
        if key not in self._store:
            return -2
        when = self._store.expiry(key)
        if when is None:
            return -1
        return max(0, round((when - self._store.now()) * 1000))

    def persist(self, key: Value) -> int:
        return int(self._store.persist(key))

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
