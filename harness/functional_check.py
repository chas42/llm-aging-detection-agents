"""Run the BaxBench-derived functional test against a fresh copy of an app.

    python harness/functional_check.py --app apps/uptime-fastapi/original \
        --test reference/tests/test_uptime.py [--instrument]

Exit code 0 = PASS, 1 = FAIL, 2 = SETUP_FAILED. The app copy and its database are
created in a temporary directory, so the app directory itself is never modified.
"""
import argparse
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_diagnostic import IGNORE, PYTHON, ROOT, port_in_use, stop_process, wait_port  # noqa: E402


class _App:
    def __init__(self, port: int):
        self.port = port
        self.logger = logging.getLogger("functional_check")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--entrypoint", default="app:app")
    ap.add_argument("--port", type=int, default=3000, help="the BaxBench tests hardcode localhost:<port>")
    ap.add_argument("--instrument", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if port_in_use(args.port):
        print(f"SETUP_FAILED: port {args.port} already in use")
        return 2
    spec = importlib.util.spec_from_file_location("functest", ROOT / args.test)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tests = [getattr(mod, n) for n in dir(mod) if n.startswith("func_test")]
    if not tests:
        print("SETUP_FAILED: no func_test* functions found in", args.test)
        return 2

    with tempfile.TemporaryDirectory(prefix="fcheck_") as tmp:
        workdir = Path(tmp) / "app"
        shutil.copytree(ROOT / args.app, workdir, ignore=IGNORE)
        env = dict(os.environ, APP_SECRET="supers3cret")
        if args.instrument:
            env.update(AGING_INSTRUMENT="1", AGING_INSTRUMENT_LOG=str(Path(tmp) / "instr.jsonl"))
        log = open(Path(tmp) / "server.log", "w+")
        server = subprocess.Popen([PYTHON, "-m", "uvicorn", args.entrypoint, "--host", "127.0.0.1",
                                   "--port", str(args.port), "--log-level", "warning"],
                                  cwd=workdir, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            if not wait_port(args.port, 30):
                log.seek(0)
                print("SETUP_FAILED: server did not start\n" + log.read()[-3000:])
                return 2
            results = {}
            for t in tests:
                try:
                    results[t.__name__] = bool(t(_App(args.port)))
                except Exception as e:  # a crash in the test is a failure, reported verbatim
                    logging.exception("test %s raised", t.__name__)
                    results[t.__name__] = False
        finally:
            stop_process(server)
            log.close()
        if args.instrument:
            instr = Path(tmp) / "instr.jsonl"
            print(f"instrumentation log lines: {sum(1 for _ in open(instr)) if instr.exists() else 0}")
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
