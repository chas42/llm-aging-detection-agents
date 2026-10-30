"""Aging instrumentation probe for monitor-flask (finding bax-flask-monitor-F1).

Finding: POST /monitor/commands runs `ps` via subprocess.run with no timeout, synchronously on the
request thread (app.py _run_ps). If `ps` stalls, request threads, child processes and pipe FDs of
the app process accumulate.

Measurement code only: it must not change application behavior. Everything is gated on
AGING_INSTRUMENT=1; when unset, every hook returns immediately and inject_ps() returns the very
same argv list it was given.

Output: JSONL at $AGING_INSTRUMENT_LOG
  {"ts", "kind": "start", "probes": [...], ...}
  {"ts", "kind": "snapshot", "metrics": {...}}   every $AGING_SNAPSHOT_INTERVAL s (default 10),
                                                 plus one at start and one at interpreter exit

State is bounded: fixed scalar counters and windowed maxima reset at every snapshot. No
containers keyed by request data. All hooks swallow their own exceptions.

------------------------------------------------------------------------------------------------
TRIGGER INJECTION (card controllability: environment)
------------------------------------------------------------------------------------------------
Active only when AGING_INSTRUMENT=1 AND AGING_INJECT_PS_HANG_PROB is a float p in (0, 1].
  AGING_INJECT_PS_HANG_PROB  p: each ps invocation stalls with probability p
  AGING_INJECT_PS_HANG_S     N: stall length in seconds (default 36000 = 10 h, see below)
  AGING_INJECT_SEED          seed of the dedicated random.Random used for the decision (default 42)
Unset / invalid / p <= 0 -> no injection: inject_ps() returns its argument unchanged.

What it does: the argv the app built (e.g. ["ps", "aux"]) is replaced, for a stalled invocation, by
    setpriv --pdeathsig KILL -- /bin/sh -c
        'setpriv --pdeathsig KILL -- sleep "$1" && shift && exec "$@"' aging_ps_stall N ps aux
i.e. the child process the app spawns first sleeps N seconds and then exec()s the original ps argv
unchanged (passed as positional parameters, so no quoting or shell interpretation is involved).
The app's logic is untouched: subprocess.run is called with the same keyword arguments, it blocks
in communicate() on a real child with real stdout/stderr pipes, and after N seconds the real `ps`
runs and its output is parsed exactly as usual. What accumulates during the stall is therefore the
real mechanism of the card: one request thread parked in Popen.communicate(), one child process
(PPid = app pid; plus its `sleep` grandchild), two pipe read-ends in the app, and the client socket.

Realism: this reproduces, from the app's point of view, a `ps` that blocks while reading
/proc/<pid>/cmdline (or /proc/<pid>/environ) of a process in uninterruptible sleep (D state), e.g.
a process stuck on a hung NFS/FUSE mount or holding its mm lock: ps is alive, produces no output,
does not exit, and the parent waits in communicate() with no timeout. The app cannot tell the two
apart (in both cases it sees a live child with open, silent pipes).

Default N = 36000 s (10 h) and why: with requests arriving at rate r and a stall probability p, the
number of stalled calls in flight is r*p*t while t < N, then plateaus at r*p*N (Little's law), since
each held set of resources is released N seconds after it started. A short N (e.g. 600 s) would
give 10 min of growth followed by a flat plateau for the rest of an 8 h run, which is a step, not an
aging trend. Choosing N longer than the run makes the stall effectively persistent for the whole run
(the card's "if ps never exits, growth is monotonic" case), so the gauges grow linearly over 8 h.
Example: r = 3 req/s, p = 0.01 -> ~108 stalled calls/h -> ~860 threads, ~860 children (+860 sleep
grandchildren) and ~2600 FDs after 8 h, below RLIMIT_NPROC/RLIMIT_NOFILE of a typical host; raise p
to drive the process into exhaustion. Limitation: the stall is still time-bounded by N, so the held
resources are released N seconds after each stall began; runs longer than N plateau.

Cleanup: setpriv --pdeathsig KILL makes the stalled `sh` (and its `sleep`) die as soon as the app
process (strictly: the request thread that forked it, which is parked until the child exits) dies,
so stopping the app never leaves orphaned stalled children behind. If setpriv is not installed the
wrapper falls back to plain /bin/sh (orphans would then survive up to N s after the app stops); the
start line reports which mode is in use ("inject_cleanup").

Side effects only under injection: a stalled invocation whose ps binary is missing exits 127 from
the shell (app returns 400) instead of raising FileNotFoundError (app returns 500); the ps output
of other calls includes the stalled sh/sleep processes (they are real processes).

Overhead: hooks take a lock for a few counter updates (microseconds). The snapshot thread, every
AGING_SNAPSHOT_INTERVAL s, reads /proc/self/status, lists /proc/self/fd, and scans /proc/[0-9]*/stat
to count children (cost proportional to the host's process count, typically a few ms). It never
touches app locks or app state.
"""
import atexit
import json
import os
import random
import shutil
import threading
import time

