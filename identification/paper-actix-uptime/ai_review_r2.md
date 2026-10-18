# 1. Performance-Engineer Final Consolidated Report (verbatim)

# Adjudication report (static-analysis hypotheses requiring runtime confirmation)

Scope: `/tmp/aging-review-prlmblum/app/src/main.rs` and `/tmp/aging-review-prlmblum/app/Cargo.toml`. I did not run anything.

## F1. Unbounded in-memory store growth (merged: CR-1 + RE-1; CR-5 contributes)
- **Static validity:** Confirmed. `Db` (L13) is only ever inserted into, at L97–98. The file has no remove, retain, clear, TTL, cap or background task. The state is created once at L150–152 and lives for the whole process.
- **Standalone or merged:** Merged: CR-1, RE-1. CR-5 (untrimmed keys) is folded in as an amplifier, because padded variants create extra distinct keys (L79 trims only for the check; L97–98 store the raw values).
- **Performance relevance:** Direct.
- **Resource:** Process heap (the outer token map and the inner serviceId maps, with `String` keys and timestamp values).
- **Trigger:** POST `/heartbeat` with new `token` or `serviceId` values. Overwriting an existing key does not grow the map.
- **Cleanup and bounds:**
  - There is no cleanup.
  - The only bounds are the non-empty check and actix's default JSON body limit.
  - The two reviewers disagree on that limit (about 32 KiB vs about 2 MB). I can't settle it from this code. `JsonConfig::default()` at L156 does not override it, and the actual value depends on the actix-web 4.x version resolved. Either way it bounds the size of one key, not the number of keys.
  - Growth is therefore limited only by memory.
- **Direct evidence:** The insert-only code paths, and that no code ever shrinks the maps.
- **Assumptions:** Key cardinality in production, such as churning or unique IDs. If the set of tokens and serviceIds is small and fixed, the store plateaus. Also the allocator behavior, the host memory limit, and the actix body limit.
- **Mechanism class:** Cumulative resource retention.
- **Aging relevance:** Static aging mechanism supported. All four elements are present: a repeatable trigger, persistent accumulation, no cleanup, and plausible degradation over a long run, namely rising RSS, rehash pauses under the lock, and eventual OOM.
- **Final severity and confidence:** Medium-high severity. It is high only if key cardinality is unbounded. Confidence high for the mechanism, medium for real-world impact.

## F2. Sort and JSON serialization under the global `std::sync::Mutex` (merged: CR-2 + RE-2)
- **Static validity:** Confirmed.
  - In `get_services`, the guard from L116 lives to function return. The clone loop (L128–135), `sort_by` (L138) and `.json()` (L140) all run inside the critical section.
  - `heartbeat` holds its guard through response construction (L100–102), but that work is trivial.
- **Standalone or merged:** Merged: CR-2, RE-2.
- **Performance relevance:** Conditional. It only matters with large inner maps and concurrent traffic.
- **Resource:** The single global mutex and the actix worker threads. `std::sync::Mutex::lock` blocks the OS thread, which stalls the other connections on that worker.
- **Trigger:** `/services` for a token with many serviceIds, concurrent with other requests.
- **Cleanup and bounds:** None on hold time. It scales as O(n log n) in the size of one token's map. There is no `.await` inside the lock, so there is no await-point deadlock. The lock is released on return.
- **Direct evidence:** The guard scope and the work performed under it.
- **Assumptions:** Inner-map sizes, worker count (actix defaults to the number of cores), and contention levels. The slowdown is only progressive if F1 grows the maps.
- **Mechanism class:** Transient or repeated overhead. It is a per-request cost that scales with state size, not an accumulation or exhaustion in itself.
- **Aging relevance:** Non-aging performance fault on its own. Its cost grows only through F1, so it is a latency amplifier of F1, not an independent aging mechanism.
- **Final severity and confidence:** Medium-low severity, medium confidence. I rate it below CR-2's "med / high" because the impact depends entirely on map size.

## F3. Permanent poisoned-mutex outage (merged: CR-6 + RE-3)
- **Static validity:** Qualified. Both lock sites return 500 on `Err` (L87–94, L116–123), and nothing recovers from poison. However, no panic path is evident inside the critical sections. Allocation failure aborts rather than panics, and the sort comparator is a plain `String::cmp`.
- **Standalone or merged:** Merged: CR-6, RE-3.
- **Performance relevance:** Conditional, and weak. It is an availability fault, not a performance one.
- **Resource:** The global mutex state.
- **Trigger:** A panic while the guard is held. None is identified in the current code.
- **Cleanup and bounds:** The error is converted to a 500 but never cleared. Worker respawn would not clear the poison, because the state is shared.
- **Direct evidence:** The error-handling branches. The panic source is hypothetical.
- **Mechanism class:** Unsupported as a performance mechanism, since there is no repeatable trigger. If it did occur it would be a latent availability fault, not a progressive one.
- **Aging relevance:** Unsupported. It has no repeatable trigger and no accumulation.
- **Final severity and confidence:** Low severity, low confidence.

