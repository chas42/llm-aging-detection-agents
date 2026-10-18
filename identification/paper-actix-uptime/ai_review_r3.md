# 1. Performance-engineer final consolidated report (verbatim)

# Verification Report: static-analysis hypotheses that need runtime confirmation

I read `/tmp/aging-review-yooh1qkk/app/src/main.rs` and `Cargo.toml` in full. Nothing was executed.

## Consolidated findings

### F1. Unbounded, never-evicted in-memory store
- **Merged from:** CR-1 and RE-1.
- **Static validity:** confirmed.
- **Performance relevance:** direct.
- **Direct code evidence:**
  - The store is `Mutex<HashMap<String, HashMap<String, String>>>` (L13), created once (L150–152).
  - `heartbeat` only calls `entry().or_insert_with()` and `insert()` (L97–98).
  - There is no `remove`, `retain`, TTL or cap anywhere in the file.
  - The only validation is `trim().is_empty()` (L79).
  - Each new (token, serviceId) pair keeps cloned key strings plus a 20-byte timestamp string.
  - Re-heartbeating an existing pair overwrites the value, so growth is bounded by the number of distinct pairs. It is not bounded by request count.
- **Resource:** process heap.
- **Trigger:** heartbeats with novel `token` or `serviceId` values, for example ephemeral instance IDs or rotating tokens.
- **Existing cleanup and bounds:** none on the aggregate. Only the per-request body size is bounded.
- **Assumptions:**
  - The default JSON limit is about 2 MiB for actix-web 4 (`JsonConfig::default()` is used with no `.limit`). I did not check the resolved version.
  - Key churn rate and container memory limits are unknown.
  - Rehash pauses (the map is rehashed under the lock) are secondary.
- **Mechanism class:** cumulative resource retention.
- **Aging relevance:** static aging mechanism supported. All four elements are present: a repeatable trigger, persistent accumulation, no cleanup, and degradation (RSS growth, OOM, rehash and lock-hold costs) plausible over long uptime. The rate depends on workload cardinality.
- **Final severity:** medium. It is high only if key churn is high or clients are untrusted. CR rated it high and RE rated it medium. The cumulative effect is real, but the rate is workload-dependent.
- **Final confidence:** high for the mechanism, low for the actual growth rate.

### F2. Global `std::sync::Mutex` held through clone, sort and serialization
- **Merged from:** CR-2 and RE-2.
- **Static validity:** confirmed, with one qualification on serialization. The guard `db` lives until function return (L116). Cloning (L128–135) and `sort_by` (L138) are inside the critical section.
  - The `.json()` call at L140 evaluates while the guard is still alive, so serialization is also under the lock. The handler returns an `HttpResponse` with the already-serialized body.
  - `heartbeat` takes the same lock at L87. The guard is held through `HttpResponse::json` at L100–102, but that payload is tiny.
- **Performance relevance:** conditional.
- **Resource:** the global lock and actix worker threads. A blocking `lock()` stalls the worker thread and every connection on it. There is no `.await` while the guard is held, so it is not held across suspension points.
- **Trigger:** a token with many services, queried repeatedly or concurrently with heartbeats.
- **Bounds:** none beyond per-token cardinality. The critical section is O(n log n) in services per token.
- **Mechanism class:** transient or repeated overhead. Its severity grows with F1, but the lock itself does not accumulate state.
- **Aging relevance:** non-aging performance fault on its own. It becomes a conditionally plausible amplifier of F1 when per-token service sets grow over time.
- **Final severity:** medium.
- **Final confidence:** medium. Whether it matters depends on services per token, concurrency and worker count. CR's "high" confidence overstates what the code shows.

### F3. Mutex poisoning leading to permanent 500s
- **Merged from:** CR-6 and RE-3.
- **Static validity:** qualified. Both handlers return 500 on `Err` from `lock()` with no recovery (L87–94, L116–123). I see no reachable panic source inside the current critical sections.
  - `get_iso_time()` runs before the lock.
  - Allocation failure aborts rather than unwinding.
  - `sort_by` on `String` comparisons does not panic.
  - Poisoning is therefore latent and hypothetical.
- **Performance relevance:** conditional, and weak. This is an availability fault, not a performance one.
- **Mechanism class:** unsupported as a performance or aging mechanism. It has no repeatable trigger and no accumulation.
- **Aging relevance:** unsupported. It is not a supported aging mechanism.
- **Final severity:** low.
- **Final confidence:** low.

