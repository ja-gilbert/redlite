# Verified against real Redis tooling

redlite is a RESP2 server. The redis-cli and redis-py sections were run on
2026-09-13 against the commit that merged PR #9, on WSL2 (Ubuntu 24.04),
Python 3.12.14, gevent 26.8.0.

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

Run on 2026-11-05 against commit 45972e9: Intel Core i9-14900HX with 32 logical
CPUs, WSL2 kernel 6.18, Python 3.12.14, gevent 26.8.0, redis-benchmark 7.0.15.
The server was started with `uv run redlite`; client and server ran on the same
machine over loopback. 50 parallel connections, no pipelining, three runs back
to back.

```
redis-benchmark -p 31337 -t ping_inline,ping_mbulk,set,get -n 10000 -c 50 --csv
```

Median of the three runs, each column taken separately:

| Test        | Requests/sec | p50 latency | p99 latency |
|-------------|-------------:|------------:|------------:|
| PING_INLINE |    33,444.82 |    1.535 ms |    2.447 ms |
| PING_MBULK  |    26,315.79 |    1.991 ms |    2.919 ms |
| SET         |    22,371.37 |    2.359 ms |    3.351 ms |
| GET         |    24,691.36 |    2.143 ms |    2.903 ms |

Requests/sec in each run:

| Test        |     Run 1 |     Run 2 |     Run 3 |
|-------------|----------:|----------:|----------:|
| PING_INLINE | 36,101.08 | 33,444.82 | 29,498.53 |
| PING_MBULK  | 31,645.57 | 26,315.79 | 25,510.21 |
| SET         | 22,573.36 | 22,371.37 | 21,276.60 |
| GET         | 32,258.06 | 24,691.36 | 22,727.27 |

The server is single-threaded Python on a gevent event loop, with no tuning; its
process had a single OS thread when checked after the third run. Runs vary
widely, so read the medians rather than any single run. The point of these
numbers is that the server stays correct under 50 concurrent clients, not that
it is fast. No redis-server was available on this machine, so there is no
side-by-side comparison with real Redis. `redis-benchmark` warns `Could not
fetch server CONFIG` because `CONFIG` is not implemented; the run proceeds
regardless.

### Earlier run, 2026-09-13

Against the commit that merged PR #9, before key expiry landed. The command had
no `-c`; redis-benchmark's default is 50 parallel connections, the same setting
as the run above.

```
redis-benchmark -p 31337 -t ping_inline,ping_mbulk,set,get -n 10000 --csv
```

| Test        | Requests/sec | p50 latency | p99 latency |
|-------------|-------------:|------------:|------------:|
| PING_INLINE |    31,152.65 |    1.679 ms |    4.063 ms |
| PING_MBULK  |    27,173.91 |    1.799 ms |    5.023 ms |
| SET         |    23,923.44 |    2.087 ms |    5.815 ms |
| GET         |    23,809.53 |    2.055 ms |    5.647 ms |

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
