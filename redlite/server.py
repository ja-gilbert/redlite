"""The gevent TCP server and command dispatch."""

from gevent.pool import Pool
from gevent.server import StreamServer

from .protocol import CommandError, Disconnect, Error, ProtocolHandler


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
            "DEL": self.delete,
            "FLUSHDB": self.flush,
            "FLUSHALL": self.flush,
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

    def delete(self, *keys):
        if not keys:
            raise CommandError("wrong number of arguments for DEL")
        removed = 0
        for key in keys:
            if key in self._kv:
                del self._kv[key]
                removed += 1
        return removed

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
