"""Run the server: `python -m redlite` or, once installed, just `redlite`."""

import argparse
import logging

from gevent import monkey

monkey.patch_all()

from redlite.server import Server


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="redlite", description="A small Redis-style key-value server."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="address to bind (default: %(default)s)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=31337,
        help="port to listen on (default: %(default)s)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    Server(host=args.host, port=args.port).run()


if __name__ == "__main__":
    main()