## F4. Timestamp ordering and clock handling (CR-3, CR-4)
- **Static validity:**
  - CR-3 is qualified. The timestamp is taken at L85 before the lock at L87, so a stale value can overwrite a newer one in a race. The window is small and has second resolution.
  - CR-4 is qualified on the clock-source claim: `SystemTime` is not monotonic, and the epoch fallback at L44 is real. Its claim about L49 is correct but harmless, because the dead branch only affects pre-epoch times.
- **Performance relevance:** None. These are correctness and data-quality issues with no runtime resource effect.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity and confidence:** Informational, medium confidence. They are excluded from the performance findings, and I don't count them as a separate finding beyond this note.

## Rejected or merged
- CR-5 is merged into F1 as an amplifier only. Standalone it is a correctness issue, which makes it unrelated to performance.

## SELF-SOURCED — UNVALIDATED
- None noted.

---

# 2. Appendix: Raw Phase-1 Handoffs (unedited)

## Appendix A: code-reviewer (CR-*)

No `unsafe` blocks exist in the target, so the triage section is left out. I reviewed both files in the target: `/tmp/aging-review-prlmblum/app/src/main.rs` and `/tmp/aging-review-prlmblum/app/Cargo.toml`.

---

**CR-1: Unbounded growth of the shared in-memory store**
- **File / lines:** `/tmp/aging-review-prlmblum/app/src/main.rs` L13, L97–98 (initialized at L150–152)
- **Fault and evidence:**
  - `type Db = Mutex<HashMap<String, HashMap<String, String>>>;` (L13)
  - `db.entry(req.token.clone()).or_insert_with(HashMap::new);` (L97)
  - `service_map.insert(req.serviceId.clone(), timestamp);` (L98)
  - Nothing anywhere in the file calls `remove`, `retain`, `clear`, applies a TTL or enforces a capacity.
- **Execution path:** POST `/heartbeat` → `heartbeat` (L74) → the entry is inserted at L97–98.
- **Affected state:** The process-wide `AppState.db` HashMap (outer token map and inner per-token maps), which lives for the whole process through `web::Data` (L150).
- **Triggering conditions:** A steady stream of heartbeats with new `token` or `serviceId` values: many clients, rotating IDs, or junk or adversarial input.
- **Existing bounding logic:** The only checks are non-empty validation (L79) and actix's default `JsonConfig` payload limit. That limit is not overridden at L156, so the default of about 32 KiB applies. It caps the size of one key, not the number of keys. Entries are never evicted, so memory only grows. HashMap capacity also never shrinks.
- **Runtime consequence:** Resident memory grows monotonically over uptime, which can end in out-of-memory termination. Each entry can hold up to about 2×32 KiB of key data, so growth per request can be large.
- **Severity / Confidence:** high / high
- **Needs runtime validation:** The real cardinality of tokens and services in production, and the effective payload limit for the actix-web 4.x version in use.

---

**CR-2: Global blocking `std::sync::Mutex` held across sort and JSON serialization in an async handler**
- **File / lines:** `/tmp/aging-review-prlmblum/app/src/main.rs` L116–141 (and L87–102)
- **Fault and evidence:**
  - `let db = match data.db.lock()` (L116) takes the single global lock.
  - The guard `db` stays alive until the function returns. While it is held, the code:
    - clones every entry (L129–134)
    - sorts with `response_list.sort_by(...)` (L138), which is O(n log n)
    - serializes the body with `HttpResponse::Ok().json(response_list)` (L140)
  - None of this work needs the lock after L135.
  - In `heartbeat`, the guard is likewise held through response construction at L100–102.
- **Execution path:** POST `/services` → `get_services` → `lock()` at L116 → copy, sort and serialize → the guard is dropped on return. Every concurrent `/heartbeat` and `/services` request on any worker blocks inside `lock()` (L87 / L116).
- **Affected state:** The single `Mutex` around all data, and the actix worker threads. `std::sync::Mutex::lock` blocks the OS thread and stalls that worker's whole async event loop.
- **Triggering conditions:** A token with many services, together with concurrent traffic.
- **Existing bounding logic:** None. No lock scoping, no async-aware lock, no limit on response size.
- **Runtime consequence:** All requests are serialized on one global lock. Latency rises and throughput falls as per-token maps grow, which compounds CR-1. Worker threads stall, and unrelated tokens' requests are delayed as well.
- **Severity / Confidence:** med / high
- **Needs runtime validation:** Lock hold time against map size, worker count, and how contended the lock is under realistic load.

