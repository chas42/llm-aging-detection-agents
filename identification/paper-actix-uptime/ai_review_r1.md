# 1. Performance-engineer final consolidated report

# Adjudication report: performance and aging (static-analysis hypotheses only)

I verified the findings against `/tmp/aging-review-4dywjbtw/app/src/main.rs` (175 lines) and `/tmp/aging-review-4dywjbtw/app/Cargo.toml`. Nothing was executed. Every conclusion below is a static-analysis hypothesis that needs runtime confirmation.

## F1. Heartbeat store grows without bound (merges CR-1 and RE-1)

- **Static validity:** Confirmed.
- **Standalone or merged:** Merged. The two reports describe the same fault.
- **Performance relevance:** Direct.
- **Mechanism class:** Cumulative resource retention.
- **Aging relevance:** Static aging mechanism supported, with a workload-dependent rate.
- **Affected resource:** Process heap, held by `AppState.db`. This is the outer token keys, the inner serviceId keys, and one 20-byte timestamp `String` per pair.
- **Direct code evidence:**
  - The type is declared at L13, and the state is created once at L150-152.
  - L97 does `entry(req.token.clone()).or_insert_with(...)`, and L98 does `insert(req.serviceId.clone(), timestamp)`.
  - No `remove`, `retain`, `clear`, TTL, capacity cap, or background task exists anywhere in the file.
- **Trigger:** A heartbeat with a previously unseen token, or with a new serviceId under a known token. Any client can supply these values. The only check is the non-empty-after-trim test at L79.
- **Bounds:**
  - A repeat heartbeat for an existing (token, serviceId) pair replaces a fixed-size value and does not grow the store. Growth is therefore proportional to the number of distinct keys, not to request count. A stable service population plateaus.
  - Each key is bounded per request by the JSON body limit. `JsonConfig::default()` is set at L156. I believe the actix-web 4 default is 2 MiB, so RE-1's recollection is probably right and CR-1's "32 KiB" is probably wrong. I did not verify this in the resolved dependency source. Either way, the limit caps a single request and not total store size.
  - HashMap capacity is never released, but nothing removes entries anyway.
- **Assumptions, not code evidence:** Real key cardinality, whether an OOM limit or a supervisor restart exists, and per-entry allocator overhead.
- **Final severity and confidence:** Medium severity, high confidence. The code defect is certain. Whether it causes degradation depends on key churn.

## F2. Global lock held during copy, sort and serialization in `/services` (merges CR-2, CR-6 and RE-2)

- **Static validity:** Confirmed.
- **Standalone or merged:** Merged.
  - CR-2 and RE-2 are the same lock-scope issue.
  - CR-6 (unbounded per-request copy and sort) is the same work seen from the CPU and allocation side.
  - It stays a cost even without the lock, but the lock is what turns it into cross-request impact.
- **Performance relevance:** Conditional.
- **Mechanism class:** Transient or repeated overhead. It contends for a shared lock, which is a finite resource, but the lock is released after every request, so nothing is exhausted permanently.
- **Aging relevance:** Non-aging performance fault on its own. It is conditionally plausible as a degradation amplifier, because hold time grows with per-token map size, and F1 is the only thing that makes that size grow. The accumulation element comes entirely from F1.
- **Affected resource:** The single `std::sync::Mutex` (L5, L13) and the actix worker threads. Handlers contain no `.await`, so a thread waiting on `lock()` blocks its whole worker.
- **Direct code evidence:**
  - The guard `db` is taken at L116 and lives to the end of the function.
  - The clone loop (L128-135), `sort_by` (L138), and `.json(response_list)` (L140) all run while it is held.
  - `heartbeat` takes the same lock (L87).
