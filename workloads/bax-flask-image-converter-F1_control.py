"""Control workload for finding bax-flask-image-converter-F1: reproduces the original JMeter plan
(reference/image_converter.jmx).

Original plan: 1 thread group, 10 threads (ramp-up 0.1 s), infinite loop, keep-alive, no timers (no think
time). Each iteration sends one sampler, "5_images":
  POST /create-gif  multipart/form-data
    images      x5: one photo per breed, in this order: Abyssinian, american_bulldog,
                american_pit_bull_terrier, basset_hound, beagle, file <breed>_test_${<breed>_random}.jpg
                (the .jmx declares mimetype image/png for the first two files and image/jpeg for the rest)
    targetSize  ${width_random}x${height_random}   width, height uniform in [400, 800]
    delay       ${delay_random}                    uniform in [10, 100]
  (no appendReverted field: the app's default, false)
The .jmx random variables are shared by all threads (perThread=false) and unseeded; here one shared
RNG seeded with --seed. The .jmx draws each <breed>_random in [1, 200] over the paper's full dataset;
the bundle only ships the first 10 images per breed (env/images, see its README), so the index is drawn
in [1, 10] instead (same sampling, smaller pool). The .jmx targets ${server_ip}:3000; here --base-url.
Without an external stall of `convert` the finding is expected NOT to activate.

client.csv: ts,endpoint,status,latency_ms,resp_bytes,phase  (endpoint create_gif, phase 'ref';
transport errors/timeouts: status 0, phase 'error'/'timeout').

Usage: python bax-flask-image-converter-F1_control.py --base-url URL --duration S --seed N --out client.csv
       [--threads 10] [--think-ms 0] [--timeout 120] [--pool 10]
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
# breeds in sampler order, with the mimetype the .jmx declares for each file
BREEDS = [("Abyssinian", "image/png"), ("american_bulldog", "image/png"),
          ("american_pit_bull_terrier", "image/jpeg"), ("basset_hound", "image/jpeg"), ("beagle", "image/jpeg")]
OK = {200}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--duration", type=float, required=True, help="run length in seconds")
    ap.add_argument("--seed", type=int, default=42, help="seeds the shared random variables")
    ap.add_argument("--out", required=True, help="client.csv path")
    ap.add_argument("--threads", type=int, default=10, help="concurrent loop workers (original: 10)")
    ap.add_argument("--think-ms", type=float, default=0.0,
                    help="pause after each request in ms (original plan has no timers: 0)")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="per-request timeout in seconds (JMeter had none; 120 s >> normal latency)")
    ap.add_argument("--pool", type=int, default=10,
                    help="images per breed to sample from (env/images holds 10; the .jmx used 200)")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    # all candidate files read once (60 images, ~7.5 MB)
    blobs = {}
    for breed, _mime in BREEDS:
        for i in range(1, args.pool + 1):
            name = f"{breed}_test_{i}.jpg"
            blobs[(breed, i)] = (name, (IMAGES / breed / name).read_bytes())

    end = time.time() + args.duration
    f = open(args.out, "w", newline="")
    w = csv.writer(f)
    w.writerow(["ts", "endpoint", "status", "latency_ms", "resp_bytes", "phase"])
    last_flush = time.time()
    counts: dict[str, int] = {}
    errors = 0
    sent = 0

    def draw():
        """One iteration's random variables (shared generator, as perThread=false)."""
        files = []
        for breed, mime in BREEDS:
            name, blob = blobs[(breed, rng.randint(1, args.pool))]
            files.append(("images", (name, blob, mime)))
        data = {"targetSize": f"{rng.randint(400, 800)}x{rng.randint(400, 800)}",
                "delay": str(rng.randint(10, 100))}
        return files, data

    limits = httpx.Limits(max_connections=args.threads, max_keepalive_connections=args.threads)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(args.timeout),
                                 limits=limits) as client:
        async def loop():
            nonlocal last_flush, errors, sent
            while time.time() < end:
                files, data = draw()
                sent += 1
                ts = time.time()
                t0 = time.perf_counter()
                phase = "ref"
                try:
                    r = await client.post("/create-gif", data=data, files=files)
                    status, size = r.status_code, len(r.content)
                except httpx.TimeoutException:
                    status, size, phase = 0, 0, "timeout"
                except (httpx.HTTPError, OSError):
                    status, size, phase = 0, 0, "error"
                w.writerow([f"{ts:.4f}", "create_gif", status, f"{(time.perf_counter() - t0) * 1000:.3f}",
                            size, phase])
                counts[phase] = counts.get(phase, 0) + 1
                errors += status not in OK
                if time.time() - last_flush >= 1.0:
                    f.flush()
                    last_flush = time.time()
                if args.think_ms > 0:
                    await asyncio.sleep(args.think_ms / 1000.0)

        await asyncio.gather(*(loop() for _ in range(args.threads)))

    f.flush()
    f.close()
    print(f"[done] requests={sent} per-phase={counts} errors={errors}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
