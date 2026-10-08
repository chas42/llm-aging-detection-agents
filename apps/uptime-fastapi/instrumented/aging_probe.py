"""Aging instrumentation probe for uptime-fastapi (finding uptime-fastapi-F4).

Measurement code only: it must not change application behavior. Everything is gated on
AGING_INSTRUMENT=1; when unset, every hook returns immediately.

Output: JSONL at $AGING_INSTRUMENT_LOG
  {"ts", "kind": "start", "probes": [...]}
  {"ts", "kind": "snapshot", "metrics": {...}}   every $AGING_SNAPSHOT_INTERVAL s (default 10),
                                                 plus one at start and one at shutdown
  {"ts", "kind": "event", "probe": ..., "fields": {...}}   rare paths, <= 1/s per probe

State is bounded: fixed scalar counters and windowed maxima reset at every snapshot. No
containers keyed by request data. All hooks swallow their own exceptions.
"""
import json
import os
import sqlite3
import threading
import time

ENABLED = os.environ.get("AGING_INSTRUMENT") == "1"

try:
    _INTERVAL = float(os.environ.get("AGING_SNAPSHOT_INTERVAL", "10"))
    if _INTERVAL <= 0:
        _INTERVAL = 10.0
except ValueError:
    _INTERVAL = 10.0

# Max time the snapshot thread waits for the app's _db_lock before giving up on the
# row gauges for that snapshot (so a stuck app never makes the probe hang forever).
_SNAPSHOT_LOCK_TIMEOUT_S = 30.0

COUNTERS = (
    "calls.upsert_heartbeat",
    "calls.list_services_for_token",
    "calls.services",
    "rows_fetched.total",          # cumulative rows returned by list_services_for_token
    "errors.heartbeat_500",
    "errors.services_500",
)
WINDOW_MAX = (
    "lock_wait_ms.max",            # max _db_lock wait over upsert + list paths
    "lock_wait_ms.upsert.max",
    "lock_wait_ms.list.max",
    "upsert_ms.max",               # time holding the lock in upsert (connect+insert+commit)
    "fetch_ms.max",                # time holding the lock in list (connect+select+fetchall)
    "fetch_rows.max",              # max rows fetched by one list_services_for_token call
    "services.serialized_rows.max",  # max ServiceOut objects built by one /services call
    "services.handler_ms.max",     # max /services handler body time (excl. FastAPI encoding)
)
WINDOW_COUNT = (
    "window.calls.upsert_heartbeat",
    "window.calls.list_services_for_token",
    "window.calls.services",
)
GAUGES = (
    "rows.total",
    "rows.max_per_token",
    "tokens.distinct",
    "db.bytes",                    # db.sqlite3 + -journal + -wal + -shm
    "db.main_bytes",
    "snapshot.lock_wait_ms",
    "snapshot.query_ms",
    "snapshot.lock_timeout",
    "snapshot.seq",
    "snapshot.interval_s",
)
EVENTS = ("heartbeat_error", "services_error", "probe_error")

_lock = threading.Lock()           # protects the scalars below (held for microseconds)
_counters = dict.fromkeys(COUNTERS, 0)
_wmax = dict.fromkeys(WINDOW_MAX, 0.0)
_wcount = dict.fromkeys(WINDOW_COUNT, 0)
_last_event = dict.fromkeys(EVENTS, 0.0)

_out_lock = threading.Lock()
_out = None
_db_path = None
_db_lock = None
_started = False
_stop = threading.Event()
_thread = None
_seq = 0


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


def _max(name: str, value: float) -> None:
    if value > _wmax[name]:
        _wmax[name] = value


# --------------------------------------------------------------------------- hooks
def t0() -> float:
    """Timestamp (ms) taken right before a suspect path starts; 0.0 when disabled."""
    if not ENABLED:
        return 0.0
    return _now_ms()


def lock_acquired(path: str, t_before: float) -> float:
    """Called as the first statement inside `with _db_lock:`. Returns acquisition ts (ms)."""
    if not ENABLED:
        return 0.0
    try:
        now = _now_ms()
        wait = now - t_before
        with _lock:
            _max("lock_wait_ms.max", wait)
            if path == "upsert":
                _counters["calls.upsert_heartbeat"] += 1
                _wcount["window.calls.upsert_heartbeat"] += 1
                _max("lock_wait_ms.upsert.max", wait)
            else:
                _counters["calls.list_services_for_token"] += 1
                _wcount["window.calls.list_services_for_token"] += 1
                _max("lock_wait_ms.list.max", wait)
        return now
    except Exception:
        return 0.0


def upsert_done(t_acquired: float) -> None:
    if not ENABLED:
        return
    try:
        dt = _now_ms() - t_acquired
        with _lock:
            _max("upsert_ms.max", dt)
    except Exception:
        pass