### F4. No explicit connection and timeout configuration
- **Merged from:** RE-4 only.
- **Static validity:** qualified. `HttpServer` is configured with only `bind` and the `JsonConfig` error handler (L154–175).
  - Actix defaults for client request timeout, keep-alive and connection limits apply. They are framework-dependent and I did not verify them against the resolved version.
  - The mitigation is a framework assumption, not something visible in this code.
- **Performance relevance:** conditional.
- **Resource:** sockets and connection slots.
- **Trigger:** many slow or idle clients.
- **Mechanism class:** finite-resource exhaustion, if defaults fail. This is speculative.
- **Aging relevance:** unsupported as an aging mechanism. Slow-client pressure is not a progressive accumulation in this code.
- **Final severity:** low.
- **Final confidence:** low.

### Findings not consolidated
- **CR-3 (timestamp captured before the lock):** The code is confirmed. `get_iso_time()` runs at L85 and the lock is taken at L87, so a stale overwrite is possible. It is a correctness race with no performance impact and is not an aging mechanism.
- **CR-4 (wall-clock timestamp, epoch fallback):** This is a correctness issue with no performance impact and is not an aging mechanism. L49's dead branch is also not performance-relevant.
- **CR-5 (`trim()` for validation but untrimmed storage):** Whitespace variants produce distinct keys. This adds to F1 only in proportion to client inconsistency, so it is not a standalone performance finding. It is a minor amplifier of F1.

## SELF-SOURCED — UNVALIDATED
None.

## UNSAFE BLOCKS — TRIAGE
None. No `unsafe` blocks, functions, impls, or traits exist in the target code (`/tmp/aging-review-yooh1qkk/app/src/main.rs`; `Cargo.toml` contains no source). Dependencies were not audited for unsafe as they are outside target scope.

None. No `unsafe` blocks in `main.rs`. No `mem::forget`, `Box::leak`, `tokio::spawn`, channels, `Rc<RefCell>`, or `Arc` cycles present. (`regex` and `tokio` declared in Cargo.toml but unused in code.)

---

# 2. Appendix: raw Phase-1 handoffs (unedited)

## Appendix A: code-reviewer (CR-*)

I reviewed `/tmp/aging-review-yooh1qkk/app/src/main.rs` (175 lines) and `/tmp/aging-review-yooh1qkk/app/Cargo.toml`. Both files were read in full and nothing was run.

---

### CR-1: The in-memory store only grows; nothing ever removes entries
- **File / lines:** `/tmp/aging-review-yooh1qkk/app/src/main.rs` lines 13, 97–98, 150–152
- **Fault and evidence:** The store is `type Db = Mutex<HashMap<String, HashMap<String, String>>>;` (line 13). It is created once at line 151 (`db: Mutex::new(HashMap::new())`). The heartbeat handler adds to it on every request: `db.entry(req.token.clone()).or_insert_with(HashMap::new)` (line 97) and `service_map.insert(req.serviceId.clone(), timestamp)` (line 98). No code path calls `remove`, `retain`, `clear`, expires entries or caps the size.
- **Execution path:** `POST /heartbeat` (line 169) → `heartbeat` → validation at line 79 → lock at line 87 → insert at lines 97–98.
- **Affected state:** The process-wide `AppState.db` and its nested maps. Each entry owns the cloned token and serviceId strings plus a 20-byte timestamp `String`.
- **Triggering conditions:** Any client sending heartbeats with new `token` or `serviceId` values. Normal service churn is enough, such as instances with ephemeral IDs, rotating tokens, or retired services that are never removed. Malicious input is not required.
- **Existing bounding logic:** The only check is the non-empty test at line 79. It does not limit how many entries exist or how long a key can be. Line 156 sets up `JsonConfig::default()` with only an error handler and no `.limit(...)`. So the actix-web default payload limit applies, which I believe is about 2 MiB. Each key can therefore be very large. This bounds the size of a single request, not the total stored, so it is not enough.
- **Runtime consequence:** Resident memory rises steadily with the number of distinct (token, serviceId) pairs seen since startup. Over a long uptime this can end in OOM kill or allocator pressure. HashMap rehashing also gets more expensive, and it runs while the lock is held.
- **Severity:** high. **Confidence:** high.
- **Needs runtime validation:** Real key cardinality and churn in production. The effective default JSON size limit for the resolved actix-web 4.x version.

