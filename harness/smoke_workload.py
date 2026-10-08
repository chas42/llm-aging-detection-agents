"""Minimal workload used only to smoke-test the harness itself (not an experiment).

Follows the workload contract of run_diagnostic.py: sends heartbeats for 4 fixed services
and reads /services, sequentially, logging every request to client.csv.
"""
import argparse
import csv
import random
import time

import requests

ap = argparse.ArgumentParser()
ap.add_argument("--base-url", required=True)
ap.add_argument("--duration", type=float, required=True)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--out", required=True)
args = ap.parse_args()
random.seed(args.seed)

end = time.time() + args.duration
with open(args.out, "w", newline="") as f, requests.Session() as s:
    w = csv.writer(f)
    w.writerow(["ts", "endpoint", "status", "latency_ms", "resp_bytes"])
    while time.time() < end:
        i = random.randint(1, 4)
        for ep, body in (("heartbeat", {"serviceId": f"svc{i}", "token": f"pass{i}"}),
                         ("services", {"token": f"pass{i}"})):
            t = time.time()
            try:
                r = s.post(f"{args.base_url}/{ep}", json=body, timeout=10)
                status, size = r.status_code, len(r.content)
            except requests.RequestException:
                status, size = 0, 0
            w.writerow([round(t, 4), ep, status, round((time.time() - t) * 1000, 3), size])
        time.sleep(0.05)