---

**CR-3: Timestamp taken before the lock, so `lastNotification` can go backwards**
- **File / lines:** `/tmp/aging-review-prlmblum/app/src/main.rs` L85–98
- **Fault and evidence:**
  - `let timestamp = get_iso_time();` (L85) runs before `data.db.lock()` (L87).
  - `service_map.insert(req.serviceId.clone(), timestamp);` (L98) overwrites without checking order.
- **Execution path:** Two concurrent `/heartbeat` requests for the same (token, serviceId) on different workers. Request A reads the time T1. Request B reads T2 > T1 and gets the lock first. A then writes T1 over T2.
- **Affected state:** The per-service `lastNotification` value.
- **Triggering conditions:** Concurrent or retried heartbeats for the same service that cross a one-second boundary while the lock is contended (more likely given CR-2).
- **Existing bounding logic:** The mutex makes each write atomic but does not order the writes. There is no comparison before overwriting.
- **Runtime consequence:** The stored last-notification time can regress, so a live service may look stale or down to consumers.
- **Severity / Confidence:** low / med
- **Needs runtime validation:** How often heartbeats for the same service actually overlap concurrently.

---

**CR-4: Wall-clock (`SystemTime`) timestamps with a silent fallback to the epoch**
- **File / lines:** `/tmp/aging-review-prlmblum/app/src/main.rs` L42–49
- **Fault and evidence:**
  - `SystemTime::now()` (L43) is not monotonic.
  - `duration_since(UNIX_EPOCH).unwrap_or_default()` (L44) turns a clock set before the epoch into `1970-01-01T00:00:00Z` with no error.
  - `z` is a `u64` (L48), so `if z >= 0 { z } else { z - 146096 }` (L49) always takes the first branch. The negative-era handling is dead code; this is not a fault for times after the epoch.
- **Execution path:** Every `/heartbeat` → `get_iso_time()` (L85).
- **Affected state:** The stored `lastNotification` strings.
- **Triggering conditions:** NTP step adjustments, manual clock changes, or VM/WSL clock skew.
- **Existing bounding logic:** None. There is no monotonic source and no check on ordering.
- **Runtime consequence:** Timestamps can jump backwards or forwards, or be stored as the epoch, which corrupts liveness reporting.
- **Severity / Confidence:** low / med
- **Needs runtime validation:** How often the host clock is adjusted.

---

**CR-5: Validation uses `trim()` but stores the untrimmed keys**
- **File / lines:** `/tmp/aging-review-prlmblum/app/src/main.rs` L79, L97–98, L110, L128
- **Fault and evidence:**
  - Validation checks `req.serviceId.trim().is_empty() || req.token.trim().is_empty()` (L79).
  - Storage uses the raw `req.token.clone()` (L97) and `req.serviceId.clone()` (L98).
  - Lookup uses the raw `db.get(&req.token)` (L128).
- **Execution path:** A heartbeat with `" svc"` or `"tok "` creates an entry separate from `"svc"` / `"tok"`. A later `/services` call with the trimmed token does not find it.
- **Affected state:** Key identity in `AppState.db`.
- **Triggering conditions:** Clients sending leading or trailing whitespace, or inconsistent padding.
- **Existing bounding logic:** The trim is applied only to the emptiness check.
- **Runtime consequence:** Duplicate or orphaned entries (which add to CR-1) and services missing from the results returned for a token.
- **Severity / Confidence:** low / med
- **Needs runtime validation:** Whether real clients send whitespace-padded identifiers.

---

**CR-6: A poisoned mutex permanently disables the service**
- **File / lines:** `/tmp/aging-review-prlmblum/app/src/main.rs` L87–94, L116–123
- **Fault and evidence:** `Err(_) => { return HttpResponse::InternalServerError()... }` appears at both lock sites. A poisoned `std::sync::Mutex` stays poisoned, and nothing calls `into_inner` or `clear_poison`.
- **Execution path:** If a panic ever happens while the guard is held (L97–102 or L126–140), every later `lock()` returns `Err`, and every request to both endpoints returns 500.
- **Affected state:** The global `Db` mutex and the availability of the whole service.
- **Triggering conditions:** A panic inside a critical section. Likely sources are low; allocation failure usually aborts rather than panics. This makes the finding latent.
- **Existing bounding logic:** The error is caught and turned into a 500, but the service never recovers and nothing is logged.
- **Runtime consequence:** The process stays up but the service is permanently down until restart, and health checks may not catch it.
- **Severity / Confidence:** low / low
- **Needs runtime validation:** Whether any panic path can actually be reached inside the critical sections in the deployed build.

## Appendix B: rust-engineer (RE-*)