### CR-2: One global `std::sync::Mutex` is held while sorting, cloning and serializing the whole result
- **File / lines:** `main.rs` lines 116–141 (also 87–103)
- **Fault and evidence:** In `get_services`, the guard `db` is taken at line 116 and lives until the function returns. While it is held, the code clones every entry (`sid.clone()`, `time.clone()` at lines 131–132), sorts (`response_list.sort_by(...)` at line 138) and serializes (`HttpResponse::Ok().json(response_list)` at line 140; actix serializes inside `.json()`). The guard is never dropped explicitly or scoped earlier. It is a blocking `std::sync::Mutex` (line 5), used inside an async handler on actix worker threads. The same lock covers every token, so tenants that have nothing to do with each other still wait on each other.
- **Execution path:** `POST /services` (line 170) → `get_services` → lock at line 116 → loop at lines 128–135 → sort at line 138 → serialize at line 140 → guard dropped when the function returns. Meanwhile every `POST /heartbeat` blocks at line 87.
- **Affected state:** The global `db` mutex and the actix worker threads, which are OS threads.
- **Triggering conditions:** A token with many services (cost grows as O(n log n) plus allocation and serialization), or many concurrent `/services` and `/heartbeat` requests. CR-1 makes this worse over time because n keeps growing.
- **Existing bounding logic:** None. Nothing limits critical-section length, uses a reader/writer split, or shards the lock.
- **Runtime consequence:** Tail latency rises across all endpoints and throughput falls into one-at-a-time processing. Blocked workers cannot service other connections because a std mutex blocks the whole thread, not just the task. As the store grows, this gets steadily worse.
- **Severity:** med. **Confidence:** high.
- **Needs runtime validation:** Services-per-token distribution, request concurrency, and worker count. Lock-hold time under real load.

### CR-3: The timestamp is taken before the lock, so the stored time can go backwards
- **File / lines:** `main.rs` lines 85–98
- **Fault and evidence:** `let timestamp = get_iso_time();` (line 85) runs before `data.db.lock()` (line 87). Then `service_map.insert(req.serviceId.clone(), timestamp)` (line 98) overwrites the value without checking it. Suppose request A reads time t1, request B reads t2 > t1, and B gets the lock first. B writes t2, then A writes t1 over it.
- **Execution path:** Two concurrent `/heartbeat` requests for the same (token, serviceId) on different workers.
- **Affected state:** The `lastNotification` value for that service.
- **Triggering conditions:** Concurrent heartbeats for the same service that straddle a one-second boundary (the format has second resolution, lines 59–66), plus lock contention (CR-2) that changes the order requests acquire the lock.
- **Existing bounding logic:** None. Nothing compares the new value to the old one or keeps updates monotonic.
- **Runtime consequence:** `/services` reports a `lastNotification` older than the latest heartbeat received. Liveness or uptime decisions could see a service as staler than it really is.
- **Severity:** low. **Confidence:** med.
- **Needs runtime validation:** Whether clients ever send overlapping heartbeats for the same service, and how much lock-wait jitter there is.

### CR-4: Wall-clock timestamps can go backwards, and a pre-epoch clock silently becomes 1970
- **File / lines:** `main.rs` lines 43–45 (plus 49)
- **Fault and evidence:** `SystemTime::now()` (line 43) is wall-clock time, not monotonic. `duration_since(UNIX_EPOCH).unwrap_or_default()` (line 44) turns a clock set before the epoch into `0`, which produces `"1970-01-01T00:00:00Z"` with no error. Line 49, `if z >= 0 { z } else { z - 146096 }`, tests a `u64`, so the negative-era branch can never run. That branch is harmless only because of the clamp on line 44.
- **Execution path:** Every heartbeat → `get_iso_time` (line 85).
- **Affected state:** The stored `lastNotification` values.
- **Triggering conditions:** NTP step corrections, VM or WSL clock jumps after suspend or resume, or a misconfigured host clock.
- **Existing bounding logic:** `unwrap_or_default` prevents a panic, but it hides the bad value instead of handling it.
- **Runtime consequence:** Stored times can go backwards or show 1970. Anything downstream that computes staleness from them gets wrong results.
- **Severity:** low. **Confidence:** med.
- **Needs runtime validation:** How stable the host clock is and how NTP is configured.

