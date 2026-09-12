"""The RESP wire protocol: parse requests, serialize responses."""

from collections import namedtuple
from io import BytesIO


# We'll use exceptions to notify the connection-handling loop of problems.
class CommandError(Exception):
    pass


class Disconnect(Exception):
    pass


Error = namedtuple("Error", ("message",))
SimpleString = namedtuple("SimpleString", ("value",))
OK = SimpleString(b"OK")


class ProtocolHandler:
    def __init__(self):
        self.handlers = {
            b"+": self.handle_simple_string,
            b"-": self.handle_error,
            b":": self.handle_integer,
            b"$": self.handle_string,
            b"*": self.handle_array,
            b"%": self.handle_dict,
        }

    def handle_request(self, socket_file):
        first_byte = socket_file.read(1)
        if not first_byte:
            raise Disconnect()

        try:
            handler = self.handlers[first_byte]
        except KeyError:
            socket_file.readline()  # discard the rest of the malformed line
            raise CommandError("Bad request")
        return handler(socket_file)

    def handle_simple_string(self, socket_file):
        return socket_file.readline().rstrip(b"\r\n")

    def handle_error(self, socket_file):
        return Error(socket_file.readline().rstrip(b"\r\n"))

    def handle_integer(self, socket_file):
        return int(socket_file.readline().rstrip(b"\r\n"))

    def handle_string(self, socket_file):
        # First read the length ($<length>\r\n).
        length = int(socket_file.readline().rstrip(b"\r\n"))
        if length == -1:
            return None  # Special-case for NULLs.
        length += 2  # Include the trailing \r\n in count.
        return socket_file.read(length)[:-2]

    def handle_array(self, socket_file):
        num_elements = int(socket_file.readline().rstrip(b"\r\n"))
        return [self.handle_request(socket_file) for _ in range(num_elements)]

    def handle_dict(self, socket_file):
        num_items = int(socket_file.readline().rstrip(b"\r\n"))
        elements = [self.handle_request(socket_file) for _ in range(num_items * 2)]
        return dict(zip(elements[::2], elements[1::2]))

    def write_response(self, socket_file, data):
        buf = BytesIO()
        self._write(buf, data)
        buf.seek(0)
        socket_file.write(buf.getvalue())
        socket_file.flush()

    def _write(self, buf, data):
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