- **Cost:** O(n) allocation and copying, O(n log n) sorting, and O(n) serialization, where n is the number of services under the requested token. The lock is global, so other tokens' requests also wait.
- **Existing cleanup and bounds:** The lock is not held across an await, so there is no deadlock and the hold time is finite. There is no pagination, caching, or output cap. The `Vec::new()` without a capacity hint (L126) adds only minor reallocation.
- **Assumptions, not code evidence:**
  - Per-token map sizes, the request rate, and the worker count (the actix default is the CPU count).
  - Whether hold time is large enough to matter. A claim of "throughput collapses" (CR-2) is only plausible for large maps and high concurrency.
  - Rejection of one claim: CR-2's "one large request delays every tenant" is true in direction but unquantified.
- **Final severity and confidence:** Medium severity. Confidence is high for the lock-scope fact and medium for any practical impact.

## F3. Validation uses trimmed strings, storage and lookup use raw strings (CR-4)

- **Static validity:** Confirmed. L79 and L110 test `trim()`, but L97, L98 and L128 use the raw values.
- **Standalone or merged:** Standalone.
- **Performance relevance:** None as a direct effect. Whitespace-variant keys can add a few extra entries to F1, but that is marginal and needs no separate mechanism.
- **Mechanism class:** No performance impact (a functional correctness issue).
- **Aging relevance:** Unrelated to performance. It has no repeatable accumulation of its own beyond what F1 already covers.
- **Direct code evidence:** The trim-versus-raw mismatch.
- **Assumption:** Whether real clients send padded values.
- **Final severity and confidence:** Low severity, high confidence on validity. CR-4's stated medium confidence applies only to whether it occurs in practice.

## F4. Mutex poisoning leads to permanent 500 responses (merges CR-5 and RE-3)

- **Static validity:** Qualified. The poisoned-lock handling is real (L87-94, L116-123, with no recovery path). A trigger is not demonstrated.
- **Standalone or merged:** Merged.
- **Performance relevance:** Conditional, and speculative. It is an availability fault, not a performance fault.
- **Mechanism class:** Unsupported as a performance or aging mechanism.
- **Aging relevance:** Unsupported.
  - There is no repeatable trigger. No panic site exists under the lock: HashMap entry, insert and clone, a string-compare `sort_by`, and serde serialization of plain strings.
  - Nothing accumulates.
  - The state is a one-time failure, not progressive degradation.
- **Bounds:**
  - Allocation failure aborts rather than unwinding.
  - `Cargo.toml` sets no `panic=abort` profile, so unwinding is the default.
  - Whether actix respawns a panicked worker is a framework assumption.
- **Final severity and confidence:** Low severity, low confidence. Both reviewers agree on this, but that agreement does not add evidence.

## F5. Timestamp read before the lock, so a stored value can go backwards (CR-3)

- **Static validity:** Confirmed as a logic ordering race. `get_iso_time()` is called at L85, before the lock at L87, and L98 overwrites unconditionally. Timestamps have one-second resolution, so the window is small.
- **Standalone or merged:** Standalone.
- **Performance relevance:** None.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Assumptions:** The frequency of concurrent heartbeats for the same key, and the stability of the host clock.
- **Final severity and confidence:** Low severity, medium confidence in practical occurrence.

## Observations rejected as findings

- **`regex` dependency unused (Cargo.toml L11):** It has no runtime effect, which both reviewers acknowledge.
- **`z >= 0` on a `u64` (L49):** This is a harmless always-true branch. The date arithmetic at L48-57 is a fixed-cost computation that does not accumulate anything.
- **CR-1's 32 KiB JSON body limit:** This is probably incorrect (see F1). It does not change the conclusion, because the limit is per request.

## SELF-SOURCED — UNVALIDATED

None.

## UNSAFE BLOCKS — TRIAGE

Both handoffs include this section. I reproduce each verbatim.

Handoff A:

There are no `unsafe` blocks in the target code. I read both `/tmp/aging-review-4dywjbtw/app/src/main.rs` and `/tmp/aging-review-4dywjbtw/app/Cargo.toml` in full and found no `unsafe` keyword.

Handoff B:

None. I found no `unsafe` blocks or functions in `src/main.rs`, the only Rust source file in `/tmp/aging-review-4dywjbtw/app`.

---

# 2. Appendix: raw Phase-1 handoffs

## Appendix A: code-reviewer (CR-*)

