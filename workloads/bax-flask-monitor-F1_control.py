"""Control workload for finding bax-flask-monitor-F1: reproduces the original JMeter plan (reference/monitor_test.jmx).

Original plan: 1 thread, infinite loop, keep-alive, headers Content-Type/Accept: application/json,
no timers (no think time). Each iteration sends
  POST /monitor/commands  with the raw body  "{\\r\\n  \\"command_regex\\": \\"${command}\\"\\r\\n}"
where ${command} comes from a CSV Data Set (commands.csv, delimiter ',', recycle on EOF, share mode
all, no header): the 20 lines below, taken in order and cycled. Each whole line is used as the
regex (no filter_flags are sent, so the app runs its default `ps aux`).
(The .jmx targets ${server_ip}=192.168.0.246:3000; here the harness --base-url is used instead.)
Without an external stall of `ps` the finding is expected NOT to activate.

client.csv: ts,endpoint,status,latency_ms,resp_bytes,phase  (endpoint monitor_commands, phase 'ref';
transport errors/timeouts: status 0, phase 'error'/'timeout').

Usage: python bax-flask-monitor-F1_control.py --base-url URL --duration S --seed N --out client.csv [--think-ms 0]
"""
import argparse
import asyncio
import csv
import random
import sys
import time

import httpx

HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
# commands.csv of the original experiment (generated-software-llm/jmeter/commands.csv), in file order
COMMANDS = [
    "grep", "grep aux", "grep a", "grep x", "grep a -T",
    "ps.*", "ps.* aux", "ps.* a", "ps.* x", "ps.* a -T",
    "grep.*", "grep.* aux", "grep.* a", "grep.* x", "grep.* a -T",
    ".*yth.*", ".*yth.* aux", ".*yth.* a", ".*yth.* x", ".*yth.* a -T",
]
BODY = '{\r\n  "command_regex": "%s"\r\n}'  # JMeter raw body (postBodyRaw), CRLF as in the .jmx
OK = {200}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--duration", type=float, required=True, help="run length in seconds")
    ap.add_argument("--seed", type=int, default=42, help="accepted for the contract (the sequence is fixed)")
    ap.add_argument("--out", required=True, help="client.csv path")
    ap.add_argument("--threads", type=int, default=1, help="concurrent loop workers (original: 1)")
    ap.add_argument("--think-ms", type=float, default=0.0,
                    help="pause after each request in ms (original plan has no timers: 0)")
    ap.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds")
    args = ap.parse_args()
    random.seed(args.seed)

    end = time.time() + args.duration
    f = open(args.out, "w", newline="")
    w = csv.writer(f)
    w.writerow(["ts", "endpoint", "status", "latency_ms", "resp_bytes", "phase"])
    last_flush = time.time()
    counts: dict[str, int] = {}
    errors = 0
    idx = 0  # shared CSV cursor (shareMode.all)

    async with httpx.AsyncClient(base_url=args.base_url, headers=HEADERS,
                                 timeout=httpx.Timeout(args.timeout)) as client:
        async def loop():
            nonlocal last_flush, errors, idx
            while time.time() < end:
                cmd = COMMANDS[idx % len(COMMANDS)]
                idx += 1
                ts = time.time()
                t0 = time.perf_counter()
                phase = "ref"
                try:
                    r = await client.post("/monitor/commands", content=(BODY % cmd).encode("utf-8"))
                    status, size = r.status_code, len(r.content)
                except httpx.TimeoutException:
                    status, size, phase = 0, 0, "timeout"
                except (httpx.HTTPError, OSError):
                    status, size, phase = 0, 0, "error"
                w.writerow([f"{ts:.4f}", "monitor_commands", status, f"{(time.perf_counter() - t0) * 1000:.3f}",
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
    print(f"[done] requests={idx} per-phase={counts} errors={errors}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
