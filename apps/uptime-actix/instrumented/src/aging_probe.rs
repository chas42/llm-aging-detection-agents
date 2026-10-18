//! Aging instrumentation probe for finding paper-actix-uptime-F1.
//!
//! Measurement code only: it never changes application behavior. Everything is gated on
//! env AGING_INSTRUMENT=1; when disabled every hook returns immediately after one relaxed
//! atomic load. Output: JSONL at $AGING_INSTRUMENT_LOG (start / snapshot / event records),
//! snapshot every $AGING_SNAPSHOT_INTERVAL seconds (default 10) from one std::thread.
//!
//! Bounded state: only fixed-size atomics (cumulative counters + windowed maxima reset at
//! every snapshot). Resource gauges are read from the snapshot thread by briefly taking the
//! application's own Mutex (same lock the handlers use); a poisoned lock skips the gauges.
//! Standard library only (no new crates).

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use actix_web::web;

use crate::AppState;

static ENABLED: AtomicBool = AtomicBool::new(false);

/// Code paths with lock timing.
pub const HEARTBEAT: usize = 0;
pub const SERVICES: usize = 1;

// Cumulative counters (never reset).
static CALLS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
static LOCKED: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
static ROWS_TOTAL: AtomicU64 = AtomicU64::new(0);
static POISONED: AtomicU64 = AtomicU64::new(0);

// Windowed aggregates (reset at each snapshot), nanoseconds / counts.
static WAIT_MAX_NS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
static HOLD_MAX_NS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
static HOLD_SUM_NS: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
static WIN_LOCKED: [AtomicU64; 2] = [AtomicU64::new(0), AtomicU64::new(0)];
static ROWS_MAX: AtomicU64 = AtomicU64::new(0);

// Rate limit for events: last event second per probe.
static LAST_EVENT_S: AtomicU64 = AtomicU64::new(0);

static OUT: Mutex<Option<File>> = Mutex::new(None);

const PATH_NAMES: [&str; 2] = ["heartbeat", "services"];

const PROBES: &[&str] = &[
    "calls.heartbeat",
    "calls.heartbeat_locked",
    "calls.services",
    "calls.services_locked",
    "services.rows_total",
    "lock_poisoned.count",
    "lock_wait_ms.max.heartbeat",
    "lock_wait_ms.max.services",
    "lock_hold_ms.max.heartbeat",
    "lock_hold_ms.max.services",
    "lock_hold_ms.mean.heartbeat",
    "lock_hold_ms.mean.services",
    "services.rows.max",
    "tokens.distinct",
    "service_ids.total",
    "service_ids.max_per_token",
    "probe.gauge_lock_wait_ms",
    "probe.gauge_scan_ms",
    "proc.rss_bytes",
    "event:lock_poisoned",
];

#[inline]
fn enabled() -> bool {
    ENABLED.load(Ordering::Relaxed)
}

fn now_ts() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn ns_to_ms(ns: u64) -> f64 {
    ns as f64 / 1_000_000.0
}

fn dur_ns(d: Duration) -> u64 {
    u64::try_from(d.as_nanos()).unwrap_or(u64::MAX)
}

fn write_line(line: &str) {
    // Never panic: a poisoned output lock or I/O error just drops the line.
    if let Ok(mut g) = OUT.lock() {
        if let Some(f) = g.as_mut() {
            let _ = f.write_all(line.as_bytes());
            let _ = f.write_all(b"\n");
            let _ = f.flush();
        }
    }
}

/// Startup hook: called once from main with a clone of the app's shared state handle.
pub fn init(state: web::Data<AppState>) {
    if std::env::var("AGING_INSTRUMENT").ok().as_deref() != Some("1") {
        return;
    }
    let path = match std::env::var("AGING_INSTRUMENT_LOG") {
        Ok(p) if !p.is_empty() => p,
        _ => "aging_instrument.jsonl".to_string(),
    };
    let file = match OpenOptions::new().create(true).append(true).open(&path) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("[aging_probe] cannot open {}: {} (probe disabled)", path, e);
            return;
        }
    };
    if let Ok(mut g) = OUT.lock() {
        *g = Some(file);
    }
    let interval = std::env::var("AGING_SNAPSHOT_INTERVAL")
        .ok()
        .and_then(|s| s.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite() && *v > 0.0)
        .unwrap_or(10.0);

    let probes: Vec<String> = PROBES.iter().map(|p| format!("\"{}\"", p)).collect();
    write_line(&format!(
        "{{\"ts\": {:.6}, \"kind\": \"start\", \"probes\": [{}], \"interval_s\": {}}}",
        now_ts(),
        probes.join(", "),
        interval
    ));
    ENABLED.store(true, Ordering::Relaxed);

    let spawned = std::thread::Builder::new()
        .name("aging-probe".into())
        .spawn(move || loop {
            snapshot(&state);
            std::thread::sleep(Duration::from_secs_f64(interval));
        });
    if let Err(e) = spawned {
        eprintln!("[aging_probe] cannot spawn snapshot thread: {}", e);
    }
}

fn read_rss_bytes() -> Option<u64> {
    let s = std::fs::read_to_string("/proc/self/status").ok()?;
    let line = s.lines().find(|l| l.starts_with("VmRSS:"))?;
    let kb: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
    Some(kb * 1024)
}

