"""Targeted workload for finding uptime-fastapi-F4 (unbounded per-token heartbeat rows + unpaginated read).

Trigger (from the card): POST /heartbeat with *distinct* serviceId values. Symptom: cost of
POST /services for the token whose service set grows (latency, response size), db file size.

Streams (all paced, closed-loop, no client backlog):
  grow     POST /heartbeat with a never-seen serviceId for a growing token, at --new-key-rate/s
           (serviceIds are counter-based: <token>-svc-0000001, ...; deterministic).
  refresh  POST /heartbeat re-sending an already-registered serviceId of a growing token
           (ON CONFLICT update path; realistic "service still alive"; does not add rows).
  ref      POST /heartbeat for the fixed services of the reference tokens (constant row count).
  prime    one-off registration of the reference tokens' fixed services at start-up.
  probe    POST /services for each growing token every --probe-interval s     (endpoint services_grow[N])
  refread  POST /services for each reference token every --probe-interval s  (endpoint services_ref<size>)

client.csv: ts,endpoint,status,latency_ms,resp_bytes,phase  (one row per request, streamed).

Usage (workload contract of harness/run_diagnostic.py):
  python uptime-fastapi-F4_targeted_v1.py --base-url URL --duration S --seed N --out client.csv [options]
"""
import argparse
import asyncio
import csv
import random
import sys
import time

import httpx

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


class Pacer:
    """Shared slot scheduler: hands out send times spaced 1/rate apart.

    If the consumers fall behind (server slower than the target rate), missed slots are
    dropped instead of being replayed in a burst, so degradation shows up as lower achieved
    throughput rather than as a client-side backlog.
    """

    def __init__(self, rate: float, start: float):
        self.interval = 1.0 / rate
        self.next = start
        self.lock = asyncio.Lock()

    async def wait_slot(self) -> None:
        async with self.lock:
            now = time.time()
            if self.next < now - self.interval:
                self.next = now
            slot = self.next
            self.next += self.interval
        delay = slot - time.time()
        if delay > 0:
            await asyncio.sleep(delay)


class Log:
    def __init__(self, path: str, flush_every: float = 1.0):
        self.f = open(path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["ts", "endpoint", "status", "latency_ms", "resp_bytes", "phase"])
        self.flush_every = flush_every
        self.last_flush = time.time()
        self.counts: dict[str, int] = {}
        self.errors = 0

    def row(self, ts, endpoint, status, latency_ms, resp_bytes, phase):
        self.w.writerow([f"{ts:.4f}", endpoint, status, f"{latency_ms:.3f}", resp_bytes, phase])
        self.counts[endpoint] = self.counts.get(endpoint, 0) + 1
        if status != 200:
            self.errors += 1
        now = time.time()
        if now - self.last_flush >= self.flush_every:
            self.f.flush()
            self.last_flush = now

    def close(self):
        self.f.flush()
        self.f.close()