ENABLED = os.environ.get("AGING_INSTRUMENT") == "1"

try:
    _INTERVAL = float(os.environ.get("AGING_SNAPSHOT_INTERVAL", "10"))
    if _INTERVAL <= 0:
        _INTERVAL = 10.0
except ValueError:
    _INTERVAL = 10.0


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


_INJECT_P = _float_env("AGING_INJECT_PS_HANG_PROB", 0.0)
if not (0.0 < _INJECT_P <= 1.0):
    _INJECT_P = 0.0
_INJECT_S = _float_env("AGING_INJECT_PS_HANG_S", 36000.0)
if _INJECT_S <= 0:
    _INJECT_S = 36000.0
try:
    _INJECT_SEED = int(os.environ.get("AGING_INJECT_SEED", "42"))
except ValueError:
    _INJECT_SEED = 42
INJECT = ENABLED and _INJECT_P > 0.0
_rng = random.Random(_INJECT_SEED)
_SETPRIV = shutil.which("setpriv") if INJECT else None
_SH_SCRIPT = ('setpriv --pdeathsig KILL -- sleep "$1" && shift && exec "$@"' if _SETPRIV
              else 'sleep "$1" && shift && exec "$@"')
_N_ARG = ("%.3f" % _INJECT_S).rstrip("0").rstrip(".")

COUNTERS = (
    "calls.monitor_commands",       # handler entries (path executed)
    "calls.run_ps",                 # _run_ps -> subprocess.run entries
    "ps.returned",                  # subprocess.run returned or raised
    "inject.stalled",               # invocations wrapped with the injected stall
)
WINDOW_MAX = (
    "ps_ms.max",                    # max duration of a ps call that returned in this window
    "ps.inflight.max",              # max concurrent in-flight ps calls seen in this window
)
WINDOW_COUNT = (
    "window.calls.monitor_commands",
    "window.calls.run_ps",
    "window.ps.returned",
    "window.inject.stalled",
)
GAUGES = (
    "ps.inflight",                  # calls.run_ps - ps.returned at snapshot time
    "proc.threads",                 # /proc/self/status Threads
    "proc.children",                # processes with PPid == app pid
    "proc.children.ps",             # ... whose comm is "ps"
    "proc.children.stall",          # ... whose comm is "sh" (injected stall wrapper; app never runs sh)
    "proc.fds",                     # len(/proc/self/fd)
    "proc.vm_size_kb",              # VmSize (VSZ: one stack per held thread)
    "proc.vm_rss_kb",               # VmRSS
    "snapshot.gauge_ms",
    "snapshot.seq",
    "snapshot.interval_s",
)

_lock = threading.Lock()            # protects the scalars below (held for microseconds)
_counters = dict.fromkeys(COUNTERS, 0)
_wmax = dict.fromkeys(WINDOW_MAX, 0.0)
_wcount = dict.fromkeys(WINDOW_COUNT, 0)
_inflight = 0

_out_lock = threading.Lock()
_out = None
_started = False
_stop = threading.Event()
_seq = 0
_pid = os.getpid()


# --------------------------------------------------------------------------- output
def _write(obj) -> None:
    if _out is None:
        return
    line = json.dumps(obj, separators=(",", ":"))
    with _out_lock:
        try:
            _out.write(line + "\n")
            _out.flush()
        except Exception:
            pass


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


# --------------------------------------------------------------------------- hooks
def handler_call() -> None:
    """First statement of monitor_commands."""
    if not ENABLED:
        return
    try:
        with _lock:
            _counters["calls.monitor_commands"] += 1
            _wcount["window.calls.monitor_commands"] += 1
    except Exception:
        pass


def inject_ps(cmd):
    """Trigger injection: returns `cmd` itself unless injection is enabled and the seeded RNG
    selects this invocation, in which case the child first sleeps N s and then execs `cmd`."""
    if not INJECT:
        return cmd
    try:
        with _lock:
            hit = _rng.random() < _INJECT_P
            if hit:
                _counters["inject.stalled"] += 1
                _wcount["window.inject.stalled"] += 1
        if not hit:
            return cmd
        head = [_SETPRIV, "--pdeathsig", "KILL", "--"] if _SETPRIV else []
        return head + ["/bin/sh", "-c", _SH_SCRIPT, "aging_ps_stall", _N_ARG] + list(cmd)
    except Exception:
        return cmd


