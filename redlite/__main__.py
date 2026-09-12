"""Run the server: `python -m redlite` or, once installed, just `redlite`."""

from gevent import monkey

monkey.patch_all()

from redlite.server import Server


def main():
    Server().run()


if __name__ == "__main__":
    main()
