"""Targeted workload for finding bax-flask-image-converter-F1 (`convert` run with no timeout on the request thread).

The trigger is ENVIRONMENTAL: `convert` must stall. HTTP traffic cannot cause it. The campaign puts a
PATH shim first on the app's PATH (env/shims/convert-hang/convert, enabled with CONVERT_HANG_PROB=p)
that makes a fraction p of the conversions stall for CONVERT_HANG_S s (default 36000, longer than the
run) before running the real convert. This script only supplies the other half of the trigger: a
steady, realistic stream of valid POST /create-gif requests. It is used unchanged for:
  R0b (matched control): no shim -> every convert returns, threads/children/FDs/temp dirs stay flat
  R1  (targeted):        shim p  -> ~rate*p requests/s park a thread + convert child + pipe/socket FDs
                                    + a gif_creator_* temp dir with the saved uploads, for the whole run

Streams:
  gif    POST /create-gif at a FIXED rate (--rate), OPEN-LOOP: each slot is sent as its own asyncio task,
         so a stalled request never blocks the stream (offered load stays constant). The body cycles
         round-robin (seeded start) over a fixed set of 4 valid requests (REQUESTS below: 2-5 images from
         env/images, fixed targetSize/delay/appendReverted), so every slot costs the same work in R0b
         and R1 and the response size per request kind is constant.
  probe  GET / every --index-interval s (the HTML form; never runs convert; should stay flat until
         thread/FD/memory exhaustion).

Stalled requests: a stalled convert never returns within the run, so the request is cut by the client
after --timeout s and recorded as status 0, phase 'timeout' (latency = elapsed time). At most
--max-inflight requests can be outstanding; a slot that finds them all busy is NOT sent and is
recorded as status 0, phase 'dropped' (should never happen with the defaults: r*p*timeout << cap).
Other transport errors: status 0, phase 'error'. Completed requests: phase 'gif' / 'probe'.
At --duration the stream stops and outstanding requests are allowed to finish or time out (<= timeout s).

client.csv: ts,endpoint,status,latency_ms,resp_bytes,phase  (one row per request slot, streamed).

Usage (workload contract of harness/run_diagnostic.py):
  python bax-flask-image-converter-F1_targeted_v1.py --base-url URL --duration S --seed N --out client.csv [options]
"""
import argparse
import asyncio
import csv
import random
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "env" / "images"

# Fixed request set: realistic small GIFs (thumbnail-sized animations of a few pet photos).
# (images relative to env/images, targetSize, delay ms, appendReverted). Frames = n (+ n-2 or +1 if reverted).
REQUESTS = [
    (["Abyssinian/Abyssinian_test_2.jpg", "beagle/beagle_test_3.jpg",
      "Bengal/Bengal_test_4.jpg", "basset_hound/basset_hound_test_5.jpg"], "320x240", 100, False),
    (["american_bulldog/american_bulldog_test_1.jpg", "american_bulldog/american_bulldog_test_2.jpg",
      "american_bulldog/american_bulldog_test_3.jpg"], "240x240", 200, True),
    (["beagle/beagle_test_1.jpg", "beagle/beagle_test_6.jpg", "beagle/beagle_test_7.jpg",
      "Bengal/Bengal_test_8.jpg", "Abyssinian/Abyssinian_test_9.jpg"], "320x320", 150, False),
    (["american_pit_bull_terrier/american_pit_bull_terrier_test_4.jpg",
      "basset_hound/basset_hound_test_1.jpg"], "400x300", 500, True),
]


def load_requests() -> list[tuple[list[tuple[str, bytes]], dict]]:
    """Read the fixed request set once (files kept in memory, ~1.7 MB total)."""
    out = []
    for files, size, delay, rev in REQUESTS:
        parts = [(Path(f).name, (IMAGES / f).read_bytes()) for f in files]
        data = {"targetSize": size, "delay": str(delay), "appendReverted": "true" if rev else "false"}
        out.append((parts, data))
    return out


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


async def request(client: httpx.AsyncClient, log: Log, endpoint: str, ok_phase: str, method: str, path: str,
                  req=None) -> None:
    ts = time.time()
    t0 = time.perf_counter()
    try:
        if req is None:
            r = await client.request(method, path)
        else:
            parts, data = req
            files = [("images", (name, blob, "image/jpeg")) for name, blob in parts]
            r = await client.request(method, path, data=data, files=files)
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
    ap.add_argument("--seed", type=int, default=42, help="seeds the request order (round-robin from a seeded offset)")
    ap.add_argument("--out", required=True, help="client.csv path")
    ap.add_argument("--rate", type=float, default=1.0,
                    help="POST /create-gif per second, fixed open-loop schedule (default 1)")
    ap.add_argument("--timeout", type=float, default=20.0,
                    help="per-request timeout in s; a stalled request is cut here and logged as phase "
                         "'timeout' (default 20; healthy calls take ~0.2-0.6 s)")
    ap.add_argument("--max-inflight", type=int, default=32,
                    help="cap on outstanding requests; a slot finding the cap reached is logged as "
                         "'dropped' instead of sent (default 32)")
    ap.add_argument("--index-interval", type=float, default=5.0,
                    help="seconds between GET / liveness probes (0 = off; default 5)")
    ap.add_argument("--progress-every", type=float, default=60.0, help="seconds between progress lines on stdout")
    args = ap.parse_args()

    reqs = load_requests()
    rng = random.Random(args.seed)
    qi = rng.randrange(len(reqs))
    log = Log(args.out)
    start = time.time()
    end = start + args.duration
    inflight = set()
    stats = {"slots": 0, "dropped": 0, "late": 0}

    # one connection per outstanding request; a timed-out (stalled) connection is closed by httpx
    limits = httpx.Limits(max_connections=args.max_inflight + 2, max_keepalive_connections=8)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(args.timeout),
                                 limits=limits) as client:

        def launch(coro):
            t = asyncio.create_task(coro)
            inflight.add(t)
            t.add_done_callback(inflight.discard)

        async def gif_stream():
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
                req = reqs[qi % len(reqs)]
                qi += 1
                stats["slots"] += 1
                if len(inflight) >= args.max_inflight:
                    stats["dropped"] += 1
                    log.row(time.time(), "create_gif", 0, 0.0, 0, "dropped")
                else:
                    launch(request(client, log, "create_gif", "gif", "POST", "/create-gif", req))
                nxt += interval

        async def index_stream():
            nxt = time.time() + args.index_interval / 2
            while True:
                if nxt > time.time():
                    await asyncio.sleep(nxt - time.time())
                if nxt >= end:
                    return
                if len(inflight) < args.max_inflight:
                    launch(request(client, log, "index", "probe", "GET", "/"))
                else:
                    log.row(time.time(), "index", 0, 0.0, 0, "dropped")
                nxt = max(nxt + args.index_interval, time.time())

        async def progress():
            while time.time() < end:
                await asyncio.sleep(min(args.progress_every, max(0.0, end - time.time())))
                el = time.time() - start
                print(f"[{el:7.0f}s] slots={stats['slots']} inflight={len(inflight)} dropped={stats['dropped']} "
                      f"late={stats['late']} rows={log.by_phase}", flush=True)

        streams = [asyncio.create_task(gif_stream())]
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