def ps_enter() -> float:
    """Right before subprocess.run. Returns a start timestamp (ms); 0.0 when disabled."""
    global _inflight
    if not ENABLED:
        return 0.0
    try:
        with _lock:
            _counters["calls.run_ps"] += 1
            _wcount["window.calls.run_ps"] += 1
            _inflight += 1
            if _inflight > _wmax["ps.inflight.max"]:
                _wmax["ps.inflight.max"] = float(_inflight)
        return _now_ms()
    except Exception:
        return 0.0


def ps_exit(t_start: float) -> None:
    """In a `finally:` after subprocess.run (returned or raised)."""
    global _inflight
    if not ENABLED:
        return
    try:
        dt = _now_ms() - t_start
        with _lock:
            _inflight -= 1
            _counters["ps.returned"] += 1
            _wcount["window.ps.returned"] += 1
            if dt > _wmax["ps_ms.max"]:
                _wmax["ps_ms.max"] = dt
    except Exception:
        pass


# --------------------------------------------------------------------------- snapshot
def _proc_gauges(m: dict) -> None:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("Threads:"):
                m["proc.threads"] = int(line.split()[1])
            elif line.startswith("VmSize:"):
                m["proc.vm_size_kb"] = int(line.split()[1])
            elif line.startswith("VmRSS:"):
                m["proc.vm_rss_kb"] = int(line.split()[1])
    m["proc.fds"] = len(os.listdir("/proc/self/fd"))
    children = n_ps = n_stall = 0
    me = str(_pid)
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/stat") as f:
                stat = f.read()
        except OSError:            # exited meanwhile
            continue
        lp, rp = stat.find("("), stat.rfind(")")
        rest = stat[rp + 2:].split(" ", 2)   # state, ppid, ...
        if len(rest) >= 2 and rest[1] == me:
            children += 1
            comm = stat[lp + 1:rp]
            if comm == "ps":
                n_ps += 1
            elif comm == "sh":
                n_stall += 1
    m["proc.children"] = children
    m["proc.children.ps"] = n_ps
    m["proc.children.stall"] = n_stall


def snapshot() -> None:
    global _seq
    if not _started:
        return
    try:
        with _lock:
            m = dict(_counters)
            m.update(_wmax)
            m.update(_wcount)
            m["ps.inflight"] = _inflight
            for k in _wmax:
                _wmax[k] = 0.0
            # the in-flight max of the next window starts from the current level
            _wmax["ps.inflight.max"] = float(_inflight)
            for k in _wcount:
                _wcount[k] = 0
            _seq += 1
            m["snapshot.seq"] = _seq
        m["snapshot.interval_s"] = _INTERVAL
        t0 = _now_ms()
        try:
            _proc_gauges(m)
        except Exception:
            pass
        m["snapshot.gauge_ms"] = _now_ms() - t0
        _write({"ts": time.time(), "kind": "snapshot", "metrics": m})
    except Exception:
        pass


def _loop() -> None:
    while not _stop.wait(_INTERVAL):
        snapshot()


def _at_exit() -> None:
    _stop.set()
    snapshot()


def start() -> None:
    """Called once at app import. Opens the log, writes the start line, takes the baseline
    snapshot and starts the daemon snapshot thread."""
    global _out, _started
    if not ENABLED or _started:
        return
    try:
        log_path = os.environ.get("AGING_INSTRUMENT_LOG") or "aging_instrument.jsonl"
        _out = open(log_path, "a", buffering=1, encoding="utf-8")
        _started = True
        _write({"ts": time.time(), "kind": "start",
                "probes": list(COUNTERS + WINDOW_MAX + WINDOW_COUNT + GAUGES),
                "pid": _pid, "interval_s": _INTERVAL,
                "inject": {"enabled": INJECT, "ps_hang_prob": _INJECT_P, "ps_hang_s": _INJECT_S,
                           "seed": _INJECT_SEED,
                           "inject_cleanup": ("setpriv-pdeathsig" if _SETPRIV else "none")
                           if INJECT else None}})
        snapshot()
        threading.Thread(target=_loop, name="aging-probe-snapshot", daemon=True).start()
        atexit.register(_at_exit)
    except Exception:
        pass
