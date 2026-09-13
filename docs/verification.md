# Verified against real Redis tooling

redlite is a RESP2 server. Everything below was run on 2026-09-13 against
the commit that merged PR #9, on WSL2 (Ubuntu 24.04), Python 3.12.14,
gevent 26.8.0.

## redis-cli 7.0.15

Connects with no handshake errors. Session, unedited:

```
alex@AlexLegion:~/src/redlite$ redis-cli -p 31337
127.0.0.1:31337> PING
PONG
127.0.0.1:31337> SET greeting hello
OK
127.0.0.1:31337> GET greeting
"hello"
127.0.0.1:31337> MSET a 1 b 2
OK
127.0.0.1:31337> MGET a b nope
1) "1"
2) "2"
3) (nil)
127.0.0.1:31337> DEL greeting a
(integer) 2
127.0.0.1:31337> GET greeting
(nil)
127.0.0.1:31337> FLUSHDB
OK
127.0.0.1:31337> QUIT
alex@AlexLegion:~/src/redlite$
```

## redis-benchmark

```
redis-benchmark -p 31337 -t ping_inline,ping_mbulk,set,get -n 10000 --csv
```

| Test        | Requests/sec | p50 latency | p99 latency |
|-------------|-------------:|------------:|------------:|
| PING_INLINE |    31,152.65 |    1.679 ms |    4.063 ms |
| PING_MBULK  |    27,173.91 |    1.799 ms |    5.023 ms |
| SET         |    23,923.44 |    2.087 ms |    5.815 ms |
| GET         |    23,809.53 |    2.055 ms |    5.647 ms |

Single-threaded Python on a gevent event loop, no tuning. Real Redis is
roughly an order of magnitude faster on the same hardware; the point of
this number is that the server is correct under concurrent load, not that
it is fast. `redis-benchmark` warns `Could not fetch server CONFIG`
because `CONFIG` is not implemented — the run proceeds regardless.

## redis-py 8.1.0

Covered by `tests/test_redis_py.py`, which runs in CI: `PING`, `SET`/`GET`,
`MSET`/`MGET`, `DEL`, `FLUSHDB`, and server errors surfacing as
`ResponseError`. redis-py 8+ defaults to RESP3 and opens with `HELLO`;
redlite is RESP2, so clients must pass `protocol=2`.

## Known gaps

- RESP2 only. No `HELLO`, no RESP3 types.
- `COMMAND` returns an empty array: enough for clients to connect, but no
  command metadata.
- `CONFIG`, `INFO`, `SELECT` are not implemented.