fn snapshot(state: &web::Data<AppState>) {
    let mut m: Vec<(String, String)> = Vec::with_capacity(PROBES.len());
    let mut push = |k: &str, v: String| m.push((k.to_string(), v));

    // (a) cumulative counters
    for i in 0..2 {
        push(&format!("calls.{}", PATH_NAMES[i]), CALLS[i].load(Ordering::Relaxed).to_string());
        push(
            &format!("calls.{}_locked", PATH_NAMES[i]),
            LOCKED[i].load(Ordering::Relaxed).to_string(),
        );
    }
    push("services.rows_total", ROWS_TOTAL.load(Ordering::Relaxed).to_string());
    push("lock_poisoned.count", POISONED.load(Ordering::Relaxed).to_string());

    // (c) windowed latency aggregates, reset now
    for i in 0..2 {
        let wait = WAIT_MAX_NS[i].swap(0, Ordering::Relaxed);
        let hold = HOLD_MAX_NS[i].swap(0, Ordering::Relaxed);
        let sum = HOLD_SUM_NS[i].swap(0, Ordering::Relaxed);
        let n = WIN_LOCKED[i].swap(0, Ordering::Relaxed);
        let mean = if n > 0 { ns_to_ms(sum) / n as f64 } else { 0.0 };
        push(&format!("lock_wait_ms.max.{}", PATH_NAMES[i]), format!("{:.6}", ns_to_ms(wait)));
        push(&format!("lock_hold_ms.max.{}", PATH_NAMES[i]), format!("{:.6}", ns_to_ms(hold)));
        push(&format!("lock_hold_ms.mean.{}", PATH_NAMES[i]), format!("{:.6}", mean));
    }
    push("services.rows.max", ROWS_MAX.swap(0, Ordering::Relaxed).to_string());

    // (b) resource gauges, under the app's own mutex (brief O(#tokens) scan)
    let t0 = Instant::now();
    if let Ok(db) = state.db.lock() {
        let waited = t0.elapsed();
        let t1 = Instant::now();
        let tokens = db.len() as u64;
        let mut total: u64 = 0;
        let mut max_per: u64 = 0;
        for inner in db.values() {
            let l = inner.len() as u64;
            total += l;
            if l > max_per {
                max_per = l;
            }
        }
        let scan = t1.elapsed();
        drop(db);
        push("tokens.distinct", tokens.to_string());
        push("service_ids.total", total.to_string());
        push("service_ids.max_per_token", max_per.to_string());
        push("probe.gauge_lock_wait_ms", format!("{:.6}", ns_to_ms(dur_ns(waited))));
        push("probe.gauge_scan_ms", format!("{:.6}", ns_to_ms(dur_ns(scan))));
    }
    // poisoned lock: gauges skipped (into_inner not used, so the poison is left untouched)

    if let Some(rss) = read_rss_bytes() {
        push("proc.rss_bytes", rss.to_string());
    }

    let body: Vec<String> = m.iter().map(|(k, v)| format!("\"{}\": {}", k, v)).collect();
    write_line(&format!(
        "{{\"ts\": {:.6}, \"kind\": \"snapshot\", \"metrics\": {{{}}}}}",
        now_ts(),
        body.join(", ")
    ));
}

// ---------------------------------------------------------------------------------------
// Hooks called from the handlers
// ---------------------------------------------------------------------------------------

/// Handler entry: cumulative call counter.
#[inline]
pub fn call(path: usize) {
    if enabled() {
        CALLS[path].fetch_add(1, Ordering::Relaxed);
    }
}

/// Taken immediately before `data.db.lock()`.
#[inline]
pub fn before_lock() -> Option<Instant> {
    if enabled() {
        Some(Instant::now())
    } else {
        None
    }
}

/// Called right after the lock was acquired; returns a guard that records the hold time
/// when dropped. Declare it after the MutexGuard so it drops just before the guard.
#[inline]
pub fn locked(path: usize, t_wait: Option<Instant>) -> HoldTimer {
    match t_wait {
        Some(t0) => {
            let now = Instant::now();
            WAIT_MAX_NS[path].fetch_max(dur_ns(now - t0), Ordering::Relaxed);
            LOCKED[path].fetch_add(1, Ordering::Relaxed);
            HoldTimer { path, start: Some(now) }
        }
        None => HoldTimer { path, start: None },
    }
}

pub struct HoldTimer {
    path: usize,
    start: Option<Instant>,
}

impl Drop for HoldTimer {
    fn drop(&mut self) {
        if let Some(s) = self.start {
            let ns = dur_ns(s.elapsed());
            HOLD_MAX_NS[self.path].fetch_max(ns, Ordering::Relaxed);
            HOLD_SUM_NS[self.path].fetch_add(ns, Ordering::Relaxed);
            WIN_LOCKED[self.path].fetch_add(1, Ordering::Relaxed);
        }
    }
}

/// Rows built by get_services for the requested token (before sort/serialization).
#[inline]
pub fn services_rows(n: usize) {
    if enabled() {
        let n = n as u64;
        ROWS_MAX.fetch_max(n, Ordering::Relaxed);
        ROWS_TOTAL.fetch_add(n, Ordering::Relaxed);
    }
}

/// Rare path: the app's mutex was poisoned (handler returns 500). Rate-limited to 1/s.
pub fn lock_poisoned(path: usize) {
    if !enabled() {
        return;
    }
    POISONED.fetch_add(1, Ordering::Relaxed);
    let ts = now_ts();
    let sec = ts as u64;
    let last = LAST_EVENT_S.load(Ordering::Relaxed);
    if sec > last
        && LAST_EVENT_S
            .compare_exchange(last, sec, Ordering::Relaxed, Ordering::Relaxed)
            .is_ok()
    {
        write_line(&format!(
            "{{\"ts\": {:.6}, \"kind\": \"event\", \"probe\": \"lock_poisoned\", \"fields\": {{\"path\": \"{}\", \"count\": {}}}}}",
            ts,
            PATH_NAMES[path],
            POISONED.load(Ordering::Relaxed)
        ));
    }
}
