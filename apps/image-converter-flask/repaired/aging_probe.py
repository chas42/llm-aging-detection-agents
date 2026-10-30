"""Aging instrumentation probe for image-converter-flask (finding bax-flask-image-converter-F1).

Finding: every POST /create-gif runs two blocking `convert` subprocesses with no timeout
(app.py ensure_imagemagick_available -> `convert -version`, and run_convert_make_gif -> the real
conversion). If `convert` stalls, the request thread, the child process, its pipe FDs and the
per-request tempfile.TemporaryDirectory(prefix="gif_creator_") with the saved uploads stay held.

Measurement code only: it must not change application behavior. Everything is gated on
AGING_INSTRUMENT=1; when unset, the decorators return the very same function object they were given
and start() returns immediately, so the app runs exactly the original code.

NO TRIGGER INJECTION here. For this finding the stall is injected at the ENVIRONMENT level by a PATH
shim (a fake `convert` placed first in PATH). The probe only observes, so it sees the mechanism
whatever makes `convert` stall (shim, SIGSTOP, slow pixel cache, pathological input).

Output: JSONL at $AGING_INSTRUMENT_LOG
  {"ts", "kind": "start", "probes": [...], ...}
  {"ts", "kind": "snapshot", "metrics": {...}}   every $AGING_SNAPSHOT_INTERVAL s (default 10),
                                                 plus one at start and one at interpreter exit

Hooks (decorators, applied once at import):
  track_handler     on create_gif            -> handler entries/returns, in-flight handlers
  track_convert(k)  on ensure_imagemagick_available (k="version", the app.py:66 call) and on
                    run_convert_make_gif (k="make_gif", the app.py:126 call). Both functions are thin
                    wrappers whose only blocking step is the subprocess.run call, so "+1 before /
                    -1 in finally" around the function is "+1 before / -1 in finally" around the
                    subprocess call (the extra work is building an argv list, microseconds).

State is bounded: fixed scalar counters and windowed aggregates reset at every snapshot. No
containers keyed by request data. All hooks swallow their own exceptions and re-raise only the
app's own exceptions unchanged.

Gauges (snapshot thread only, never per request):
  proc.*            /proc/self/status (Threads, VmSize, VmRSS), len(/proc/self/fd), and one scan of
                    /proc/[0-9]*/stat: direct children of the app (total and by comm), their
                    descendants (e.g. a shim's `sleep`), and the age of the oldest live convert child.
  tmp.gif_creator.* count / files / bytes of <tempfile.gettempdir()>/gif_creator_* (the app's own
                    TemporaryDirectory prefix; gettempdir() honors TMPDIR exactly as the app does).
  tmp.magick.*      count / bytes of magick-* temp files in ImageMagick's temp dir
                    ($MAGICK_TEMPORARY_PATH, else $TMPDIR, else /tmp; a policy.xml temporary-path is
                    not read). Includes ImageMagick temp files of any process on the host.
  The tmp gauges are host-wide by name: gif_creator_* / magick-* entries created by other processes
  sharing the same temp dir are counted too (run with a private TMPDIR to exclude them).

Overhead: hooks take a lock for a few counter updates (microseconds). The snapshot thread, every
AGING_SNAPSHOT_INTERVAL s, reads /proc/self/status, lists /proc/self/fd, scans /proc/*/stat (cost
proportional to the host's process count, a few ms) and scandirs the temp dir plus each
gif_creator_* dir (cost proportional to the number of held dirs and their files). It never touches
app locks or app state.
"""
import atexit
import functools
import json
import os
import shutil
import tempfile
import threading
import time

ENABLED = os.environ.get("AGING_INSTRUMENT") == "1"

try:
    _INTERVAL = float(os.environ.get("AGING_SNAPSHOT_INTERVAL", "10"))
    if _INTERVAL <= 0:
        _INTERVAL = 10.0
except ValueError:
    _INTERVAL = 10.0

_KINDS = ("version", "make_gif")
GIF_PREFIX = "gif_creator_"            # app.py: tempfile.TemporaryDirectory(prefix="gif_creator_")
MAGICK_PREFIX = "magick-"              # ImageMagick AcquireUniqueFileResource names
CONVERT_COMMS = ("convert", "magick", "convert-im6.q16", "convert-im6")

