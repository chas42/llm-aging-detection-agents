"""Targeted workload for finding bax-flask-monitor-F1 (subprocess.run(ps) without timeout on the request thread).

The trigger is ENVIRONMENTAL: `ps` must stall. HTTP traffic cannot cause it; the campaign enables the
instrumentation's stall injection through the app environment (AGING_INJECT_PS_HANG_PROB=p, see
apps/monitor-flask/instrumented/aging_probe.py). This script only supplies the other half of the
trigger, a steady, realistic stream of valid POST /monitor/commands, and is used unchanged for:
  R0b (matched control): no injection  -> every ps returns, threads/children/FDs stay flat
  R1  (targeted):        injection p    -> ~rate*p requests/s park a thread + child + ~3 FDs for the run

Streams:
  cmd    POST /monitor/commands at a FIXED rate (--rate), OPEN-LOOP: each slot is sent as its own
         asyncio task, so a stalled request never blocks the stream (offered load stays constant).
         The body cycles over a fixed set of 4 (filter_flags, command_regex) queries whose result does
         not depend on how many stalled processes exist (no regex matches the stall wrapper lines), so
         the response size is constant across R0b/R1.
  probe  GET / every --index-interval s (liveness path that never runs ps; should stay flat until
         thread/FD exhaustion).

Stalled requests: a stalled ps never returns within the run, so the request is cut by the client
after --timeout s and recorded as status 0, phase 'timeout' (latency = elapsed time). At most
--max-inflight requests can be outstanding; a slot that finds them all busy is NOT sent and is
recorded as status 0, phase 'dropped' (should never happen with the defaults: r*p*timeout << cap).
Other transport errors: status 0, phase 'error'. Completed requests: phase 'cmd' / 'probe'.
At --duration the stream stops and outstanding requests are allowed to finish or time out (<= timeout s).

client.csv: ts,endpoint,status,latency_ms,resp_bytes,phase  (one row per request slot, streamed).

Usage (workload contract of harness/run_diagnostic.py):
  python bax-flask-monitor-F1_targeted_v1.py --base-url URL --duration S --seed N --out client.csv [options]
"""
import argparse
import asyncio
import csv
import random
import sys
import time

import httpx

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
# Fixed query set (realistic "is my service up" checks). None of the regexes matches the injected
# stall wrapper ("/bin/sh -c setpriv ... sleep ... aging_ps_stall N ps <flags>") or `sleep N`.
QUERIES = [
    {"filter_flags": "aux", "command_regex": "python"},
    {"filter_flags": "-ef", "command_regex": r"app\.py"},
    {"filter_flags": "x", "command_regex": ".*yth.*"},
    {"command_regex": "grep"},                       # default flags (aux)
]


class Log:
    def __init__(self, path: str, flush_every: float = 1.0):
        self.f = open(path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["ts", "endpoint", "status", "latency_ms", "resp_bytes", "phase"])
        self.flush_every = flush_every
        self.last_flush = time.time()
        self.by_phase: dict[str, int] = {}

    def row(self, ts, endpoint, status, latency_ms, resp_bytes, phase):
        self.w.writerow([f"{ts:.4f}", endpoint, status, f"{latency_ms:.3f}", resp_bytes, phase])
        k = f"{endpoint}/{phase}"
        self.by_phase[k] = self.by_phase.get(k, 0) + 1
        now = time.time()
        if now - self.last_flush >= self.flush_every:
            self.f.flush()
            self.last_flush = now

    def close(self):
        self.f.flush()
        self.f.close()