### CR-5: Inputs are checked with `trim()` but stored untrimmed, so one service can end up under several keys
- **File / lines:** `main.rs` lines 79, 97–98, 110, 128
- **Fault and evidence:** Validation uses `req.serviceId.trim().is_empty()` and `req.token.trim().is_empty()` (lines 79 and 110). Storage and lookup use the raw values: `req.token.clone()` and `req.serviceId.clone()` at lines 97–98, and `db.get(&req.token)` at line 128. So `"svc"`, `" svc"` and `"svc\n"` become separate keys.
- **Execution path:** `/heartbeat` insert; `/services` lookup.
- **Affected state:** Key identity in `db`.
- **Triggering conditions:** Clients that send inconsistent whitespace, for example from templated config or trailing newlines out of env files or files.
- **Existing bounding logic:** None. Nothing normalizes keys.
- **Runtime consequence:** One service shows up as several entries, or a lookup by token returns nothing. Duplicate keys also add to the growth in CR-1.
- **Severity:** low. **Confidence:** med.
- **Needs runtime validation:** Whether real clients send whitespace that varies between requests.

### CR-6: A poisoned mutex would make the service fail permanently
- **File / lines:** `main.rs` lines 87–94, 116–123
- **Fault and evidence:** If the lock is poisoned, both handlers return 500 (`Err(_) => return HttpResponse::InternalServerError()...`, lines 89–93 and 118–122). They never recover the guard (for example via `into_inner`) and never clear the poison. If any panic ever happens while the guard is held, every later request to both endpoints fails until the process restarts. The process stays up, so a liveness check based on the process existing would not notice. The code shown has little that could panic in those sections: HashMap allocation failure aborts rather than panics, and so does serde serialization of `String`s. That is why confidence is low.
- **Execution path:** Any handler that panics while holding the lock, then every later call to either handler.
- **Affected state:** The global `db` mutex and service availability.
- **Triggering conditions:** A panic inside a critical section, for example from future code changes or a hash/comparison panic in a dependency.
- **Existing bounding logic:** The error is handled gracefully per request, but there is no recovery, so the outage is permanent.
- **Runtime consequence:** A permanent partial outage (500s on every request) while the process keeps running.
- **Severity:** med. **Confidence:** low.
- **Needs runtime validation:** Whether any panic path in the critical sections can actually be reached with the resolved dependency versions.

---

### UNSAFE BLOCKS — TRIAGE
There are no `unsafe` blocks, functions, impls or traits in the target code (`/tmp/aging-review-yooh1qkk/app/src/main.rs`; `Cargo.toml` has no source). I did not audit the dependencies for unsafe code, since they are outside the target.

## Appendix B: rust-engineer (RE-*)

# Review of `/tmp/aging-review-yooh1qkk/app/src/main.rs`

I reviewed the only source file, `main.rs` (175 lines), plus `Cargo.toml`. Nothing was executed. I found three findings and one low-severity item, and no `unsafe` blocks.

## RE-1
- **File and lines:** `/tmp/aging-review-yooh1qkk/app/src/main.rs`, lines 13, 97–98, 150–152.
- **Fault:** The in-memory store has no bound and no eviction. Entries are only ever added or overwritten. Nothing removes a token, a service ID, or a stale heartbeat.
  - The store is `Mutex<HashMap<String, HashMap<String, String>>>` (line 13).
  - `db.entry(req.token.clone()).or_insert_with(HashMap::new)` and `service_map.insert(req.serviceId.clone(), timestamp)` (lines 97–98) create a new key pair for every distinct `(token, serviceId)`.
  - The only validation is `trim().is_empty()` (line 79). There is no length cap, no count cap and no TTL.
- **Execution path:** `POST /heartbeat` → `heartbeat` → lock → `entry().or_insert_with()` → `insert()`.
- **Affected state or resource:** Process heap, held by the `AppState` that lives for the whole process. The `Data` is cloned per worker but is one `Arc`.
- **Triggering conditions:** Any client sending heartbeats with many distinct tokens or service IDs. Actix's default JSON body limit is 2 MiB, which I believe applies since `JsonConfig::default()` is used. A single request can therefore carry a multi-hundred-KB key that is stored twice (cloned token plus cloned `serviceId`).
- **Existing cleanup or bounding logic:** None. The default JSON limit only caps each request, not the total.
- **Runtime consequence:** Memory grows without bound over the process lifetime, ending in OOM kill. Rehashing of the large outer and inner maps also causes latency spikes while the global lock is held.
- **Severity / confidence:** Medium / High.
- **Assumptions to validate:** Real traffic cardinality, container memory limits, and whether the default 2 MiB JSON limit is what applies.