COUNTERS = (
    "calls.create_gif",                 # handler entries (path executed)
    "create_gif.returned",              # handler returned (response or except branch) or raised
    "calls.convert",                    # all convert subprocess entries (version + make_gif)
    "calls.convert.version",            # entries to subprocess.run at app.py:66
    "calls.convert.make_gif",           # entries to subprocess.run at app.py:126
    "convert.returned",                 # convert calls that returned or raised (all kinds)
    "convert.returned.version",
    "convert.returned.make_gif",
    "convert.failed",                   # ... of which raised (RuntimeError on rc != 0, OSError, ...)
)
WINDOW_MAX = (
    "convert_ms.max",                   # max duration of a convert call that returned in the window
    "convert_ms.max.version",
    "convert_ms.max.make_gif",
    "create_gif_ms.max",                # max handler duration (returned in the window)
    "convert.inflight.max",             # max concurrent in-flight convert calls in the window
)
WINDOW_COUNT = (
    "window.calls.create_gif",
    "window.create_gif.returned",
    "window.create_gif.error",          # handler returned a (json, status) error tuple
    "window.calls.convert",
    "window.calls.convert.version",
    "window.calls.convert.make_gif",
    "window.convert.returned",
    "window.convert.failed",
)
GAUGES = (
    "create_gif.inflight",              # calls.create_gif - create_gif.returned
    "convert.inflight",                 # calls.convert - convert.returned
    "convert.inflight.version",
    "convert.inflight.make_gif",
    "proc.threads",                     # /proc/self/status Threads
    "proc.children",                    # processes with PPid == app pid
    "proc.children.convert",            # ... whose comm is convert/magick (also a PATH shim named convert)
    "proc.children.other",              # ... any other comm (e.g. a shim wrapper run as sh/python)
    "proc.descendants",                 # children + their children (e.g. a shim's sleep)
    "proc.children.convert.max_age_s",  # age of the oldest live convert child (0 if none)
    "proc.fds",                         # len(/proc/self/fd)
    "proc.vm_size_kb",                  # VmSize
    "proc.vm_rss_kb",                   # VmRSS
    "tmp.gif_creator.dirs",             # <gettempdir>/gif_creator_* directories
    "tmp.gif_creator.files",            # files inside them
    "tmp.gif_creator.bytes",            # total size of those files
    "tmp.magick.files",                 # <magick tmp>/magick-* entries
    "tmp.magick.bytes",
    "snapshot.gauge_ms",
    "snapshot.seq",
    "snapshot.interval_s",
)

_lock = threading.Lock()                # protects the scalars below (held for microseconds)
_counters = dict.fromkeys(COUNTERS, 0)
_wmax = dict.fromkeys(WINDOW_MAX, 0.0)
_wcount = dict.fromkeys(WINDOW_COUNT, 0)
_inflight = dict.fromkeys(_KINDS, 0)

_out_lock = threading.Lock()
_out = None
_started = False
_stop = threading.Event()
_seq = 0
_pid = os.getpid()
_gif_tmp = None
_magick_tmp = None


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
def _handler_enter() -> float:
    try:
        with _lock:
            _counters["calls.create_gif"] += 1
            _wcount["window.calls.create_gif"] += 1
        return _now_ms()
    except Exception:
        return 0.0


def _handler_exit(t0: float, result) -> None:
    try:
        dt = _now_ms() - t0
        err = isinstance(result, tuple)
        with _lock:
            _counters["create_gif.returned"] += 1
            _wcount["window.create_gif.returned"] += 1
            if err:
                _wcount["window.create_gif.error"] += 1
            if dt > _wmax["create_gif_ms.max"]:
                _wmax["create_gif_ms.max"] = dt
    except Exception:
        pass


def track_handler(fn):
    """Decorator for create_gif. Disabled: returns fn itself (no wrapper at all)."""
    if not ENABLED:
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = _handler_enter()
        result = None
        try:
            result = fn(*args, **kwargs)
            return result
        finally:
            _handler_exit(t0, result)
    return wrapper


def _convert_enter(kind: str) -> float:
    try:
        with _lock:
            _counters["calls.convert"] += 1
            _counters["calls.convert." + kind] += 1
            _wcount["window.calls.convert"] += 1
            _wcount["window.calls.convert." + kind] += 1
            _inflight[kind] += 1
            total = _inflight["version"] + _inflight["make_gif"]
            if total > _wmax["convert.inflight.max"]:
                _wmax["convert.inflight.max"] = float(total)
        return _now_ms()
    except Exception:
        return 0.0


def _convert_exit(kind: str, t0: float, failed: bool) -> None:
    try:
        dt = _now_ms() - t0
        with _lock:
            _inflight[kind] -= 1
            _counters["convert.returned"] += 1
            _counters["convert.returned." + kind] += 1
            _wcount["window.convert.returned"] += 1
            if failed:
                _counters["convert.failed"] += 1
                _wcount["window.convert.failed"] += 1
            if dt > _wmax["convert_ms.max"]:
                _wmax["convert_ms.max"] = dt
            if dt > _wmax["convert_ms.max." + kind]:
                _wmax["convert_ms.max." + kind] = dt
    except Exception:
        pass


def track_convert(kind: str):
    """Decorator factory for the two functions that wrap subprocess.run(["convert", ...]).
    Disabled: the returned decorator returns fn itself."""
    def deco(fn):
        if not ENABLED or kind not in _KINDS:
            return fn

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = _convert_enter(kind)
            failed = True
            try:
                result = fn(*args, **kwargs)
                failed = False
                return result
            finally:
                _convert_exit(kind, t0, failed)
        return wrapper
    return deco