def fetched(n_rows: int, t_acquired: float) -> None:
    if not ENABLED:
        return
    try:
        dt = _now_ms() - t_acquired
        with _lock:
            _counters["rows_fetched.total"] += n_rows
            _max("fetch_rows.max", float(n_rows))
            _max("fetch_ms.max", dt)
    except Exception:
        pass


def services_done(n_out: int, t_start: float) -> None:
    if not ENABLED:
        return
    try:
        dt = _now_ms() - t_start
        with _lock:
            _counters["calls.services"] += 1
            _wcount["window.calls.services"] += 1
            _max("services.serialized_rows.max", float(n_out))
            _max("services.handler_ms.max", dt)
    except Exception:
        pass


def event(probe: str, **fields) -> None:
    """Rare-path event, rate-limited to 1/s per probe name (fixed set of names)."""
    if not ENABLED:
        return
    try:
        if probe == "heartbeat_error":
            with _lock:
                _counters["errors.heartbeat_500"] += 1
        elif probe == "services_error":
            with _lock:
                _counters["errors.services_500"] += 1
        if probe not in _last_event:
            return
        now = time.time()
        with _lock:
            if now - _last_event[probe] < 1.0:
                return
            _last_event[probe] = now
        _write({"ts": now, "kind": "event", "probe": probe,
                "fields": {k: (v if isinstance(v, (int, float)) else str(v)[:200])
                           for k, v in fields.items()}})
    except Exception:
        pass


# --------------------------------------------------------------------------- snapshot
def _db_gauges(m: dict) -> None:
    total = 0
    main = 0
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            size = os.path.getsize(_db_path + suffix)
        except OSError:
            continue
        total += size
        if suffix == "":
            main = size
    m["db.bytes"] = total
    m["db.main_bytes"] = main

    t_before = _now_ms()
    if not _db_lock.acquire(timeout=_SNAPSHOT_LOCK_TIMEOUT_S):
        m["snapshot.lock_timeout"] = 1
        m["snapshot.lock_wait_ms"] = _now_ms() - t_before
        return
    try:
        t_acq = _now_ms()
        m["snapshot.lock_wait_ms"] = t_acq - t_before
        m["snapshot.lock_timeout"] = 0
        conn = sqlite3.connect(_db_path)
        try:
            m["rows.total"] = conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
            # Both use idx_heartbeats_token (covering scan of the token index).
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(c), 0) FROM "
                "(SELECT COUNT(*) AS c FROM heartbeats GROUP BY token)"
            ).fetchone()
            m["tokens.distinct"] = row[0]
            m["rows.max_per_token"] = row[1]
        finally:
            conn.close()
        m["snapshot.query_ms"] = _now_ms() - t_acq
    finally:
        _db_lock.release()


def snapshot() -> None:
    global _seq
    if not _started:
        return
    try:
        with _lock:
            m = dict(_counters)
            m.update(_wmax)
            m.update(_wcount)
            for k in _wmax:
                _wmax[k] = 0.0
            for k in _wcount:
                _wcount[k] = 0
            _seq += 1
            m["snapshot.seq"] = _seq
        m["snapshot.interval_s"] = _INTERVAL
        try:
            _db_gauges(m)
        except Exception as e:  # e.g. table not yet created
            event("probe_error", where="db_gauges", error=repr(e))
        _write({"ts": time.time(), "kind": "snapshot", "metrics": m})
    except Exception:
        pass


def _loop() -> None:
    while not _stop.wait(_INTERVAL):
        snapshot()


def start(db_path: str, db_lock) -> None:
    """Called from the app's startup path. Opens the log, writes the start line, takes
    the baseline snapshot and starts the daemon snapshot thread."""
    global _out, _db_path, _db_lock, _started, _thread
    if not ENABLED or _started:
        return
    try:
        log_path = os.environ.get("AGING_INSTRUMENT_LOG") or "aging_instrument.jsonl"
        _out = open(log_path, "a", buffering=1, encoding="utf-8")
        _db_path = os.path.abspath(db_path)
        _db_lock = db_lock
        _started = True
        _write({"ts": time.time(), "kind": "start",
                "probes": list(COUNTERS + WINDOW_MAX + WINDOW_COUNT + GAUGES)
                + ["event." + e for e in EVENTS],
                "pid": os.getpid(), "interval_s": _INTERVAL, "db_path": _db_path})
        snapshot()
        _thread = threading.Thread(target=_loop, name="aging-probe-snapshot", daemon=True)
        _thread.start()
    except Exception:
        pass


def stop() -> None:
    """Called from the app's shutdown path: final snapshot, then stop the thread."""
    if not ENABLED or not _started:
        return
    try:
        _stop.set()
        snapshot()
    except Exception:
        pass
