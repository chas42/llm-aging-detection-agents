"""Run one diagnostic execution: fresh app copy -> start server -> monitor -> workload -> stop.

Every run gets its own directory under runs/ with all evidence:
    runs/<YYYYmmdd-HHMMSS>_<label>/
        workdir/                 fresh copy of the app (so the database starts empty)
        server.log               app stdout/stderr
        monitor.csv              server-side psutil samples (see harness/monitor.py)
        client.csv               per-request client log written by the workload
        workload.log             workload stdout/stderr
        instrumentation.jsonl    only if --instrument and the app is instrumented
        meta.json                parameters, file hashes, timings, exit codes

Workload contract (what workload scripts must accept):
    python <workload.py> --base-url URL --duration SECONDS --seed N --out client.csv [extra args]
and write client.csv with at least: ts,endpoint,status,latency_ms,resp_bytes

Example:
    .venv/bin/python harness/run_diagnostic.py --app apps/uptime-fastapi/original \\
        --workload workloads/uptime_targeted.py --duration 3600 --label R1_targeted \\
        --instrument -- --distinct-rate 20
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor import Monitor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "bin" / "python")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.sqlite3*", "*.db", ".venv")


def wait_port(port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def pinned(cmd: list[str], cpus: str | None) -> list[str]:
    if cpus and shutil.which("taskset"):
        return ["taskset", "-c", cpus] + cmd
    return cmd


def hash_tree(path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(path.rglob("*.py"))
    }


def stop_process(proc: subprocess.Popen) -> int | None:
    if proc.poll() is not None:
        return proc.returncode
    for sig, wait in ((signal.SIGINT, 15), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        proc.send_signal(sig)
        try:
            return proc.wait(wait)
        except subprocess.TimeoutExpired:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True, help="app directory containing the ASGI module")
    ap.add_argument("--entrypoint", default="app:app", help="uvicorn target (default app:app)")
    ap.add_argument("--workload", required=True)
    ap.add_argument("--duration", type=int, required=True, help="workload duration in seconds")
    ap.add_argument("--label", required=True)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--interval", type=float, default=5.0, help="monitor sampling interval (s)")
    ap.add_argument("--watch", nargs="*", default=["db.sqlite3"], help="files (relative to workdir) whose size is tracked")
    ap.add_argument("--instrument", action="store_true", help="set AGING_INSTRUMENT=1 for the app")
    ap.add_argument("--app-cpus", default=None, help="taskset CPU list for the server, e.g. 0")
    ap.add_argument("--load-cpus", default=None, help="taskset CPU list for the workload, e.g. 1-3")
    ap.add_argument("workload_args", nargs=argparse.REMAINDER, help="extra args after --")
    args = ap.parse_args()
    extra = [a for a in args.workload_args if a != "--"]

    if port_in_use(args.port):
        print(f"port {args.port} already in use; refusing to start", file=sys.stderr)
        return 2

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / f"{stamp}_{args.label}"
    workdir = run_dir / "workdir"
    shutil.copytree(ROOT / args.app, workdir, ignore=IGNORE)

    env = dict(os.environ, APP_SECRET=os.environ.get("APP_SECRET", "supers3cret"), PYTHONUNBUFFERED="1")
    if args.instrument:
        env["AGING_INSTRUMENT"] = "1"
        env["AGING_INSTRUMENT_LOG"] = str(run_dir / "instrumentation.jsonl")

    meta = {
        "label": args.label, "app": args.app, "entrypoint": args.entrypoint,
        "workload": args.workload, "workload_args": extra, "duration_s": args.duration,
        "seed": args.seed, "port": args.port, "monitor_interval_s": args.interval,
        "instrument": args.instrument, "app_cpus": args.app_cpus, "load_cpus": args.load_cpus,
        "app_hashes": hash_tree(workdir),
        "workload_hash": hashlib.sha256((ROOT / args.workload).read_bytes()).hexdigest()[:16],
        "host": {"platform": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()},
    }

    server_log = open(run_dir / "server.log", "w")
    server_cmd = pinned([PYTHON, "-m", "uvicorn", args.entrypoint, "--host", "127.0.0.1",
                         "--port", str(args.port), "--log-level", "warning"], args.app_cpus)
    server = subprocess.Popen(server_cmd, cwd=workdir, env=env, stdout=server_log, stderr=subprocess.STDOUT)
    meta["server_cmd"] = server_cmd

    monitor = None
    try:
        if not wait_port(args.port, 30):
            meta["error"] = "server did not start within 30s (see server.log)"
            print(meta["error"], file=sys.stderr)
            return 1

        monitor = Monitor(server.pid, str(run_dir / "monitor.csv"), args.interval,
                          [str(workdir / w) for w in args.watch])
        monitor.start()

        meta["start"] = time.time()
        workload_cmd = pinned([PYTHON, str(ROOT / args.workload), "--base-url", f"http://127.0.0.1:{args.port}",
                               "--duration", str(args.duration), "--seed", str(args.seed),
                               "--out", str(run_dir / "client.csv")] + extra, args.load_cpus)
        meta["workload_cmd"] = workload_cmd
        print(f"[run] {run_dir.name}: workload running for {args.duration}s ...", flush=True)
        with open(run_dir / "workload.log", "w") as wlog:
            try:
                meta["workload_exit"] = subprocess.run(workload_cmd, stdout=wlog, stderr=subprocess.STDOUT,
                                                       timeout=args.duration + 120).returncode
            except subprocess.TimeoutExpired:
                meta["workload_exit"] = "timeout"
        meta["end"] = time.time()
        meta["server_alive_at_end"] = server.poll() is None
    finally:
        if monitor:
            monitor.stop()
            monitor.join(10)
        meta["server_exit"] = stop_process(server)
        server_log.close()
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        print(f"[run] done -> {run_dir.relative_to(ROOT)}", flush=True)
    return 0 if meta.get("workload_exit") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
