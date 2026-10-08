"""Control agent for the SERVER machine in two-machine mode (stdlib only, no extra dependencies).

The client (harness/run_remote.py) drives every diagnostic run through this agent: it asks for a
fresh app instance, runs the workload over the network, then stops the run and downloads the
server-side evidence. Only one run can be active at a time.

    .venv/bin/python harness/server_agent.py --token <secret> [--bind 0.0.0.0] [--port 9000]
    (or: bash deploy/server_agent.sh, which generates and stores the token)

API (JSON; every request needs the header "Authorization: Bearer <token>"):
    GET  /time                     {"server_time": unix float}          (clock-offset estimation)
    GET  /status                   agent info + active run, if any
    POST /runs/start               body: {label, app, entrypoint?, app_port?, instrument?, interval?,
                                          watch?, app_cpus?, max_duration_s}
                                   -> {run_id, server_run_dir, app_port, ...}
    POST /runs/<run_id>/stop       -> server_meta (exit codes, timings)
    GET  /runs/<run_id>/archive    tar.gz of the server run directory

Safety: token required (constant-time compare); app dirs restricted to apps/; a watchdog stops a
run that outlives its max_duration_s (e.g. the client died), so the server never runs forever.
Expose it only on a trusted LAN.
"""
import argparse
import hmac
import io
import json
import re
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor import Monitor  # noqa: E402
from run_diagnostic import ROOT, new_run_dir, port_in_use, resolve_app, start_app, stop_process, wait_port  # noqa: E402

LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class Agent:
    def __init__(self, default_cpus: str | None):
        self.lock = threading.Lock()
        self.active: dict | None = None
        self.finished: dict[str, Path] = {}
        self.default_cpus = default_cpus

    # -- lifecycle ----------------------------------------------------------------------------

    def start(self, req: dict) -> dict:
        label = str(req.get("label", ""))
        if not LABEL_RE.match(label):
            raise ValueError("label must match [A-Za-z0-9_.-]{1,80}")
        port = int(req.get("app_port", 8000))
        max_duration = float(req["max_duration_s"])
        with self.lock:
            if self.active:
                raise RuntimeError(f"run {self.active['run_id']} is still active")
            if port_in_use(port):
                raise RuntimeError(f"app port {port} already in use on the server")
            resolve_app(req["app"])  # validate before creating anything on disk
            run_dir = new_run_dir(label)
            cpus = req.get("app_cpus", self.default_cpus)
            proc, info = start_app(run_dir, req["app"], req.get("entrypoint", "app:app"), "0.0.0.0", port,
                                   bool(req.get("instrument", False)), cpus or None)
            if not wait_port(port, 30):
                stop_process(proc)
                raise RuntimeError(f"app did not start within 30s; see {run_dir.name}/server.log")
            interval = float(req.get("interval", 5.0))
            mon = Monitor(proc.pid, str(run_dir / "monitor.csv"), interval,
                          [str(run_dir / "workdir" / w) for w in req.get("watch", ["db.sqlite3"])])
            mon.start()
            run_id = run_dir.name
            self.active = {"run_id": run_id, "run_dir": run_dir, "proc": proc, "monitor": mon,
                           "meta": {"mode": "remote-server", "label": label, "app_port": port,
                                    "monitor_interval_s": interval, "app_started": time.time(), **info}}
            watchdog = threading.Timer(max_duration, self._watchdog, args=(run_id,))
            watchdog.daemon = True
            watchdog.start()
            print(f"[agent] started {run_id} on :{port}", flush=True)
            return {"run_id": run_id, "server_run_dir": str(run_dir.relative_to(ROOT)), "app_port": port,
                    "app_hashes": info["app_hashes"], "hostname": info["host"]["hostname"]}

    def stop(self, run_id: str, reason: str = "client") -> dict:
        with self.lock:
            if not self.active or self.active["run_id"] != run_id:
                if run_id in self.finished:
                    return json.loads((self.finished[run_id] / "server_meta.json").read_text())
                raise KeyError(f"unknown run {run_id}")
            run = self.active
            self.active = None
        meta = run["meta"]
        meta["server_alive_at_stop"] = run["proc"].poll() is None
        run["monitor"].stop()
        run["monitor"].join(10)
        meta["server_exit"] = stop_process(run["proc"])
        meta["app_stopped"] = time.time()
        meta["stop_reason"] = reason
        (run["run_dir"] / "server_meta.json").write_text(json.dumps(meta, indent=2))
        self.finished[run_id] = run["run_dir"]
        print(f"[agent] stopped {run_id} ({reason})", flush=True)
        return meta

    def _watchdog(self, run_id: str) -> None:
        if self.active and self.active["run_id"] == run_id:
            self.stop(run_id, reason="watchdog: max_duration_s exceeded")

    def archive(self, run_id: str) -> bytes:
        run_dir = self.finished.get(run_id)
        if run_dir is None:
            raise KeyError(f"run {run_id} not finished or unknown")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for p in sorted(run_dir.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    tar.add(p, arcname=str(p.relative_to(run_dir)))
        return buf.getvalue()

    def status(self) -> dict:
        bundle = ROOT / "BUNDLE_VERSION"
        return {"active": self.active["run_id"] if self.active else None,
                "finished": sorted(self.finished), "root": str(ROOT),
                "bundle": bundle.read_text().strip() if bundle.exists() else "dev tree",
                "server_time": time.time()}


def make_handler(agent: Agent, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "aging-agent/1"

        def log_message(self, fmt, *args):  # keep stdout readable: one line per request
            print(f"[agent] {self.address_string()} {fmt % args}", flush=True)

        def _auth(self) -> bool:
            got = self.headers.get("Authorization", "")
            if hmac.compare_digest(got.encode(), f"Bearer {token}".encode()):
                return True
            self._json(401, {"error": "unauthorized"})
            return False

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _dispatch(self, method: str) -> None:
            if not self._auth():
                return
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            try:
                if method == "GET" and parts == ["time"]:
                    return self._json(200, {"server_time": time.time()})
                if method == "GET" and parts == ["status"]:
                    return self._json(200, agent.status())
                if method == "POST" and parts == ["runs", "start"]:
                    return self._json(200, agent.start(self._body()))
                if method == "POST" and len(parts) == 3 and parts[0] == "runs" and parts[2] == "stop":
                    return self._json(200, agent.stop(parts[1]))
                if method == "GET" and len(parts) == 3 and parts[0] == "runs" and parts[2] == "archive":
                    data = agent.archive(parts[1])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/gzip")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._json(404, {"error": "not found"})
            except KeyError as e:
                self._json(404, {"error": str(e)})
            except (ValueError, RuntimeError) as e:
                self._json(409, {"error": str(e)})
            except Exception as e:  # report, never crash the agent
                self._json(500, {"error": repr(e)})

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", required=True)
    ap.add_argument("--bind", default="0.0.0.0", help="interface to listen on (prefer the LAN IP)")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--app-cpus", default=None, help="default taskset CPU list for the app")
    args = ap.parse_args()
    if len(args.token) < 16:
        print("token must have at least 16 characters", file=sys.stderr)
        return 2
    agent = Agent(args.app_cpus)
    httpd = ThreadingHTTPServer((args.bind, args.port), make_handler(agent, args.token))
    print(f"[agent] listening on {args.bind}:{args.port} (root {ROOT}); Ctrl-C to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if agent.active:
            agent.stop(agent.active["run_id"], reason="agent shutdown")
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