## RE-1
- **File and lines:** /tmp/aging-review-prlmblum/app/src/main.rs, lines 13, 97-98 (state declared at 150-152)
- **Fault:** The store is `Mutex<HashMap<String, HashMap<String, String>>>`. Every heartbeat runs `db.entry(req.token.clone()).or_insert_with(HashMap::new)` and `service_map.insert(req.serviceId.clone(), timestamp)`. Nothing ever removes, expires, or caps an outer token entry or an inner serviceId entry.
- **Execution path:** POST /heartbeat, then `heartbeat()`, then the insert under the global lock. The state is created once in `main` (150-152) and shared by all workers through `web::Data`, so it lives for the whole process.
- **Affected state or resource:** Process heap. The map keeps one entry per distinct (token, serviceId) pair for the life of the process.
- **Triggering conditions:** Any workload with many distinct tokens or serviceIds, such as churning instance IDs or per-request unique IDs. The only per-request limit is actix's default JSON payload limit of about 2 MB (`JsonConfig::default()`, line 156). The only validation is a non-empty check on lines 79-83.
- **Existing cleanup or bounding logic:** None. There is no TTL, eviction, background task, or size cap. Overwriting an existing key does not grow the map, but new keys always do. The cleanup is not sufficient.
- **Runtime consequence:** Monotonic memory growth, with HashMap rehash latency spikes while the lock is held. Eventually the process is OOM-killed and all state is lost. Growth also makes `get_services` slower for very large inner maps.
- **Severity / confidence:** med / high
- **Assumptions to validate:** That key cardinality is unbounded in production, and the growth rate against available memory.

## RE-2
- **File and lines:** /tmp/aging-review-prlmblum/app/src/main.rs, lines 116-140
- **Fault:** In `get_services`, the `MutexGuard` named `db` (line 116) is never dropped explicitly and has no inner scope. It stays alive until the function returns. That means the `sort_by` on line 138 and `HttpResponse::Ok().json(response_list)` on line 140 (JSON serialization into the response body) both run while the global lock is held. The clone loop (129-134) also runs under the lock.
- **Execution path:** POST /services, then lock at 116, then clone every entry for the token (128-135), then sort (138), then serialize (140), then the guard drops at function exit.
- **Affected state or resource:** The single global `std::sync::Mutex`, shared by all actix worker threads (one per CPU core by default). Blocking it also blocks those workers' async executors.
- **Triggering conditions:** A token with many serviceIds (see RE-1), combined with concurrent /heartbeat and /services calls. Each heartbeat needs the same lock at line 87.
- **Existing cleanup or bounding logic:** The lock has no await inside it, so there is no deadlock across an await point. But nothing bounds the hold time. Work that doesn't need the lock (sort and serialize) is inside the critical section, so the hold time grows as O(n log n) in the size of the token's map.
- **Runtime consequence:** Lock convoy and head-of-line blocking. Heartbeats and reads from unrelated tokens stall. A blocked `std::sync::Mutex::lock()` parks the whole worker thread, so other connections on that worker stall too, and tail latency rises.
- **Severity / confidence:** med / med
- **Assumptions to validate:** Typical inner-map sizes, and the contention level under real concurrency. For small maps the effect is negligible.

## RE-3
- **File and lines:** /tmp/aging-review-prlmblum/app/src/main.rs, lines 87-94 and 116-123
- **Fault:** Both handlers treat `lock()` returning `Err` (a poisoned mutex) as a permanent 500, with no `into_inner()` recovery. Once any thread panics while holding the guard, every later request to either endpoint fails. Code that runs under the lock includes `HashMap` insert and entry operations, `clone()` calls, and the sort comparator. Allocation failure in `clone` or `insert` aborts, and a normal panic is unlikely there, but any panic in those sections poisons the lock.
- **Execution path:** A panic under the guard at line 97/98, or at 129-138, poisons `data.db`. Every subsequent `data.db.lock()` returns `Err` and the handler returns 500 (lines 90-92, 119-121).
- **Affected state or resource:** The entire shared store. All tokens become unavailable at once, and the whole service is down until restart.
- **Triggering conditions:** A panic inside a critical section. Candidates are an internal panic in the std HashMap or the sort, or a panic from a future code path that runs under the guard. In the current code the likelihood is low.
- **Existing cleanup or bounding logic:** The error branches stop the handler from panicking, but there is no recovery or reset. actix-web respawns a worker thread that panics, but the poisoned `Arc` state is shared by all workers, so respawning does not clear the poison.
- **Runtime consequence:** Total, persistent service outage with every request returning 500. The data is also lost on the restart needed to recover.
- **Severity / confidence:** low / low
- **Assumptions to validate:** That a panic can actually occur under the guard. Reading the code, no panic path is evident.