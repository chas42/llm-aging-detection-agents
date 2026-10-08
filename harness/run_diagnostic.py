"""Run one diagnostic execution: fresh app copy -> start server -> monitor -> workload -> stop.

Every run gets its own directory under runs/ with all evidence:
    runs/<YYYYmmdd-HHMMSS>_<label>/
        workdir/                 fresh copy of the app (so the database starts empty)
        server.log               app stdout/stderr
        monitor.csv              server-side psutil samples (see harness/monitor.py)
        client.csv               per-request client log written by the workload
        client_monitor.csv       psutil samples of the load generator itself (client saturation check)
        workload.log             workload stdout/stderr
        instrumentation.jsonl    only if --instrument and the app is instrumented
        meta.json                parameters, file hashes, timings, exit codes

Workload contract (what workload scripts must accept):
    python <workload.py> --base-url URL --duration SECONDS --seed N --out client.csv [extra args]
and write client.csv with at least: ts,endpoint,status,latency_ms,resp_bytes

Two-machine mode (server and load generator on different hosts): harness/server_agent.py on the
server + harness/run_remote.py on the client; both reuse start_app()/run_workload() from here.

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


def new_run_dir(label: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / f"{stamp}_{label}"
    run_dir.mkdir(parents=True)
    return run_dir


def resolve_app(app: str) -> Path:
    """App directories must live under apps/ (also guards the remote agent against path traversal)."""
    path = (ROOT / app).resolve()
    if not path.is_dir() or (ROOT / "apps").resolve() not in path.parents:
        raise ValueError(f"app dir must be an existing directory under apps/: {app}")
    return path


def start_app(run_dir: Path, app: str, entrypoint: str, host: str, port: int, instrument: bool,
              cpus: str | None) -> tuple[subprocess.Popen, dict]:
    """Copy the app into run_dir/workdir (fresh database) and start uvicorn. Shared by the local
    mode below and by harness/server_agent.py (two-machine mode)."""
    workdir = run_dir / "workdir"
    shutil.copytree(resolve_app(app), workdir, ignore=IGNORE)
    env = dict(os.environ, APP_SECRET=os.environ.get("APP_SECRET", "supers3cret"), PYTHONUNBUFFERED="1")
    if instrument:
        env["AGING_INSTRUMENT"] = "1"
        env["AGING_INSTRUMENT_LOG"] = str(run_dir / "instrumentation.jsonl")
    cmd = pinned([PYTHON, "-m", "uvicorn", entrypoint, "--host", host, "--port", str(port),
                  "--log-level", "warning"], cpus)
    with open(run_dir / "server.log", "w") as log:  # the child keeps its own descriptor
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=log, stderr=subprocess.STDOUT)
    info = {"app": app, "entrypoint": entrypoint, "bind": f"{host}:{port}", "instrument": instrument,
            "app_cpus": cpus, "app_hashes": hash_tree(workdir), "server_cmd": cmd,
            "host": {"platform": platform.platform(), "python": platform.python_version(),
                     "cpus": os.cpu_count(), "hostname": platform.node()}}
    return proc, info


def run_workload(run_dir: Path, workload: str, base_url: str, duration: int, seed: int, extra: list[str],
                 cpus: str | None, interval: float) -> dict:
    """Run the workload script, sampling the load generator itself into client_monitor.csv so that
    client-side saturation can be ruled out."""
    cmd = pinned([PYTHON, str(ROOT / workload), "--base-url", base_url, "--duration", str(duration),
                  "--seed", str(seed), "--out", str(run_dir / "client.csv")] + extra, cpus)
    info = {"workload": workload, "workload_args": extra, "duration_s": duration, "seed": seed,
            "load_cpus": cpus, "workload_cmd": cmd,
            "workload_hash": hashlib.sha256((ROOT / workload).read_bytes()).hexdigest()[:16]}
    print(f"[run] {run_dir.name}: workload running for {duration}s against {base_url} ...", flush=True)
    with open(run_dir / "workload.log", "w") as wlog:
        proc = subprocess.Popen(cmd, stdout=wlog, stderr=subprocess.STDOUT)
        mon = Monitor(proc.pid, str(run_dir / "client_monitor.csv"), interval)
        mon.start()
        info["start"] = time.time()
        try:
            info["workload_exit"] = proc.wait(timeout=duration + 120)
        except subprocess.TimeoutExpired:
            stop_process(proc)
            info["workload_exit"] = "timeout"
        info["end"] = time.time()
        mon.stop()
        mon.join(10)
    return info


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

    try:
        resolve_app(args.app)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    run_dir = new_run_dir(args.label)
    server, app_info = start_app(run_dir, args.app, args.entrypoint, "127.0.0.1", args.port,
                                 args.instrument, args.app_cpus)
    meta = {"mode": "local", "label": args.label, "port": args.port, "monitor_interval_s": args.interval,
            **app_info}

    monitor = None
    try:
        if not wait_port(args.port, 30):
            meta["error"] = "server did not start within 30s (see server.log)"
            print(meta["error"], file=sys.stderr)
            return 1
        monitor = Monitor(server.pid, str(run_dir / "monitor.csv"), args.interval,
                          [str(run_dir / "workdir" / w) for w in args.watch])
        monitor.start()
        meta.update(run_workload(run_dir, args.workload, f"http://127.0.0.1:{args.port}", args.duration,
                                 args.seed, extra, args.load_cpus, args.interval))
        meta["server_alive_at_end"] = server.poll() is None
    finally:
        if monitor:
            monitor.stop()
            monitor.join(10)
        meta["server_exit"] = stop_process(server)
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        print(f"[run] done -> {run_dir.relative_to(ROOT)}", flush=True)
    return 0 if meta.get("workload_exit") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