I reviewed `/tmp/aging-review-4dywjbtw/app/src/main.rs` (175 lines) and `/tmp/aging-review-4dywjbtw/app/Cargo.toml`. I only read the code; nothing was run. The service is a single-file actix-web app with two routes, `/heartbeat` and `/services`, and its only state is one in-memory map behind a global lock.

---

### CR-1: The heartbeat store only ever grows
- **File / lines:** `/tmp/aging-review-4dywjbtw/app/src/main.rs` L13, L15-17, L97-98, L150-152
- **Fault and evidence:** The store is `type Db = Mutex<HashMap<String, HashMap<String, String>>>;` (L13). It is created once with `Mutex::new(HashMap::new())` (L151) and lives for the whole process. Every heartbeat runs `db.entry(req.token.clone()).or_insert_with(HashMap::new)` (L97) and then `service_map.insert(req.serviceId.clone(), timestamp)` (L98). Nothing in the file ever calls `remove`, `retain`, `clear`, sets a TTL or a capacity limit, or evicts anything. No route deletes data.
- **Execution path:** `POST /heartbeat` → `heartbeat` (L74) → the check at L79 (only rejects empty or all-whitespace strings) → `get_iso_time` (L85) → lock (L87) → entry/insert (L97-98).
- **Affected state:** The process-wide `AppState.db` map, both the outer token keys and the inner service-ID keys. Each one is a heap-allocated `String` clone of the request data.
- **Triggering conditions:** Requests with many different `token` values or different `serviceId` values under a token. Any client can choose these freely, and every new pair adds a permanent entry. Services that are retired or tokens that are abandoned are never cleaned up, even with normal traffic.
- **Existing bounding logic and why it falls short:** actix's default `JsonConfig` body limit (32 KiB, still in effect because L156 only sets an error handler) caps the size of a single request. It does not cap how many entries build up. The L79 check rejects only empty or whitespace input. HashMap does not shrink by itself, so memory stays at its peak.
- **Runtime consequence:** Memory use keeps climbing for the life of the process and can end in the process being killed for running out of memory (OOM). It also makes `/services` scans more expensive under the lock (see CR-2).
- **Severity / confidence:** high / high
- **Needs runtime validation:** The real number of distinct tokens and service IDs in production. Whether an outside supervisor restarts the process. The per-entry overhead of the allocator and HashMap.

### CR-2: `/services` holds the global lock through building, sorting and serializing the response
- **File / lines:** `/tmp/aging-review-4dywjbtw/app/src/main.rs` L116-140 (the same lock is taken at L87-98)
- **Fault and evidence:** The guard `let db = match data.db.lock()` (L116) lives until the end of the function. While it is held, the handler loops over the whole service map for the token and clones two strings per entry (L128-134), sorts the result with `response_list.sort_by(...)` (L138), and then calls `HttpResponse::Ok().json(response_list)` (L140), which serializes the whole list to JSON. The guard is not dropped first (no `drop(db)` and no inner scope). The lock is a blocking `std::sync::Mutex` (L5), so threads waiting on it block their worker thread rather than yielding.
- **Execution path:** `POST /services` → `get_services` → lock (L116) → loop (L129) → sort (L138) → serialize (L140) → guard dropped when the function returns.
- **Affected state:** The single global `Mutex` around all heartbeat data. Every actix worker thread that runs `heartbeat` (L87) or `get_services` (L116).
- **Triggering conditions:** A token with many services (made possible by CR-1), combined with concurrent `/heartbeat` or `/services` traffic. The lock cost is O(n) copying + O(n log n) sorting + O(n) serialization, all while holding the lock.
- **Existing bounding logic and why it falls short:** There is no `.await` inside the locked section, so the lock is never held across a suspension point. That limits the hold time to the synchronous work, but the work grows with the map size and is unbounded (CR-1). There is only one lock for all tokens, so traffic for unrelated tokens also waits.
- **Runtime consequence:** All requests effectively take turns through one lock. Worker threads sit blocked, latency spikes, and throughput collapses under load. One large `/services` request delays heartbeats from every tenant.
- **Severity / confidence:** med / high
- **Needs runtime validation:** The largest per-token map sizes, the number of worker threads and the real level of concurrency. Profiling should show lock wait time.

