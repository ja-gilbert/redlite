"""The RESP wire protocol: parse requests, serialize responses."""

from collections.abc import Callable, Mapping, Sequence
from io import BufferedIOBase, BytesIO
from typing import NamedTuple


# We'll use exceptions to notify the connection-handling loop of problems.
class CommandError(Exception):
    pass


class Error(NamedTuple):
    message: bytes | str


class SimpleString(NamedTuple):
    value: bytes


OK = SimpleString(b"OK")
PONG = SimpleString(b"PONG")

# Everything RESP can carry. Arrays and maps hold Values, so it's recursive.
type Value = (
    bytes
    | str
    | int
    | None
    | Error
    | SimpleString
    | Sequence[Value]
    | Mapping[Value, Value]
)


class Disconnect(Exception):
    """End the connection. If `reply` is given, send that first"""

    def __init__(self, reply: Value = None) -> None:
        super().__init__()
        self.reply = reply


class ProtocolHandler:
    def __init__(self) -> None:
        self.handlers: dict[bytes, Callable[[BufferedIOBase], Value]] = {
            b"+": self.handle_simple_string,
            b"-": self.handle_error,
            b":": self.handle_integer,
            b"$": self.handle_string,
            b"*": self.handle_array,
            b"%": self.handle_dict,
        }

    def handle_request(self, socket_file: BufferedIOBase) -> Value:
        first_byte = socket_file.read(1)
        if not first_byte:
            raise Disconnect()

        try:
            handler = self.handlers[first_byte]
        except KeyError:
            # Not a RESP type byte, so this is an inline command: a plain text
            # line like b"SET some data\r\n", as sent by telnet or redis-benchmark.
            line = first_byte + socket_file.readline()
            return line.split()
        return handler(socket_file)

    def handle_simple_string(self, socket_file: BufferedIOBase) -> bytes:
        return socket_file.readline().rstrip(b"\r\n")

    def handle_error(self, socket_file: BufferedIOBase) -> Error:
        return Error(socket_file.readline().rstrip(b"\r\n"))

    def handle_integer(self, socket_file: BufferedIOBase) -> int:
        return int(socket_file.readline().rstrip(b"\r\n"))

    def handle_string(self, socket_file: BufferedIOBase) -> bytes | None:
        # First read the length ($<length>\r\n).
        length = int(socket_file.readline().rstrip(b"\r\n"))
        if length == -1:
            return None  # Special-case for NULLs.
        length += 2  # Include the trailing \r\n in count.
        return socket_file.read(length)[:-2]

    def handle_array(self, socket_file: BufferedIOBase) -> list[Value]:
        num_elements = int(socket_file.readline().rstrip(b"\r\n"))
        return [self.handle_request(socket_file) for _ in range(num_elements)]

    def handle_dict(self, socket_file: BufferedIOBase) -> dict[Value, Value]:
        num_items = int(socket_file.readline().rstrip(b"\r\n"))
        elements = [self.handle_request(socket_file) for _ in range(num_items * 2)]
        return dict(zip(elements[::2], elements[1::2]))

    def write_response(self, socket_file: BufferedIOBase, data: Value) -> None:
        buf = BytesIO()
        self._write(buf, data)
        buf.seek(0)
        socket_file.write(buf.getvalue())
        socket_file.flush()

    def _write(self, buf: BytesIO, data: Value) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")

        if isinstance(data, bytes):
            buf.write(b"$%d\r\n%s\r\n" % (len(data), data))
        elif isinstance(data, int):
            buf.write(b":%d\r\n" % data)
        elif isinstance(data, Error):
            message = data.message
            if isinstance(message, str):
                message = message.encode("utf-8")
            buf.write(b"-%s\r\n" % message)
        elif isinstance(data, SimpleString):
            buf.write(b"+%s\r\n" % data.value)
        elif isinstance(data, (list, tuple)):
            buf.write(b"*%d\r\n" % len(data))
            for item in data:
                self._write(buf, item)
        elif isinstance(data, dict):
            buf.write(b"%%%d\r\n" % len(data))
            for key in data:
                self._write(buf, key)
                self._write(buf, data[key])
        elif data is None:
            buf.write(b"$-1\r\n")
        else:
            raise CommandError(f"unrecognized type: {type(data)}")