async def post(client: httpx.AsyncClient, log: Log, path: str, body: dict, endpoint: str, phase: str) -> int:
    ts = time.time()
    t0 = time.perf_counter()
    try:
        r = await client.post(path, json=body)
        status, size = r.status_code, len(r.content)
    except (httpx.HTTPError, OSError):
        status, size = 0, 0
    log.row(ts, endpoint, status, (time.perf_counter() - t0) * 1000.0, size, phase)
    return status


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--duration", type=float, required=True, help="run length in seconds")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", required=True, help="client.csv path")
    ap.add_argument("--grow-tokens", type=int, default=1,
                    help="number of tokens whose service set grows (default 1)")
    ap.add_argument("--token-prefix", default="tenant-grow-", help="growing token name prefix")
    ap.add_argument("--new-key-rate", type=float, default=10.0,
                    help="target distinct-serviceId heartbeats per second, over all growing tokens (default 10)")
    ap.add_argument("--grow-workers", type=int, default=2, help="concurrent workers for the grow stream")
    ap.add_argument("--key-space", type=int, default=0,
                    help="0 = every grow heartbeat uses a new serviceId (targeted, default); N > 0 = the grow "
                         "stream cycles over N fixed serviceIds per token at the same rate (matched control: "
                         "same load, no accumulation; phase label 'cycle')")
    ap.add_argument("--refresh-rate", type=float, default=2.0,
                    help="re-heartbeats of already-registered growing services per second (0 = off; default 2)")
    ap.add_argument("--ref-sizes", default="4,200",
                    help="comma list: one reference token per entry, with that constant number of services (default 4,200)")
    ap.add_argument("--ref-hb-rate", type=float, default=2.0,
                    help="heartbeats per second cycling over all reference services (0 = off; default 2)")
    ap.add_argument("--probe-interval", type=float, default=2.0,
                    help="seconds between POST /services reads, per probed token (growing and reference; default 2)")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds")
    ap.add_argument("--progress-every", type=float, default=60.0, help="seconds between progress lines on stdout")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    log = Log(args.out)
    start = time.time()
    end = start + args.duration

    grow_tokens = [f"{args.token_prefix}{i + 1}" for i in range(args.grow_tokens)]
    ref_sizes = [int(x) for x in args.ref_sizes.split(",") if x.strip()]
    ref_tokens = [(f"tenant-ref-{s}", s) for s in ref_sizes]
    ref_services = [(tok, f"{tok}-svc-{j:04d}") for tok, s in ref_tokens for j in range(1, s + 1)]
    grow_ep = {t: ("services_grow" if len(grow_tokens) == 1 else f"services_grow{i + 1}")
               for i, t in enumerate(grow_tokens)}

    # per growing token: number of distinct serviceIds sent so far (key space size from the client's view)
    sent = {t: 0 for t in grow_tokens}
    ok = {t: 0 for t in grow_tokens}
    counter = {"n": 0}

    limits = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    async with httpx.AsyncClient(base_url=args.base_url, headers=HEADERS,
                                 timeout=httpx.Timeout(args.timeout), limits=limits) as client:

        # prime: register the reference tokens' fixed services (constant row count afterwards)
        for tok, sid in ref_services:
            if time.time() >= end:
                break
            await post(client, log, "/heartbeat", {"serviceId": sid, "token": tok}, "heartbeat", "prime")

        t0 = time.time()

        async def grow_worker():
            pacer = grow_pacer
            while True:
                await pacer.wait_slot()
                if time.time() >= end:
                    return
                n = counter["n"]
                counter["n"] += 1
                tok = grow_tokens[n % len(grow_tokens)]
                sent[tok] += 1
                idx = sent[tok] if args.key_space <= 0 else (sent[tok] - 1) % args.key_space + 1
                sid = f"{tok}-svc-{idx:07d}"
                if await post(client, log, "/heartbeat", {"serviceId": sid, "token": tok},
                              "heartbeat", "grow" if args.key_space <= 0 else "cycle") == 200:
                    ok[tok] += 1

        async def refresh_worker():
            pacer = Pacer(args.refresh_rate, t0)
            while True:
                await pacer.wait_slot()
                if time.time() >= end:
                    return
                tok = grow_tokens[rng.randrange(len(grow_tokens))]
                if sent[tok] == 0:
                    continue
                known = sent[tok] if args.key_space <= 0 else min(sent[tok], args.key_space)
                sid = f"{tok}-svc-{rng.randint(1, known):07d}"
                await post(client, log, "/heartbeat", {"serviceId": sid, "token": tok}, "heartbeat", "refresh")

        async def ref_hb_worker():
            pacer = Pacer(args.ref_hb_rate, t0)
            i = 0
            while True:
                await pacer.wait_slot()
                if time.time() >= end:
                    return
                tok, sid = ref_services[i % len(ref_services)]
                i += 1
                await post(client, log, "/heartbeat", {"serviceId": sid, "token": tok}, "heartbeat", "ref")

        async def probe_worker(token: str, endpoint: str, phase: str, offset: float):
            # one dedicated closed-loop worker per probed token: fixed rate, fixed parameters
            pacer = Pacer(1.0 / args.probe_interval, t0 + offset)
            while True:
                await pacer.wait_slot()
                if time.time() >= end:
                    return
                await post(client, log, "/services", {"token": token}, endpoint, phase)

        async def progress():
            while time.time() < end:
                await asyncio.sleep(min(args.progress_every, max(0.0, end - time.time())))
                el = time.time() - start
                keys = ", ".join(f"{t}: sent={sent[t]} ok={ok[t]}" for t in grow_tokens)
                print(f"[{el:7.0f}s] {keys} | requests={sum(log.counts.values())} errors={log.errors}",
                      flush=True)

        grow_pacer = Pacer(args.new_key_rate, t0)
        tasks = [asyncio.create_task(grow_worker()) for _ in range(args.grow_workers)]
        if args.refresh_rate > 0:
            tasks.append(asyncio.create_task(refresh_worker()))
        if args.ref_hb_rate > 0 and ref_services:
            tasks.append(asyncio.create_task(ref_hb_worker()))
        probes = [(t, grow_ep[t], "probe") for t in grow_tokens] + \
                 [(tok, f"services_ref{s}", "refread") for tok, s in ref_tokens]
        for k, (tok, ep, ph) in enumerate(probes):
            # stagger probes inside the interval so they do not all hit the lock together
            tasks.append(asyncio.create_task(
                probe_worker(tok, ep, ph, args.probe_interval * k / max(1, len(probes)))))
        prog = asyncio.create_task(progress())

        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=max(1.0, end - time.time()) + args.timeout + 5)
        except asyncio.TimeoutError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        prog.cancel()
        await asyncio.gather(prog, return_exceptions=True)

    log.close()
    keys = ", ".join(f"{t}: sent={sent[t]} ok={ok[t]}" for t in grow_tokens)
    print(f"[done] {keys} | per-endpoint={log.counts} errors={log.errors}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