### CR-3: The timestamp is taken before the lock, so a stored value can go backwards
- **File / lines:** `/tmp/aging-review-4dywjbtw/app/src/main.rs` L85-98 (and L43-44)
- **Fault and evidence:** `let timestamp = get_iso_time();` (L85) runs before `data.db.lock()` (L87). The value is then written unconditionally with `service_map.insert(req.serviceId.clone(), timestamp)` (L98), with no check against the value already stored. The clock is `SystemTime::now()` (L43), which can jump backwards. If the clock is before the epoch, `unwrap_or_default()` (L44) quietly gives 1970-01-01.
- **Execution path:** Two `heartbeat` requests run at the same time for the same (token, serviceId). Request A reads time T1, request B reads T2 > T1, B gets the lock first and writes T2, then A writes T1.
- **Affected state:** The `lastNotification` value for that (token, serviceId) pair.
- **Triggering conditions:** Concurrent heartbeats for the same key that land on either side of a one-second boundary (timestamps have one-second resolution, L45 and L59-62), or a backward step of the system clock such as an NTP correction.
- **Existing bounding logic and why it falls short:** The lock makes each write atomic, but it does not order writes by their timestamps, because the time is read outside the lock.
- **Runtime consequence:** `/services` can report an older `lastNotification` than the newest heartbeat actually received. A monitor reading that value could treat the service as stale and raise a false alert.
- **Severity / confidence:** low / med
- **Needs runtime validation:** How often heartbeats for the same key arrive concurrently, and how stable the host clock is.

### CR-4: Validation checks trimmed strings but the store uses the raw strings
- **File / lines:** `/tmp/aging-review-4dywjbtw/app/src/main.rs` L79, L97-98, L110, L128
- **Fault and evidence:** Validation calls `req.serviceId.trim().is_empty() || req.token.trim().is_empty()` (L79) and `req.token.trim().is_empty()` (L110). The store and the lookup use the untrimmed values: `db.entry(req.token.clone())` (L97), `insert(req.serviceId.clone(), ...)` (L98) and `db.get(&req.token)` (L128).
- **Execution path:** A heartbeat sent with `"token": "abc "` creates its own entry. A later `/services` call with `"token": "abc"` does not find it, and the reverse is also true.
- **Affected state:** How keys are identified in `AppState.db`.
- **Triggering conditions:** Clients that send leading or trailing whitespace, or that are inconsistent about it.
- **Existing bounding logic and why it falls short:** None. The trimmed value is used only to decide pass or fail.
- **Runtime consequence:** Heartbeats that look missing or are split across entries. `/services` returns an empty or partial list, and the store gains extra duplicate entries (adding to CR-1).
- **Severity / confidence:** low / med
- **Needs runtime validation:** Whether real clients ever send whitespace-padded values.

### CR-5: A poisoned lock turns every later request into a permanent 500 error
- **File / lines:** `/tmp/aging-review-4dywjbtw/app/src/main.rs` L87-94, L116-123
- **Fault and evidence:** Both handlers map `Err(_)` from `lock()` to `HttpResponse::InternalServerError()` (L89-93, L118-122). With `std::sync::Mutex`, once any thread panics while holding the guard, every later `lock()` returns `Err` until the process exits. Nothing calls `into_inner()` on the error or `clear_poison`, and nothing restarts the process.
- **Execution path:** A panic inside a locked section (L97-98 or L126-140), for example a failure inside `HashMap` growth, `sort_by` or serialization, poisons the mutex. From then on, every `heartbeat` and `get_services` call returns 500.
- **Affected state:** The global `Mutex` and the availability of the whole service.
- **Triggering conditions:** Any panic while the lock is held. Likely triggers are rare (allocation failure normally aborts the process instead of panicking), so this is hard to reach.
- **Existing bounding logic and why it falls short:** The error is handled without crashing, but it is never recovered from. actix catches the panic in the worker, so the process keeps running in a permanently broken state instead of restarting.
- **Runtime consequence:** The process stays up but fails every request until someone restarts it by hand.
- **Severity / confidence:** low / low
- **Needs runtime validation:** Whether any panic is actually reachable inside the locked sections under production conditions and dependency versions.

