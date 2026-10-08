"""Run one diagnostic execution in TWO-MACHINE mode, from the CLIENT (load generator) machine.

The server machine runs harness/server_agent.py. This script:
  1. estimates the client/server clock offset (GET /time, min-RTT sample);
  2. asks the agent for a fresh app instance (POST /runs/start) and checks the bundle versions match;
  3. runs the workload locally against http://<server>:<app_port>, sampling the load generator;
  4. stops the run (POST /runs/<id>/stop), re-estimates the offset, downloads the server evidence
     (monitor.csv, instrumentation.jsonl, server.log, workdir/, server_meta.json) into the local
     run directory, and writes a merged meta.json.
The resulting runs/<ts>_<label>/ has the same layout as a local run, so analyze_run.py and the
activation judge work unchanged (server-side timestamps are corrected by meta.clock.offset_s).

    .venv/bin/python harness/run_remote.py --server http://192.168.0.10:9000 --token <secret> \\
        --app apps/uptime-fastapi/instrumented --instrument \\
        --workload workloads/uptime-fastapi-F4_targeted_v1.py --duration 3600 --label R1 -- [workload args]
The token can also come from the AGING_AGENT_TOKEN environment variable.
"""
import argparse
import io
import json
import os
import socket
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_diagnostic import ROOT, hash_tree, new_run_dir, resolve_app, run_workload  # noqa: E402


class AgentClient:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 60, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"agent {method} {path} -> {e.code}: {e.read().decode(errors='replace')}") from None
        return payload if raw else json.loads(payload)

    def clock_offset(self, samples: int = 15) -> dict:
        """offset_s = server_clock - client_clock, from the sample with the smallest round trip."""
        best = None
        for _ in range(samples):
            t0 = time.time()
            srv = self.call("GET", "/time", timeout=10)["server_time"]
            t1 = time.time()
            rtt = t1 - t0
            if best is None or rtt < best[0]:
                best = (rtt, srv - (t0 + t1) / 2)
            time.sleep(0.05)
        return {"offset_s": best[1], "rtt_ms": best[0] * 1000}


def wait_tcp(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", required=True, help="agent URL, e.g. http://192.168.0.10:9000")
    ap.add_argument("--token", default=os.environ.get("AGING_AGENT_TOKEN"))
    ap.add_argument("--app", required=True, help="app dir (must exist on BOTH machines, same bundle)")
    ap.add_argument("--entrypoint", default="app:app")
    ap.add_argument("--app-port", type=int, default=8000)
    ap.add_argument("--app-cpus", default=None, help="server-side taskset CPU list (default: agent's)")
    ap.add_argument("--workload", required=True)
    ap.add_argument("--duration", type=int, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--watch", nargs="*", default=["db.sqlite3"])
    ap.add_argument("--instrument", action="store_true")
    ap.add_argument("--load-cpus", default=None, help="client-side taskset CPU list")
    ap.add_argument("workload_args", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    extra = [a for a in args.workload_args if a != "--"]
    if not args.token:
        print("missing --token (or AGING_AGENT_TOKEN)", file=sys.stderr)
        return 2

    try:
        local_hashes = hash_tree(resolve_app(args.app))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    agent = AgentClient(args.server, args.token)
    app_host = urlparse(args.server).hostname
    status = agent.call("GET", "/status")
    if status["active"]:
        print(f"server busy with run {status['active']}", file=sys.stderr)
        return 2

    run_dir = new_run_dir(args.label)
    local_bundle = (ROOT / "BUNDLE_VERSION").read_text().strip() if (ROOT / "BUNDLE_VERSION").exists() else "dev tree"
    meta = {"mode": "remote", "label": args.label, "server": args.server, "app_host": app_host,
            "app_port": args.app_port, "app": args.app, "instrument": args.instrument,
            "monitor_interval_s": args.interval, "bundle": {"client": local_bundle, "server": status["bundle"]}}
    clock = {"start": agent.clock_offset()}
    run_id = None
    try:
        started = agent.call("POST", "/runs/start", {
            "label": args.label, "app": args.app, "entrypoint": args.entrypoint, "app_port": args.app_port,
            "instrument": args.instrument, "interval": args.interval, "watch": args.watch,
            "app_cpus": args.app_cpus, "max_duration_s": args.duration + 600}, timeout=90)
        run_id = meta["server_run_id"] = started["run_id"]
        meta["app_hashes"] = started["app_hashes"]
        if local_hashes != started["app_hashes"]:
            meta["warning"] = "app code differs between client and server bundles"
            print(f"[remote] WARNING: {meta['warning']}: {local_hashes} vs {started['app_hashes']}", file=sys.stderr)
        if not wait_tcp(app_host, args.app_port, 30):
            raise RuntimeError(f"app port {app_host}:{args.app_port} not reachable from the client (firewall?)")
        meta.update(run_workload(run_dir, args.workload, f"http://{app_host}:{args.app_port}", args.duration,
                                 args.seed, extra, args.load_cpus, args.interval))
    except Exception as e:
        meta["error"] = str(e)
        print(f"[remote] ERROR: {e}", file=sys.stderr)
    finally:
        if run_id:
            try:
                meta["server_meta"] = agent.call("POST", f"/runs/{run_id}/stop", timeout=90)
                with tarfile.open(fileobj=io.BytesIO(agent.call("GET", f"/runs/{run_id}/archive",
                                                                timeout=600, raw=True))) as tar:
                    if hasattr(tarfile, "data_filter"):  # Python >= 3.10.12: reject unsafe members
                        tar.extractall(run_dir, filter="data")
                    else:
                        tar.extractall(run_dir)
                meta["server_alive_at_end"] = meta["server_meta"].get("server_alive_at_stop")
                meta["server_exit"] = meta["server_meta"].get("server_exit")
            except Exception as e:
                meta["error"] = (meta.get("error", "") + f"; stop/fetch failed: {e}").lstrip("; ")
                print(f"[remote] ERROR during stop/fetch: {e}", file=sys.stderr)
        try:
            clock["end"] = agent.clock_offset()
            clock["drift_s"] = clock["end"]["offset_s"] - clock["start"]["offset_s"]
            clock["offset_s"] = (clock["start"]["offset_s"] + clock["end"]["offset_s"]) / 2
        except Exception:
            clock["offset_s"] = clock["start"]["offset_s"]
        meta["clock"] = clock
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        print(f"[remote] done -> {run_dir.relative_to(ROOT)} (clock offset {clock['offset_s'] * 1000:.1f} ms, "
              f"rtt {clock['start']['rtt_ms']:.2f} ms)", flush=True)
    return 0 if meta.get("workload_exit") == 0 and "error" not in meta else 1


if __name__ == "__main__":
    sys.exit(main())