async def request(client: httpx.AsyncClient, log: Log, method: str, path: str, body, endpoint: str,
                  ok_phase: str) -> None:
    ts = time.time()
    t0 = time.perf_counter()
    try:
        if body is None:
            r = await client.request(method, path)
        else:
            r = await client.request(method, path, json=body)
        status, size, phase = r.status_code, len(r.content), ok_phase
    except httpx.TimeoutException:
        status, size, phase = 0, 0, "timeout"
    except (httpx.HTTPError, OSError):
        status, size, phase = 0, 0, "error"
    log.row(ts, endpoint, status, (time.perf_counter() - t0) * 1000.0, size, phase)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--duration", type=float, required=True, help="run length in seconds")
    ap.add_argument("--seed", type=int, default=42, help="seeds the query order (round-robin from a seeded offset)")
    ap.add_argument("--out", required=True, help="client.csv path")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="POST /monitor/commands per second, fixed open-loop schedule (default 2)")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="per-request timeout in s; a stalled request is cut here and logged as phase "
                         "'timeout' (default 10; healthy calls take ~10-100 ms)")
    ap.add_argument("--max-inflight", type=int, default=32,
                    help="cap on outstanding requests; a slot finding the cap reached is logged as "
                         "'dropped' instead of sent (default 32)")
    ap.add_argument("--index-interval", type=float, default=5.0,
                    help="seconds between GET / liveness probes (0 = off; default 5)")
    ap.add_argument("--progress-every", type=float, default=60.0, help="seconds between progress lines on stdout")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    qi = rng.randrange(len(QUERIES))
    log = Log(args.out)
    start = time.time()
    end = start + args.duration
    inflight = set()
    stats = {"slots": 0, "dropped": 0, "late": 0}

    # one connection per outstanding request; a timed-out (stalled) connection is closed by httpx
    limits = httpx.Limits(max_connections=args.max_inflight + 2, max_keepalive_connections=8)
    async with httpx.AsyncClient(base_url=args.base_url, headers=HEADERS,
                                 timeout=httpx.Timeout(args.timeout), limits=limits) as client:

        def launch(coro):
            t = asyncio.create_task(coro)
            inflight.add(t)
            t.add_done_callback(inflight.discard)

        async def cmd_stream():
            nonlocal qi
            interval = 1.0 / args.rate
            nxt = time.time()
            while True:
                now = time.time()
                if nxt < now - interval:      # client fell behind: skip missed slots, no burst
                    stats["late"] += int((now - nxt) / interval)
                    nxt = now
                if nxt > now:
                    await asyncio.sleep(nxt - now)
                if nxt >= end:
                    return
                body = QUERIES[qi % len(QUERIES)]
                qi += 1
                stats["slots"] += 1
                if len(inflight) >= args.max_inflight:
                    stats["dropped"] += 1
                    log.row(time.time(), "monitor_commands", 0, 0.0, 0, "dropped")
                else:
                    launch(request(client, log, "POST", "/monitor/commands", body, "monitor_commands", "cmd"))
                nxt += interval

        async def index_stream():
            nxt = time.time() + args.index_interval / 2
            while True:
                if nxt > time.time():
                    await asyncio.sleep(nxt - time.time())
                if nxt >= end:
                    return
                if len(inflight) < args.max_inflight:
                    launch(request(client, log, "GET", "/", None, "index", "probe"))
                else:
                    log.row(time.time(), "index", 0, 0.0, 0, "dropped")
                nxt = max(nxt + args.index_interval, time.time())

        async def progress():
            while time.time() < end:
                await asyncio.sleep(min(args.progress_every, max(0.0, end - time.time())))
                el = time.time() - start
                print(f"[{el:7.0f}s] slots={stats['slots']} inflight={len(inflight)} dropped={stats['dropped']} "
                      f"late={stats['late']} rows={log.by_phase}", flush=True)

        streams = [asyncio.create_task(cmd_stream())]
        if args.index_interval > 0:
            streams.append(asyncio.create_task(index_stream()))
        prog = asyncio.create_task(progress())
        await asyncio.gather(*streams)
        # drain: outstanding requests either complete or hit their own timeout
        if inflight:
            await asyncio.wait(set(inflight), timeout=args.timeout + 5)
        for t in list(inflight):
            t.cancel()
        await asyncio.gather(*inflight, return_exceptions=True)
        prog.cancel()
        await asyncio.gather(prog, return_exceptions=True)

    log.close()
    print(f"[done] slots={stats['slots']} dropped={stats['dropped']} late={stats['late']} rows={log.by_phase}",
          flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
