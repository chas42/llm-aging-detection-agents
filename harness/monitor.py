"""Server-side resource sampler (psutil), the successor of the ps-based collect-data scripts.

Samples one process tree (the app server and its children) at a fixed interval and writes
one CSV row per sample. Also records the size of watched files (e.g. the SQLite database
and its journal/WAL), since disk-backed state is an aging resource too.

Can be used standalone:
    python harness/monitor.py --pid 1234 --out monitor.csv --interval 5 --watch db.sqlite3
"""
import argparse
import csv
import os
import threading
import time

import psutil

FIELDS = [
    "ts", "elapsed_s", "rss_bytes", "vms_bytes", "uss_bytes", "cpu_percent",
    "num_threads", "num_fds", "num_children", "sys_mem_used_bytes", "watched_bytes",
]


def _tree(proc: psutil.Process) -> list[psutil.Process]:
    try:
        return [proc] + proc.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def _watched_size(paths: list[str]) -> int:
    total = 0
    for p in paths:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                total += os.path.getsize(p + suffix)
            except OSError:
                pass
    return total


def sample(proc: psutil.Process, watch: list[str], t0: float) -> dict | None:
    procs = _tree(proc)
    if not procs:
        return None
    row = dict.fromkeys(FIELDS, 0)
    for p in procs:
        try:
            with p.oneshot():
                mem = p.memory_full_info()
                row["rss_bytes"] += mem.rss
                row["vms_bytes"] += mem.vms
                row["uss_bytes"] += mem.uss
                row["cpu_percent"] += p.cpu_percent(None)
                row["num_threads"] += p.num_threads()
                row["num_fds"] += p.num_fds()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    now = time.time()
    row["ts"] = round(now, 3)
    row["elapsed_s"] = round(now - t0, 3)
    row["num_children"] = len(procs) - 1
    row["sys_mem_used_bytes"] = psutil.virtual_memory().used
    row["watched_bytes"] = _watched_size(watch)
    return row


class Monitor(threading.Thread):
    """Background sampler; call stop() and join() to finish."""

    def __init__(self, pid: int, out_path: str, interval: float = 5.0, watch: list[str] | None = None):
        super().__init__(daemon=True)
        self.proc = psutil.Process(pid)
        self.out_path = out_path
        self.interval = interval
        self.watch = watch or []
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        t0 = time.time()
        for p in _tree(self.proc):  # prime cpu_percent so the first sample is meaningful
            try:
                p.cpu_percent(None)
            except psutil.NoSuchProcess:
                pass
        with open(self.out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            while not self._stop_evt.wait(self.interval):
                row = sample(self.proc, self.watch, t0)
                if row is None:
                    break
                writer.writerow(row)
                f.flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--watch", nargs="*", default=[])
    args = ap.parse_args()
    m = Monitor(args.pid, args.out, args.interval, args.watch)
    m.start()
    try:
        m.join()
    except KeyboardInterrupt:
        m.stop()
        m.join()