# --------------------------------------------------------------------------- gauges
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

    # one pass over /proc; the temporary maps live only for this call (bounded by host processes)
    me = str(_pid)
    ppid_of = {}
    children = n_conv = 0
    oldest_start = None
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/stat") as f:
                stat = f.read()
        except OSError:            # exited meanwhile
            continue
        lp, rp = stat.find("("), stat.rfind(")")
        rest = stat[rp + 2:].split(" ")   # [0]=state [1]=ppid ... [19]=starttime
        if len(rest) < 20:
            continue
        ppid_of[name] = rest[1]
        if rest[1] == me:
            children += 1
            comm = stat[lp + 1:rp]
            if comm in CONVERT_COMMS:
                n_conv += 1
                st = int(rest[19])
                if oldest_start is None or st < oldest_start:
                    oldest_start = st
    child_set = {p for p, pp in ppid_of.items() if pp == me}
    grand = sum(1 for pp in ppid_of.values() if pp in child_set)
    m["proc.children"] = children
    m["proc.children.convert"] = n_conv
    m["proc.children.other"] = children - n_conv
    m["proc.descendants"] = children + grand
    age = 0.0
    if oldest_start is not None:
        try:
            with open("/proc/uptime") as f:
                uptime = float(f.read().split()[0])
            age = max(0.0, uptime - oldest_start / os.sysconf("SC_CLK_TCK"))
        except Exception:
            age = 0.0
    m["proc.children.convert.max_age_s"] = round(age, 1)


def _dir_usage(path: str):
    files = size = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                size += os.lstat(os.path.join(root, n)).st_size
                files += 1
            except OSError:
                pass
    return files, size


def _tmp_gauges(m: dict) -> None:
    dirs = files = size = 0
    mfiles = msize = 0
    same = _magick_tmp == _gif_tmp
    try:
        with os.scandir(_gif_tmp) as it:
            for e in it:
                try:
                    if e.name.startswith(GIF_PREFIX) and e.is_dir(follow_symlinks=False):
                        dirs += 1
                        f, s = _dir_usage(e.path)
                        files += f
                        size += s
                    elif same and e.name.startswith(MAGICK_PREFIX):
                        mfiles += 1
                        msize += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    except OSError:
        pass
    if not same:
        try:
            with os.scandir(_magick_tmp) as it:
                for e in it:
                    if e.name.startswith(MAGICK_PREFIX):
                        try:
                            mfiles += 1
                            msize += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
        except OSError:
            pass
    m["tmp.gif_creator.dirs"] = dirs
    m["tmp.gif_creator.files"] = files
    m["tmp.gif_creator.bytes"] = size
    m["tmp.magick.files"] = mfiles
    m["tmp.magick.bytes"] = msize


# --------------------------------------------------------------------------- snapshot
def snapshot() -> None:
    global _seq
    if not _started:
        return
    try:
        with _lock:
            m = dict(_counters)
            m.update(_wmax)
            m.update(_wcount)
            m["create_gif.inflight"] = _counters["calls.create_gif"] - _counters["create_gif.returned"]
            total = _inflight["version"] + _inflight["make_gif"]
            m["convert.inflight"] = total
            m["convert.inflight.version"] = _inflight["version"]
            m["convert.inflight.make_gif"] = _inflight["make_gif"]
            for k in _wmax:
                _wmax[k] = 0.0
            # the in-flight max of the next window starts from the current level
            _wmax["convert.inflight.max"] = float(total)
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
        try:
            _tmp_gauges(m)
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
    global _out, _started, _gif_tmp, _magick_tmp
    if not ENABLED or _started:
        return
    try:
        _gif_tmp = tempfile.gettempdir()
        _magick_tmp = (os.environ.get("MAGICK_TEMPORARY_PATH") or os.environ.get("TMPDIR")
                       or "/tmp")
        log_path = os.environ.get("AGING_INSTRUMENT_LOG") or "aging_instrument.jsonl"
        _out = open(log_path, "a", buffering=1, encoding="utf-8")
        _started = True
        _write({"ts": time.time(), "kind": "start",
                "probes": list(COUNTERS + WINDOW_MAX + WINDOW_COUNT + GAUGES),
                "pid": _pid, "interval_s": _INTERVAL, "inject": None,
                "gif_tmp_dir": _gif_tmp, "magick_tmp_dir": _magick_tmp,
                "path": os.environ.get("PATH", ""),
                "convert_resolved": shutil.which("convert")})
        snapshot()
        threading.Thread(target=_loop, name="aging-probe-snapshot", daemon=True).start()
        atexit.register(_at_exit)
    except Exception:
        pass
