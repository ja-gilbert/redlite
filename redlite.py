from collections import namedtuple
from io import BytesIO

from gevent import socket
from gevent.pool import Pool
from gevent.server import StreamServer


# We'll use exceptions to notify the connection-handling loop of problems.
class CommandError(Exception):
    pass


class Disconnect(Exception):
    pass


Error = namedtuple("Error", ("message",))


def _decode(value):
    """Recursively convert bytes to str. Other types pass through untouched."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        return {_decode(k): _decode(v) for k, v in value.items()}
    return value


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


class Server:
    def __init__(self, host="127.0.0.1", port=31337, max_clients=64):
        self._pool = Pool(max_clients)
        self._server = StreamServer(
            (host, port), self.connection_handler, spawn=self._pool
        )

        self._protocol = ProtocolHandler()
        self._kv = {}

        self._commands = self.get_commands()

    def get_commands(self):
        return {
            "GET": self.get,
            "SET": self.set,
            "DELETE": self.delete,
            "FLUSH": self.flush,
            "MGET": self.mget,
            "MSET": self.mset,
        }

    def get_response(self, data):
        if not isinstance(data, list):
            try:
                data = data.split()
            except AttributeError:
                raise CommandError("Request must be a list or a simple string.")

        if not data:
            raise CommandError("Missing command")

        command = data[0]
        if isinstance(command, bytes):
            command = command.decode("utf-8", "replace")
        if not isinstance(command, str):
            raise CommandError("Command name must be a string")
        command = command.upper()

        if command not in self._commands:
            raise CommandError(f"Unrecognized command: {command}")

        try:
            return self._commands[command](*data[1:])
        except TypeError:
            raise CommandError(f"Wrong number of arguments for {command}")

    def get(self, key):
        return self._kv.get(key)

    def set(self, key, value):
        self._kv[key] = value
        return 1

    def delete(self, key):
        if key in self._kv:
            del self._kv[key]
            return 1
        return 0

    def flush(self):
        kvlen = len(self._kv)
        self._kv.clear()
        return kvlen

    def mget(self, *keys):
        return [self._kv.get(key) for key in keys]

    def mset(self, *items):
        if len(items) % 2 != 0:
            raise CommandError("wrong number of arguments for MSET")
        data = list(zip(items[::2], items[1::2]))
        for key, value in data:
            self._kv[key] = value
        return len(data)

    def connection_handler(self, conn, address):
        # Convert "conn" (a socket object) into a file-like object.
        socket_file = conn.makefile("rwb")

        # Process client requests until client disconnects.
        while True:
            try:
                data = self._protocol.handle_request(socket_file)
            except Disconnect:
                break
            except CommandError as exc:
                self._protocol.write_response(socket_file, Error(exc.args[0]))
                continue

            try:
                resp = self.get_response(data)
            except CommandError as exc:
                resp = Error(exc.args[0])

            self._protocol.write_response(socket_file, resp)

    def start(self):
        self._server.start()

    def stop(self):
        self._server.stop()

    @property
    def port(self):
        return self._server.server_port

    def run(self):
        self._server.serve_forever()


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

    def delete(self, key):
        return self.execute("DELETE", key)

    def flush(self):
        return self.execute("FLUSH")

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


if __name__ == "__main__":
    from gevent import monkey

    monkey.patch_all()
    Server().run()