## RE-2
- **File and lines:** `main.rs`, lines 87–98 and 116–140.
- **Fault:** All requests, across all tokens, share one global `std::sync::Mutex`. `get_services` holds the guard (`db`, line 116) through the whole response build. That covers iteration with a `clone()` of every key and value (lines 128–135) and the `sort_by` (line 138). The guard is only dropped at function exit, so the O(n log n) sort runs inside the critical section. The `heartbeat` path (lines 87–98) takes the same lock.
- **Execution path:** Actix worker threads (one per core by default) run the async handlers. There is no `.await` while the guard is held, so the lock itself is not held across a suspension point. A blocked `lock()` blocks the whole worker thread, though, and every other connection on that worker stalls with it.
- **Affected state or resource:** The global mutex, the worker threads, and tail latency for unrelated tokens.
- **Triggering conditions:** A token with a very large service set (see RE-1) queried repeatedly or concurrently with heartbeats.
- **Existing cleanup or bounding logic:** None. The critical section is short for small maps but scales linearly or worse with the number of services for the token.
- **Runtime consequence:** Head-of-line blocking. Heartbeats and queries for all tokens queue behind a large `/services` call, and workers stall with their other connections. Throughput and latency degrade, and heartbeat timestamps can lag.
- **Severity / confidence:** Medium / Medium.
- **Assumptions to validate:** Per-token service counts in production and the contention profile.

## RE-3
- **File and lines:** `main.rs`, lines 87–94 and 116–123, with the writer at lines 97–98.
- **Fault:** Mutex poisoning is handled by returning a permanent 500. If a panic ever occurs while the guard is held, every later `lock()` returns `Err`, and both handlers answer 500 forever. Nothing calls `into_inner()` on the poison error or clears it.
  - Panic sources inside the critical section are `HashMap` operations, `clone()` and `sort_by`. These can panic on allocation failure and on a panic in a comparison.
  - `get_iso_time()` is called before the lock (line 85), so it is not one of them.
- **Execution path:** Panic in a handler → worker thread catches or unwinds → the `Mutex` stays poisoned → all subsequent requests hit the `Err` branches.
- **Affected state or resource:** The global store and service availability.
- **Triggering conditions:** A panic while the lock is held. This is unlikely with the current code, since the operations are mostly infallible.
- **Existing cleanup or bounding logic:** The handlers return 500 gracefully, but there is no recovery, so the service stays down until restart.
- **Runtime consequence:** A latent total outage after a single panic, with no self-healing.
- **Severity / confidence:** Low / Low.
- **Assumptions to validate:** That a panic can actually occur inside the locked region.

## RE-4
- **File and lines:** `main.rs`, lines 154–175.
- **Fault:** There are no connection-level timeouts or limits configured on `HttpServer`. The only options set are the `bind` address and the `JsonConfig` error handler. Actix's defaults (client request timeout, keep-alive, worker count, shutdown timeout of 30 s) are accepted unchanged.
- **Execution path:** `HttpServer::new(...).bind(...)?.run().await`.
- **Affected state or resource:** Worker connection slots (default max 25k per worker) and sockets.
- **Triggering conditions:** Many slow or idle clients.
- **Existing cleanup or bounding logic:** Actix defaults provide a 5 s client request timeout and 5 s keep-alive, which largely mitigate this. Body-read behavior under the 2 MiB limit is not otherwise bounded.
- **Runtime consequence:** Mostly mitigated by defaults. There is limited residual risk of resource pressure and no application-level backpressure.
- **Severity / confidence:** Low / Low.
- **Assumptions to validate:** The Actix version's default timeouts and the deployment's connection profile.

## UNSAFE BLOCKS — TRIAGE
None. `main.rs` contains no `unsafe` blocks. There is also no `mem::forget`, no `Box::leak`, no `tokio::spawn`, no channels, no `Rc<RefCell>` and no `Arc` cycles. `regex` and `tokio` are declared in `Cargo.toml` but not used in the code.