# 1. Performance-engineer final consolidated report (verbatim)

# Adjudication report: `/tmp/aging-review-44hfrxky/app/app.py`

All conclusions below are static-analysis hypotheses and need runtime confirmation. I read only `app.py`.

## Consolidated findings

### F1: Global DB lock with per-request connect, fsync commit and close
- **Merged from:** CR-1, FA-1. CR-3 and FA-3 contribute lock-hold time, and CR-5 and FA-2 contribute the multi-process variant.
- **Static validity:** Confirmed.
  - `_db_lock` is a plain `threading.Lock` (line 17).
  - It is held across connect, execute, commit and close in `upsert_heartbeat` (lines 47-61).
  - It is held across connect, execute, `fetchall` and close in `list_services_for_token` (lines 65-79).
  - Both handlers are sync `def`, so they run in the framework threadpool.
- **Performance relevance:** Direct.
- **Mechanism class:** Transient or repeated overhead. Under load it can become finite-resource exhaustion, because blocked threads hold threadpool tokens.
- **Resource and trigger:** The resources are the threadpool and request latency. The trigger is concurrent requests. A single process is bounded by the lock, with no timeout.
- **Cleanup and bounds:**
  - The `with` block always releases the lock.
  - Nothing bounds the queue depth.
  - Nothing is retained after a request completes, so load disappearing clears the state.
- **Direct evidence:** The lock scope and the per-call connect and commit.
- **Assumptions:** The threadpool size (about 40 is a framework default, not in this code), fsync cost, and request rate.
- **Aging relevance:** Non-aging performance fault. The trigger is repeatable, but nothing accumulates across time and the state recovers once load drops. Aging would only follow if the load itself grew over time, for example through F2.
- **Final severity and confidence:** Medium severity, high confidence.

### F2: Unbounded table growth feeding unbounded `/services` reads
- **Merged from:** CR-4, CR-3, FA-3.
- **Static validity:** Confirmed for the growth and the unbounded read. The reviewers' growth premise holds: rows are only inserted or updated (lines 50-58), and no code deletes them. Qualified for impact, because it depends on the number of distinct (serviceId, token) pairs that actually appear.
  - `fetchall()` at line 77 loads every matching row while the lock is held.
  - Lines 159-168 build a second list and Pydantic validates it again.
  - The `ORDER BY` needs a sort step because the index is on `token` only. That detail is an assumption about the SQLite query planner, not something visible in the code.
- **Performance relevance:** Conditional.
- **Mechanism class:**
  - The table and its indexes grow without bound, which is cumulative resource retention (persistent disk and index state).
  - Per-request memory and lock-hold time are transient overhead that scales with the table.
- **Resource and trigger:**
  - The resources are the SQLite file, the indexes, and per-request memory and lock time.
  - The trigger is a continuing stream of new `serviceId` or token values (client-chosen, with no length limit). Service churn or token rotation would produce this.
- **Cleanup and bounds:**
  - There is no TTL, deletion or LIMIT.
  - The upsert bounds rows per pair only.
  - The growth rate is entirely workload-dependent. A stable fleet of services would produce no growth.
- **Direct evidence:** The absence of any DELETE, the insert path, and the `fetchall` with no LIMIT.
- **Assumptions:** A churning or high-cardinality workload, and how often `/services` is polled.
- **Aging relevance:** Conditionally plausible aging mechanism. All four elements are present only if the workload keeps creating new pairs: the trigger repeats, rows accumulate, nothing cleans them up, and `/services` latency and F1 lock contention degrade over a long run. With a static set of services, this is not aging.
- **Final severity and confidence:** Low-to-medium severity, medium confidence.

### F3: Multi-process or working-directory variant of the SQLite setup
- **Merged from:** CR-5, FA-2.
- **Static validity:** Qualified.
  - The relative `DB_PATH` (line 12) and a lock that is process-local are confirmed.
  - The code runs `uvicorn.run(app)` single-process (line 177), and nothing calls chdir.
  - Multi-worker contention therefore needs a deployment that differs from the code's own entry point.
- **Performance relevance:** Conditional.
- **Mechanism class:** Transient or repeated overhead. Stalls are bounded by SQLite's default timeout (about 5 s, a library default), followed by errors that become HTTP 500.
- **Resource and trigger:** The resource is the SQLite file lock, with each stalled request holding a thread and the in-process lock. The trigger is several worker processes.
- **Cleanup and bounds:** The default busy timeout bounds each stall. Connections are always closed in `finally`.
- **Direct evidence:** The path and lock definitions.
- **Assumption:** The deployment topology. The wrong-database-file concern is a correctness issue, not a performance one.
- **Aging relevance:** Non-aging performance fault. There is no accumulation.
- **Final severity and confidence:** Low severity, low confidence.