### CR-6: `/services` does an unbounded copy and sort on every request
- **File / lines:** `/tmp/aging-review-4dywjbtw/app/src/main.rs` L126-140
- **Fault and evidence:** Every call starts with an empty `Vec::new()` and no capacity hint (L126). It copies every entry with `sid.clone()` and `time.clone()` (L131-132) and re-sorts the whole list with `sort_by(... cmp ...)` (L138). Nothing is cached, there is no pagination and there is no limit on result size. A request body of a few bytes can make the server copy and serialize the entire per-token map.
- **Execution path:** `POST /services` → L128-140.
- **Affected state:** Heap churn and CPU on the worker thread, and the size of the response (all built while the lock is held, see CR-2).
- **Triggering conditions:** Large per-token maps (CR-1) queried often, for example by dashboards or monitors polling.
- **Existing bounding logic and why it falls short:** The request body limit applies only to input. The output size depends only on how big the stored map has grown, which nothing limits.
- **Runtime consequence:** Per-request CPU and memory grow linearly with map size and responses become very large, which amplifies work for the server. Combined with CR-2, it stalls all workers.
- **Severity / confidence:** med / med
- **Needs runtime validation:** The real per-token map sizes and how often `/services` is called.

---

### Other observations (not counted as findings)
- `get_iso_time` has a dead branch: `z` is a `u64`, so `if z >= 0` (L49) is always true. For dates after the epoch the calendar math (L48-57) matches the standard days-to-civil-date algorithm, and I found no correctness fault in it.
- `regex = "1"` (Cargo.toml L11) is never used in the code. It only adds build time and binary size, with no measurable effect at runtime.

### UNSAFE BLOCKS — TRIAGE
There are no `unsafe` blocks in the target code. I read both `/tmp/aging-review-4dywjbtw/app/src/main.rs` and `/tmp/aging-review-4dywjbtw/app/Cargo.toml` in full and found no `unsafe` keyword.

## Appendix B: rust-engineer (RE-*)

# Review of `/tmp/aging-review-4dywjbtw/app/src/main.rs`

The app is one file (`src/main.rs`, 175 lines). I found 3 findings, listed below as RE-1 to RE-3. I did not run anything.

## RE-1
- **File and lines:** `src/main.rs` 13, 87-98 (the `heartbeat` handler), 150-152 (state creation).
- **Fault:** The in-memory store `Mutex<HashMap<String, HashMap<String, String>>>` only ever grows. Line 97 does `db.entry(req.token.clone()).or_insert_with(HashMap::new)`. Line 98 does `service_map.insert(req.serviceId.clone(), timestamp)`. Nothing in the file removes a token or a service entry. There is no TTL, cap, or eviction.
- **Execution path:** `POST /heartbeat` calls `heartbeat`, which validates only that the strings are non-empty after trim (line 79). It then inserts a new outer or inner key for every distinct `token` or `serviceId`.
- **Affected state or resource:** Process heap, held by the single `AppState.db` that lives for the whole process.
- **Triggering conditions:** Any sustained traffic with many distinct tokens or service ids. Clients that generate ids dynamically, such as per-instance or per-request ids, trigger it. Key sizes are bounded only by actix's default JSON body limit (the `JsonConfig::default()` limit, 2 MB by my recollection). One request can therefore store a multi-megabyte `serviceId`/`token` pair.
- **Existing cleanup or bounding logic:** Only the JSON body size limit. It bounds each request, not the total size of the store, so it is not sufficient.
- **Runtime consequence:** Memory grows without bound until the process hits OOM or is killed, and all state is lost. Larger maps also make rehashing and the lock-held copy in RE-2 slower.
- **Severity and confidence:** Severity med. Confidence high.
- **Assumptions to validate:** The real key cardinality and traffic pattern. The actual default JSON payload limit in the resolved actix-web 4.x version. Whether the process is memory-limited.

