# redlite

A small Redis-style key-value server in Python, built up from Charles Leifer's [Building a simple Redis server with Python](https://charlesleifer.com/blog/building-a-simple-redis-server-with-python/)
and extended from there

It speaks the RESP wire protocol over a gevent TCP server, stores values as
bytes end-to-end so they're binary-safe, and ships a client that reuses the
same protocol handler. Compatibility with standard Redis clients is the goal,
not yet the state - see the roadmap.

## Running

Start the server:
    
    uv run redlite

Then, in another terminal:

    uv run python

    >>> from redlite import Client
    >>> c = Client(decode_responses=True)
    >>> c.set('hello', 'world')
    1
    >>> c.get('hello')
    'world'

## Roadmap
- [ ] Tests and CI
- [ ] Real Redis command names and replies, verified against redis-cli, redis-benchmark, and redis-py
- [ ] Key expiry (TTL) with lazy and active eviction
- [ ] Append-only-file persistence with crash recovery