### F4: Fallback timestamp parse can fail and turn into a 500
- **Merged from:** CR-2, FA-4.
- **Static validity:** Confirmed that the second `fromisoformat` (line 166) is unguarded. The outer handler (line 171) converts the failure into a 500, and the comment on line 165 does not match the code. The failure can only be triggered by a malformed stored value, and the app's own writer always emits ISO text (line 21), so this needs outside modification of the data.
- **Performance relevance:** None. This is a correctness and availability issue.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity and confidence:** Low severity, medium confidence (the code defect is certain, the trigger is not).

### F5: Whitespace asymmetry between `serviceId` and `token`
- **Merged from:** CR-6, FA-6 (partly).
- **Static validity:** Confirmed that `serviceId` is stripped (line 125) and the token is not. Whitespace-variant tokens create separate rows.
- **Performance relevance:** None to marginal. Any extra rows are a minor contribution to F2 only if clients actually send inconsistent tokens. Neither reviewer's evidence shows that happening.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity and confidence:** Low severity, low confidence.

### F6: Unbounded input sizes, redundant checks and the blocking startup init
- **Merged from:** FA-6 (second half), FA-5.
- **Static validity:**
  - Qualified. Neither `serviceId` nor `token` has a length limit in the model definitions (lines 83-95). A very large string would be written under the lock.
  - Any body size cap would come from the server or proxy, and this is not visible in the code.
  - Confirmed that the `isinstance` checks are redundant, but their cost is negligible.
  - The startup `init_db` (line 115) is a one-time cost.
- **Performance relevance:** Conditional, for the large-payload case only. The redundant checks and startup cost are none.
- **Mechanism class:** Transient or repeated overhead.
- **Aging relevance:** Non-aging performance fault. A large-payload row would feed F2's storage growth, but that depends on a client sending such data.
- **Final severity and confidence:** Low severity, low confidence.

## SELF-SOURCED — UNVALIDATED

None. I found no additional aging-relevant issue in the cited code paths.

---

# 2. Appendix: raw Phase-1 handoffs (unedited)

## Handoff A: code-reviewer (CR-*)