## RE-2
- **File and lines:** `src/main.rs` 116-140 (the `get_services` handler). Compare lines 87-98.
- **Fault:** The `std::sync::Mutex` guard `db` (line 116) is held until the function returns. That covers the whole clone loop (lines 128-135), the `sort_by` (line 138), and `HttpResponse::Ok().json(response_list)` serialization (line 140). `heartbeat` takes the same global lock for every write. The guard is never dropped explicitly or scoped, so the O(n log n) sort and the JSON serialization run inside the critical section.
- **Execution path:** `POST /services` takes the lock, clones every `(serviceId, time)` pair for the token, sorts them, and serializes the result, all with the lock held. Concurrent `/heartbeat` and `/services` calls on all actix worker threads serialize behind it.
- **Affected state or resource:** The single global mutex `AppState.db`. Each actix worker thread blocks on `lock()` synchronously, which stalls that worker's whole single-threaded runtime. That includes every other connection multiplexed on that worker, not only this request.
- **Triggering conditions:** A token with many services (a large inner map), made worse by RE-1 growth. Concurrent heartbeat writers plus readers. The lock is global across all tokens, so one heavy token slows every token.
- **Existing cleanup or bounding logic:** None. The lock is never held across an `.await` (there are no awaits in the handlers), so it cannot deadlock. The contention and worker-stall risk is not mitigated.
- **Runtime consequence:** Tail latency spikes and throughput collapse under load. Workers stall on the blocking lock, so unrelated requests on the same worker are delayed too. There is no response size bound.
- **Severity and confidence:** Severity med. Confidence med.
- **Assumptions to validate:** Typical number of services per token. Request concurrency and the worker count (the actix default is the number of CPUs). Whether the work done under the lock is large enough, relative to the request rate, to matter.

## RE-3
- **File and lines:** `src/main.rs` 87-94, 116-123.
- **Fault:** Mutex poisoning is handled by returning 500 forever. Both handlers use `data.db.lock()` and map `Err(_)` to `InternalServerError` (lines 89-93 and 118-122). Nothing calls `into_inner()` or `clear_poison()`.
- **Execution path:** If any thread panics while holding the guard, the mutex is poisoned. Every later `/heartbeat` and `/services` request on every worker then returns 500 until the process restarts.
- **Affected state or resource:** The global `AppState.db` mutex and the whole service's availability.
- **Triggering conditions:** A panic inside a critical section. The code under the lock is `HashMap` entry/insert, clone, `sort_by`, and `json` serialization. A panic is unlikely there. Allocation failure aborts rather than unwinding, and the `sort_by` comparator is a plain string `cmp`. The trigger is therefore speculative.
- **Existing cleanup or bounding logic:** None for recovery.
- **Runtime consequence:** If it triggers, the service is permanently degraded and all stored data is effectively unreachable, with no self-healing.
- **Severity and confidence:** Severity low. Confidence low, because I found no concrete panic site inside the critical sections.
- **Assumptions to validate:** Whether any panic is reachable while the lock is held, for example through a panic in a dependency or a serialization panic. Whether the build uses `panic=abort`. That setting would make poisoning moot.

## Other notes
- No `tokio::spawn`, channels, `Arc` cycles, `mem::forget`, `Box::leak`, WebSockets, or streaming exist in this file.
- There are no `.await`s inside the handlers, so no lock is held across an await point.
- No graceful-shutdown configuration is set beyond actix's defaults: signal handling and a 30 s worker shutdown timeout. Nothing is persisted, so a restart loses all state.
- The date arithmetic in `get_iso_time` (lines 42-68) appears correct for dates after 1970. The `z >= 0` check on a `u64` is always true, which is a harmless no-op.

## UNSAFE BLOCKS — TRIAGE
None. I found no `unsafe` blocks or functions in `src/main.rs`, the only Rust source file in `/tmp/aging-review-4dywjbtw/app`.