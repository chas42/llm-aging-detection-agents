"""Run the BaxBench-derived functional test against a fresh copy of an app.

    python harness/functional_check.py --app apps/uptime-fastapi/original \
        --test reference/tests/test_uptime.py [--instrument]

Exit code 0 = PASS, 1 = FAIL, 2 = SETUP_FAILED. The app copy and its database are
created in a temporary directory, so the app directory itself is never modified.
"""
import argparse
import importlib.util
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_diagnostic import ROOT, app_port, port_in_use, start_app, stop_process, wait_port  # noqa: E402


class _App:
    def __init__(self, port: int):
        self.port = port
        self.logger = logging.getLogger("functional_check")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--entrypoint", default="app:app")
    ap.add_argument("--port", type=int, default=3000,
                    help="the BaxBench tests call localhost:<port>; an app.yaml 'port' overrides it")
    ap.add_argument("--instrument", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        args.port = app_port(args.app, args.port)  # the app's own port, if its manifest fixes one
    except ValueError as e:
        print(f"SETUP_FAILED: {e}")
        return 2
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
        run_dir = Path(tmp)
        server, _ = start_app(run_dir, args.app, args.entrypoint, "127.0.0.1", args.port, args.instrument, None)
        try:
            if not wait_port(args.port, 60):
                print("SETUP_FAILED: server did not start\n" + (run_dir / "server.log").read_text()[-3000:])
                return 2
            results = {}
            for t in tests:
                try:
                    results[t.__name__] = bool(t(_App(args.port)))
                except Exception:  # a crash in the test is a failure, reported verbatim
                    logging.exception("test %s raised", t.__name__)
                    results[t.__name__] = False
        finally:
            stop_process(server)
        if args.instrument:
            instr = run_dir / "instrumentation.jsonl"
            print(f"instrumentation log lines: {sum(1 for _ in open(instr)) if instr.exists() else 0}")
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