**CR-1**
- **File / lines:** /tmp/aging-review-44hfrxky/app/app.py, lines 17, 46-61, 64-79 (call sites at 134 and 158)
- **Fault and evidence:** All database work is serialized behind one process-wide lock, `_db_lock = threading.Lock()` (line 17). Both `upsert_heartbeat` (line 47) and `list_services_for_token` (line 65) hold `with _db_lock:` for the whole operation. That covers `sqlite3.connect(DB_PATH)` (lines 48 and 66), the statement, `conn.commit()` (line 59, a durable commit that does fsync with the default journal mode), `cur.fetchall()` (line 77) and `conn.close()`. Reads and writes cannot overlap at all, even though SQLite allows concurrent readers.
- **Execution path:** `POST /heartbeat` → `heartbeat()` (sync `def`, so it runs in Starlette's anyio threadpool) → `upsert_heartbeat` → lock → connect → INSERT/UPSERT → commit/fsync → close → release. `POST /services` → `services()` → `list_services_for_token` → the same lock.
- **Affected state/resource:** `_db_lock`, the anyio worker threadpool (default limit about 40 tokens), and request latency.
- **Triggering conditions:** Many monitored services sending heartbeats at the same time, `/services` reads during write bursts, or slow storage that makes the per-request fsync and connection setup expensive.
- **Existing bounding logic:** None. `Lock.acquire` has no timeout. Waiting threads keep their threadpool tokens while blocked, so once the pool is full, new requests queue without limit in the event loop. The lock also only coordinates threads in one process. It does not bound or coordinate anything else.
- **Plausible runtime consequence:** Throughput is capped at roughly 1 / (connect + statement + fsync + close) across all endpoints. Under load, latency rises linearly with queue depth, the threadpool saturates, and clients time out. A heartbeat client that times out may count a healthy service as down.
- **Severity / confidence:** med / high
- **Assumptions to validate:** The actual heartbeat request rate, fsync latency on the deployment volume, and the effective anyio threadpool size.

**CR-2**
- **File / lines:** /tmp/aging-review-44hfrxky/app/app.py, lines 162-168 (with 171-172)
- **Fault and evidence:** The fallback parse on line 166, `datetime.fromisoformat(last_notification.replace("Z", "+00:00"))`, is not protected. If the first `fromisoformat` (line 163) raises `ValueError` and the second one also fails (the row is not just a "Z"-suffix variant), the exception escapes the loop. It is caught by the outer `except Exception` (line 171) and turned into a 500 (line 172). The comment on line 165, "treat as UTC naive and attach UTC", does not match the code: no timezone is ever attached. A naive stored value is returned as naive. A NULL value cannot occur because of `NOT NULL`, but any other malformed text can.
- **Execution path:** `POST /services` → `list_services_for_token` → for each row, line 163 fails → line 166 fails → line 171 → HTTP 500.
- **Affected state/resource:** The whole `/services` response for that token.
- **Triggering conditions:** Any row with a `last_notification` value that is not ISO-parseable, for example from manual DB edits, older schema data, or another writer. Also, on Python versions before 3.11, `fromisoformat` is stricter about formats.
- **Existing bounding logic:** There is a per-row try/except, but it only covers the first parse. The fallback has no handling, so one bad row is not isolated.
- **Plausible runtime consequence:** A single corrupted row stops every service for that token from being listed, with a persistent 500 until the row is overwritten by a new heartbeat. If naive datetimes are returned, they are serialized without an offset, which makes the timestamp ambiguous to clients.
- **Severity / confidence:** low / med
- **Assumptions to validate:** Whether any writer other than `upsert_heartbeat` (which always writes `utc_now_iso()`, offset-aware) can put data into the table, and which Python version is used.

**CR-3**
- **File / lines:** /tmp/aging-review-44hfrxky/app/app.py, lines 64-79 and 157-168
- **Fault and evidence:** `/services` returns an unbounded result set. `cur.fetchall()` (line 77) loads every row for a token into memory while `_db_lock` is held. Lines 159-167 then build a second full list of `ServiceOut` Pydantic objects, which is validated again through `response_model=List[ServiceOut]` (line 149) and serialized in one go. There is no LIMIT, no pagination, and no streaming.
- **Execution path:** `POST /services` → `list_services_for_token` (lock held during the full fetch) → materialized Python list → Pydantic model list → response validation → JSON.
- **Affected state/resource:** Process memory, the time `_db_lock` is held (which blocks all heartbeat writes, see CR-1), and CPU.
- **Triggering conditions:** A token shared by a large number of services (`serviceId` values are client-chosen and unbounded), or repeated `/services` polling by dashboards.
- **Existing bounding logic:** None. The `idx_heartbeats_token` index (line 39) makes the lookup fast, but it does not limit result size. `ORDER BY service_id` also needs a sort, because the index is on `token` only.
- **Plausible runtime consequence:** Memory spikes and long lock hold times per request. Heartbeat writes stall while large reads run.
- **Severity / confidence:** low / med
- **Assumptions to validate:** The real number of services per token and how often `/services` is polled.

**CR-4**
- **File / lines:** /tmp/aging-review-44hfrxky/app/app.py, lines 30-35 and 50-57
- **Fault and evidence:** The `heartbeats` table only grows. The upsert inserts a new row for every new `(service_id, token)` pair (lines 52-55), and nothing in the module ever deletes or expires rows. Because `serviceId` and `token` are arbitrary client strings (lines 83-87), each distinct pair adds a permanent row plus entries in both the primary-key index and `idx_heartbeats_token`.
- **Execution path:** `POST /heartbeat` with a previously unseen `serviceId`/`token` pair → INSERT path.
- **Affected state/resource:** The size of the `db.sqlite3` file, index size, and page cache.
- **Triggering conditions:** Long uptime, service churn (renamed or decommissioned services, rotated tokens), or clients that send unique IDs.
- **Existing bounding logic:** None. The UPSERT bounds rows per pair, but not the number of pairs.
- **Plausible runtime consequence:** Disk use grows steadily over time. Index and cache pressure increase, and per-token result sets grow (which makes CR-3 worse).
- **Severity / confidence:** low / med
- **Assumptions to validate:** The rate at which new service/token pairs appear in the deployment.

**CR-5**
- **File / lines:** /tmp/aging-review-44hfrxky/app/app.py, lines 12, 17, 26, 48, 66
- **Fault and evidence:** `DB_PATH = "db.sqlite3"` (line 12) is a relative path, resolved against the process working directory at every `sqlite3.connect` call. Concurrency control is a `threading.Lock` (line 17), which only works inside a single process. `sqlite3.connect` uses the default 5 s busy timeout, and there is no WAL setting.
- **Execution path:** Startup `init_db` (line 115) and every request-level connect (lines 48 and 66).
- **Affected state/resource:** The identity of the database file, and the SQLite file lock when more than one process is running.
- **Triggering conditions:** Starting the app from a different working directory, which silently creates and uses a new empty DB. Running with several uvicorn/gunicorn workers, where each process has its own `_db_lock` and writers compete for the SQLite file lock.
- **Existing bounding logic:** The in-process lock prevents contention inside one process but has no effect across processes. The default busy timeout only delays the failure.
- **Plausible runtime consequence:** Data appears to be "lost", because a different file is in use. With multiple workers, `sqlite3.OperationalError: database is locked` occurs under write contention and is turned into a 500 at line 139 or 172, so heartbeats are dropped.
- **Severity / confidence:** low / med
- **Assumptions to validate:** The deployment uses multiple worker processes or a working directory that is not fixed. The `__main__` entry point (line 177) runs a single process.

**CR-6**
- **File / lines:** /tmp/aging-review-44hfrxky/app/app.py, lines 125-131 and 153-155
- **Fault and evidence:** Keys are normalized inconsistently. `serviceId` is stripped (`payload.serviceId.strip()`, line 125), but `token` is stored exactly as received (line 126) and matched exactly at line 72 (`WHERE token = ?`). Both endpoints only reject `token == ""` (lines 130 and 154), so a whitespace-only token is accepted. Different whitespace variants of the same `serviceId` collapse into one row, while different whitespace variants of a token create separate rows that never match each other.
- **Execution path:** `POST /heartbeat` with a token that has a trailing newline or space (common with env-file or shell-sourced tokens) → row stored under that token → `POST /services` with the trimmed token → the service is missing from the list.
- **Affected state/resource:** The heartbeat rows' `(service_id, token)` keys.
- **Triggering conditions:** Clients that are inconsistent about whitespace around tokens.
- **Existing bounding logic:** Only the empty-string check, which does not cover whitespace.
- **Plausible runtime consequence:** Services look unregistered or stale to readers even though heartbeats are succeeding (HTTP 200), which leads to false "service down" conclusions. Orphaned rows are also created, adding to CR-4.
- **Severity / confidence:** low / low
- **Assumptions to validate:** Whether real clients send tokens with surrounding whitespace.

## Handoff B: fastapi-developer (FA-*)

FA-1
- File: /tmp/aging-review-44hfrxky/app/app.py, lines 46-61, 64-79, 17, 124, 152
- Fault: Every call opens a new `sqlite3.connect(DB_PATH)` (lines 48, 66) and closes it. A single global `threading.Lock` (`_db_lock`, line 17) serializes all reads and writes, including connection setup, execution, commit (fsync) and close. The sync `def` handlers run in AnyIO's threadpool, which has a default of 40 threads.
- Execution path: The `/heartbeat` and `/services` requests run `heartbeat` or `services` in a worker thread. Each calls `upsert_heartbeat` or `list_services_for_token` and blocks on `with _db_lock`.
- Affected resource: The threadpool tokens (about 40), `_db_lock`, and the SQLite file and its journal.
- Triggering conditions: Concurrent requests. Each upsert commit does a journal fsync (default rollback-journal mode) while holding the lock.
- Existing bounding logic: The lock prevents SQLite "database is locked" errors between threads. It does not bound throughput or queueing. It makes the DB path fully serial, so the threadpool adds no parallelism. There is no pooling, WAL mode, or timeout.
- Runtime consequence: Throughput is capped at about 1 / (connect + fsync + close) per second. Under load, the 40 threads all sit blocked on the lock. Latency grows and the pool saturates. Anything else using the threadpool (the framework's sync work) is starved, and request queueing is unbounded.
- Severity: med. Confidence: high.
- Assumptions to validate: fsync cost on the deployed disk, the actual request concurrency, and the AnyIO limiter default.

FA-2
- File: /tmp/aging-review-44hfrxky/app/app.py, lines 12, 26, 48, 66
- Fault: `DB_PATH = "db.sqlite3"` is a relative path, resolved against the process cwd on every `sqlite3.connect`. `init_db` runs once at startup (line 115). No `timeout` is passed, so the default is 5 s. Connections are not shared across processes, and the in-process lock does not cover multiple workers.
- Execution path: Startup creates the table. If the process runs with multiple uvicorn or gunicorn workers, or any other process touches the file, writers contend at the file level. `_db_lock` protects only within one process.
- Affected resource: The SQLite file lock, and the correctness of the heartbeat write.
- Triggering conditions: More than one worker process, or a cwd change between startup and requests. The cwd change is unlikely, since nothing calls chdir.
- Existing bounding logic: The 5 s default busy timeout is the only bound. It is insufficient under sustained multi-process write load.
- Runtime consequence: `sqlite3.OperationalError: database is locked` is caught by the broad `except Exception` (lines 138, 171) and returned as HTTP 500. Heartbeats are lost, so services appear dead. Requests also stall for up to 5 s while holding a threadpool thread and `_db_lock`.
- Severity: low-med. Confidence: low-med.
- Assumptions to validate: The deployment worker count. `uvicorn.run(app)` at line 177 runs a single process, so this only applies if the deployment differs.

FA-3
- File: /tmp/aging-review-44hfrxky/app/app.py, lines 64-79 and 158-168
- Fault: `list_services_for_token` uses `fetchall()` with no LIMIT and holds `_db_lock` for the whole query. The handler then builds a `ServiceOut` per row and validates the response again through `response_model=List[ServiceOut]`. Rows are unbounded per token. The index `idx_heartbeats_token` (line 39) exists, but `ORDER BY service_id` is not served by it alone, so the query needs a sort or a temp b-tree.
- Execution path: A `POST /services` call with a token that has many registered `serviceId` values.
- Affected resource: Memory, CPU, and `_db_lock` hold time. Heartbeat writes are blocked behind the read.
- Triggering conditions: A token (or many tokens) with a large number of services. Heartbeat rows are never deleted, so the table grows monotonically.
- Existing bounding logic: None. There is no row cap, pagination, or TTL or cleanup of stale rows.
- Runtime consequence: Latency for `/services` grows linearly with rows. Because the lock is global, heartbeats stall during large reads. Memory and serialization cost grow, and the table and file grow without bound.
- Severity: med. Confidence: med.
- Assumptions to validate: Cardinality of services per token, and whether the (token, service_id) index ordering is used. The primary key is (service_id, token), which is the wrong order for a token filter, hence the secondary index.

FA-4
- File: /tmp/aging-review-44hfrxky/app/app.py, lines 160-167
- Fault: The "defensive" fallback only re-parses with `replace("Z", "+00:00")`. It does not do what its comment says ("treat as UTC naive and attach UTC"). If the first `fromisoformat` fails, the second can raise `ValueError` again, and that is not caught inside the inner try. The outer `except Exception` (line 171) converts it to a 500 for the whole request.
- Execution path: `POST /services` hits a corrupted row, and one bad row fails the entire list.
- Affected resource: Response correctness for that token.
- Triggering conditions: Only a malformed `last_notification` value. Normal writes always use `utc_now_iso`, so this needs outside tampering or corruption.
- Existing bounding logic: The broad exception handler returns 500, so there is no crash, but the token's whole listing becomes unavailable.
- Runtime consequence: Persistent 500s for the affected token.
- Severity: low. Confidence: low.
- Assumptions to validate: That a bad row can actually get into the table. The app's own writer cannot produce one.

FA-5
- File: /tmp/aging-review-44hfrxky/app/app.py, lines 113-115
- Fault: Startup uses the deprecated `@app.on_event("startup")`, and there is no shutdown or lifespan handling. `init_db` is synchronous and blocking and runs on the event loop at startup. It takes `_db_lock` and does disk I/O.
- Execution path: App startup, once.
- Affected resource: The event loop at startup, and the readiness of the app.
- Triggering conditions: A slow or locked database file at startup.
- Existing bounding logic: None. The process fails on error, which at least prevents serving without a table.
- Runtime consequence: Startup blocks for as long as the DB takes. This is a one-time cost and is minor. Failure to create the table raises at startup and the app does not come up.
- Severity: low. Confidence: med.
- Assumptions to validate: Whether the filesystem is slow or the file is locked at launch.

FA-6
- File: /tmp/aging-review-44hfrxky/app/app.py, lines 130, 154
- Fault: The `isinstance(token, str)` checks are redundant, because Pydantic already guarantees a `str`. Whitespace-only tokens pass, and `service_id` is stripped while `token` is not. A heartbeat with `serviceId=" a "` is stored as `"a"`. The `HTTPException` paths and the `except HTTPException: raise` clauses (lines 136, 169) are unreachable from inside the `try`, since nothing in it raises `HTTPException`.
- Execution path: Every request.
- Affected resource: None significant. This is only per-request CPU overhead.
- Triggering conditions: Every request.
- Existing bounding logic: Not applicable.
- Runtime consequence: Negligible. I list it as a minor correctness and perf note only: there is no length limit on `serviceId` or `token`, so very large strings are written to SQLite and held under the global lock.
- Severity: low. Confidence: low.
- Assumptions to validate: Request body size limits at the server or proxy.