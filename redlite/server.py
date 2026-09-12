"""The gevent TCP server and command dispatch."""

from gevent.pool import Pool
from gevent.server import StreamServer

from .protocol import OK, PONG, CommandError, Disconnect, Error, ProtocolHandler


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
            "QUIT": self.quit,
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

    def ping(self):
        return PONG

    def echo(self, message):
        return message

    def command(self, *args):
        # Real Redis would describe every command here. Clients only need an
        # array to proceed, so an empty one keeps redis-cli's handshake quiet
        return []

    def quit(self):
        raise Disconnect(reply=OK)

    def get(self, key):
        return self._kv.get(key)

    def set(self, key, value):
        self._kv[key] = value
        return OK

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
        self._kv.clear()
        return OK

    def mget(self, *keys):
        return [self._kv.get(key) for key in keys]

    def mset(self, *items):
        if len(items) % 2 != 0:
            raise CommandError("wrong number of arguments for MSET")
        data = list(zip(items[::2], items[1::2]))
        for key, value in data:
            self._kv[key] = value
        return OK

    def connection_handler(self, conn, address):
        # Convert "conn" (a socket object) into a file-like object.
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
