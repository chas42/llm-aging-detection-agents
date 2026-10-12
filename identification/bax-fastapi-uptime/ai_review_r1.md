# 1. Performance-engineer final consolidated report (verbatim)

**Performance and aging adjudication: `/tmp/aging-review-5mpim4s1/app/app.py`**

All of this is a static-analysis hypothesis. None of it is empirical confirmation of software aging, and runtime measurement is needed to confirm any of it. I read the whole file, which is a single module of 177 lines. I did not run anything.

---

### F1. Unbounded row growth in `heartbeats`
- **Merged from:** CR-3 and FA-5. FA-2 contributes only its "table only grows" statement.
- **Static validity:** Confirmed.
- **Performance relevance:** Conditional.
- **Evidence:**
  - Lines 30–35 and 52–55: the key is `(service_id, token)`, and the upsert deduplicates only identical pairs.
  - No `DELETE`, expiry or retention logic exists anywhere in the file.
  - Lines 83–87: `serviceId` and `token` have no length or count limits.
  - Lines 125–126: `serviceId` is stripped, which does not bound growth.
  - Each insert also updates `idx_heartbeats_token`.
- **Affected resource:**
  - Disk space, the db file, the table B-tree and the token index.
  - Page cache and fsync cost per commit, which grow indirectly.
- **Triggering conditions:** Requests with new `(serviceId, token)` pairs arrive repeatedly. Examples are per-pod or per-run IDs, or arbitrary callers.
- **Existing cleanup and bounds:** None. The only ceiling is disk capacity.
- **Direct code evidence:** The absence of deletion, the absence of bounds and the key design.
- **Assumption:** Real traffic keeps producing new distinct IDs. If the ID space is fixed and small, the table plateaus and nothing ages.
- **Mechanism class:** Cumulative resource retention.
- **Aging relevance:** Static aging mechanism supported. All four elements are present:
  - The trigger is repeatable.
  - Rows accumulate persistently.
  - Cleanup is absent.
  - Degradation over a long run is plausible: slower inserts, larger `/services` results, and write failures at disk exhaustion.
  - The strength of this finding depends on the workload assumption above.
- **Severity / confidence:** Medium / high for the code fact. Confidence is medium that it matters in practice, because it depends on the ID cardinality of the real workload.

### F2. Global lock held across the full connect, execute, commit and close cycle
- **Merged from:** CR-1 and FA-1.
- **Static validity:** Confirmed.
- **Performance relevance:** Direct.
- **Evidence:**
  - Lines 47–61 and 65–79: `_db_lock` is held through `sqlite3.connect`, the statement, `commit()` (a journal fsync) and `close()`.
  - Reads and writes serialize behind it.
  - There is no timeout or backpressure.
  - Handlers at lines 124 and 152 are sync `def`, so they run on the anyio threadpool.
- **Affected resource:** `_db_lock`, threadpool threads and request latency.
- **Triggering conditions:** Concurrent requests, with severity scaling with the heartbeat rate and fsync latency.
- **Existing cleanup and bounds:**
  - The `finally` blocks always close the connection and release the lock, so there is no leak.
  - Only the threadpool size bounds how many threads can wait, which is 40 by default (an environment assumption).
  - Queue wait time is not bounded.
- **Direct code evidence:** The lock scope and the sync handlers.
- **Assumptions:** The default threadpool size, the storage latency (WSL2 is the environment, but this is not shown in the code) and the load level.
- **Mechanism class:** Finite-resource exhaustion (threadpool tokens) and repeated overhead (a connect and fsync per request). It is load-dependent contention and does not accumulate.
- **Aging relevance:** Non-aging performance fault. There is no persistent accumulation. Queue buildup under overload clears when load drops.
- **Correction to FA-1:** The claim that non-DB requests also queue is only partly right. Only handlers that run in the threadpool are affected, and with 40 tokens saturated, validation of request bodies and docs are not necessarily blocked. Pydantic body parsing happens before the sync handler is dispatched and does not need a threadpool token. I treat this part as unsupported.
- **Severity / confidence:** Medium / high.

### F3. `/services` is unbounded in rows, memory and lock hold time
- **Merged from:** CR-4 and FA-2.
- **Static validity:** Confirmed. The sort-cost claim is qualified.
- **Performance relevance:** Conditional.
- **Evidence:**
  - Lines 68–77: no `LIMIT`, and `fetchall()` runs inside the lock.
  - Lines 159–167: a `ServiceOut` is built per row, then the list goes through `response_model` validation and serialization.
  - Per-request time and memory grow linearly with the row count.
  - `fetchall` and the sort run under `_db_lock`, so heartbeat writes stall behind large reads.
- **Qualification on the sort:**
  - The index `idx_heartbeats_token` is on `token` alone, but the primary key autoindex is `(service_id, token)`.
  - SQLite will likely use the token index plus a temp B-tree sort.
  - The planner's choice cannot be confirmed statically.
  - The "double validation" claim is framework-behavior dependent.
- **Existing bounds:** None on row count or response size. The memory is released after each request.
- **Mechanism class:** Transient or repeated overhead. Per-request cost scales with the F1 accumulation.
- **Aging relevance:** Conditionally plausible. It is a downstream amplifier of F1 and not an independent accumulation. Per-request cost grows over time only if one token accumulates many rows.
- **Severity / confidence:** Medium-low / medium.

### F4. Multi-process use of one SQLite file, with `_db_lock` held during busy waits
- **Merged from:** CR-2 and FA-3.
- **Static validity:** Qualified.
- **Performance relevance:** Conditional.
- **Evidence:**
  - `threading.Lock` is per-process.
  - The default `connect` timeout is 5 s.
  - There is no WAL pragma.
  - Lines 138 and 171 convert errors to HTTP 500.
  - The `__main__` block (line 177) runs a single process, in which case cross-process contention does not occur.
- **Triggering conditions:** An external multi-worker launch such as `--workers`, gunicorn or a second instance. Nothing in the code shows this.
- **Existing bounds:** The 5 s busy timeout per wait. A waiting thread holds `_db_lock`, so a stall spreads to all DB requests in that process.
- **Rejected sub-claims:**
  - FA-3's startup, lifecycle and `on_event` points have no runtime performance consequence. The deprecated hook is a style issue.
  - FA-1's statement that the busy timeout "is never reached" is right only for a single process.
- **Mechanism class:** Finite-resource exhaustion (file lock and threads), and transient.
- **Aging relevance:** Unrelated to aging. There is no accumulation. This is an error and latency fault under a specific deployment.
- **Severity / confidence:** Low-medium / low-medium.

### F5. Timestamp captured before the lock wait can be written out of order
- **Source:** FA-6, standalone.
- **Static validity:** Confirmed as a mechanism, and qualified on magnitude.
- **Performance relevance:** None. This is a data-correctness issue that is small and bounded by queue delay. It is listed because it was raised.
- **Evidence:**
  - Line 134 computes the timestamp before the lock is taken.
  - The upsert at line 55 overwrites unconditionally.
  - `threading.Lock` is not FIFO.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Severity / confidence:** Low / medium.

### F6. Malformed `last_notification` makes `/services` fail with 500 for that token
- **Merged from:** CR-6 and FA-4.
- **Static validity:** Qualified.
- **Evidence:**
  - Line 166 is unguarded, and its `ValueError` is caught by the outer `except Exception`, giving HTTP 500.
  - This path needs a value that this code does not write, because `utc_now_iso()` always writes a parseable value.
  - The claim that `Z` handling is dead code on Python 3.11+ depends on the runtime version.
  - CR-6's claim that no tzinfo is attached is accurate but irrelevant to performance.
  - The CR-6 and FA-4 claim of a permanent failure holds only under external-writer conditions.
- **Performance relevance:** None. This is an availability and correctness issue.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Severity / confidence:** Low / low.

### Rejected finding
- **CR-5 (relative `DB_PATH`):**
  - The behavior is real, but it is a deployment and persistence concern with no runtime resource consequence.
  - Per-request `connect` cost is the same regardless of path.
  - Static validity: confirmed as a fact. Performance relevance: none. It is unrelated to performance.
  - Severity: low. Confidence: medium.

---

### SELF-SOURCED — UNVALIDATED
None. I did not search for additional issues, and nothing aging-relevant surfaced while verifying the cited code beyond what is already covered.

---

# 2. Appendix: raw Phase-1 handoffs (unedited)

## Handoff A: code-reviewer (CR-*)

**CR-1**
- **File / lines:** `/tmp/aging-review-5mpim4s1/app/app.py` lines 17, 47–61, 65–79, 124, 152
- **Fault and evidence:** All database work goes through one process-wide `threading.Lock` (`_db_lock = threading.Lock()`, line 17). The lock is held for the whole connection lifetime: `sqlite3.connect(DB_PATH)` (lines 48, 66), `execute`, `conn.commit()` (line 59, which is a synchronous disk sync) and `conn.close()`. Both handlers are plain `def` (lines 124, 152), so Starlette runs them in the anyio worker threadpool, which defaults to 40 tokens. Every read and write is therefore serialized, including reads that SQLite could run in parallel.
- **Execution path:** POST /heartbeat → threadpool → `heartbeat` → `upsert_heartbeat` → `with _db_lock` → connect/execute/commit/close. POST /services → threadpool → `list_services_for_token` → the same lock.
- **Affected state/resource:** `_db_lock`, threadpool worker threads, request latency.
- **Triggering conditions:** Heartbeats arriving concurrently, which is normal when many services report on short intervals, or slow disk sync during commit.
- **Existing bounding logic:** None. There is no lock timeout and no backpressure. The threadpool limit only caps how many threads can be blocked. It does not limit wait time. Waiting threads hold threadpool tokens, so other sync work queues behind them too.
- **Plausible runtime consequence:** Throughput is capped at one DB operation at a time, bounded by commit and disk sync latency. Under bursts, the threadpool fills with threads blocked on the lock, latency grows, and clients that retry on timeout add more load.
- **Severity / confidence:** med / high
- **Validation assumptions:** Heartbeat rate is high enough for commit latency to dominate. Uses the default anyio threadpool size. Disk sync latency depends on the storage, for example WSL2 or network filesystems.

**CR-2**
- **File / lines:** `/tmp/aging-review-5mpim4s1/app/app.py` lines 17, 26, 48, 66, 133–139, 157–172
- **Fault and evidence:** Locking is in-process only (`threading.Lock`, line 17). Nothing coordinates multiple processes, such as several uvicorn/gunicorn workers or a second instance sharing `db.sqlite3`. `sqlite3.connect(DB_PATH)` uses the default `timeout=5.0` and the default rollback-journal mode, because no `PRAGMA journal_mode=WAL` is set. A writer in one process therefore blocks readers and writers in other processes. When the 5 s busy timeout expires, `sqlite3.OperationalError: database is locked` is raised. The broad `except Exception` handlers at lines 138–139 and 171–172 turn it into HTTP 500. While a thread waits up to 5 s on the file lock, it also holds `_db_lock`, which stalls every other DB request in that process.
- **Execution path:** Multiple worker processes → `upsert_heartbeat` / `list_services_for_token` → `conn.execute` waits on the SQLite file lock while holding `_db_lock`.
- **Affected state/resource:** SQLite file locks, `_db_lock`, request success rate.
- **Triggering conditions:** More than one process using the same DB file, combined with concurrent writes or a long read.
- **Existing bounding logic:** The default 5 s busy timeout bounds each wait. However, the wait is multiplied across all queued requests because `_db_lock` is held during it. There is no retry, and WAL mode is not enabled.
- **Plausible runtime consequence:** Intermittent 500s, heartbeats that are silently dropped (the client gets a 500, but monitoring data goes stale and services can look down when they are not), and multi-second latency spikes.
- **Severity / confidence:** med / med
- **Validation assumptions:** The deployment uses more than one process or instance against the same file. The `__main__` entry point runs a single process, so this applies only to other deployment modes.

**CR-3**
- **File / lines:** `/tmp/aging-review-5mpim4s1/app/app.py` lines 30–35, 46–61
- **Fault and evidence:** Every new `(service_id, token)` pair inserts a permanent row (`INSERT ... ON CONFLICT(service_id, token) DO UPDATE`, lines 52–55). No code ever deletes or expires rows, and there is no limit on the number of distinct serviceIds per token or the number of tokens. `serviceId` and `token` are unbounded `str` fields (lines 83–87) with no length limit.
- **Execution path:** POST /heartbeat with a new serviceId or token → a new row in `heartbeats` plus an entry in the PK index and `idx_heartbeats_token`.
- **Affected state/resource:** Size of the `db.sqlite3` file, both B-tree indexes, disk space.
- **Triggering conditions:** Clients that generate serviceIds (per pod, hostname or run ID), any caller sending arbitrary IDs or tokens, or very large string values.
- **Existing bounding logic:** None. The upsert only deduplicates exact pairs that repeat.
- **Plausible runtime consequence:** The DB and its indexes grow without limit over the life of the process. Inserts and commits slowly get more expensive, the disk can fill, and once it does every write fails with 500.
- **Severity / confidence:** med / high
- **Validation assumptions:** The number of distinct IDs grows over time in the real workload.

**CR-4**
- **File / lines:** `/tmp/aging-review-5mpim4s1/app/app.py` lines 68–77, 158–168
- **Fault and evidence:** `/services` returns every row for a token with no limit or paging (`SELECT ... WHERE token = ? ORDER BY service_id ASC`, lines 70–73). It loads them all at once with `cur.fetchall()` (line 77) while holding `_db_lock`. It then builds a full `list[ServiceOut]` (lines 159–167), parsing a datetime for each row, and FastAPI re-validates and serializes that list through `response_model=List[ServiceOut]` (line 149). The `ORDER BY service_id` cannot use `idx_heartbeats_token` (on `token` only, line 39) for ordering, so SQLite performs a temporary sort.
- **Execution path:** POST /services → `list_services_for_token` (under the lock) → fetchall → loop of `ServiceOut` construction → response-model validation → JSON encoding.
- **Affected state/resource:** Per-request memory, CPU, how long `_db_lock` is held, response size.
- **Triggering conditions:** A token with many rows, which CR-3 makes possible, or frequent polling of `/services`.
- **Existing bounding logic:** None. There is no LIMIT, no pagination and no cap on response size.
- **Plausible runtime consequence:** Memory and CPU per request grow linearly with the number of rows. Large reads keep `_db_lock` held for longer, which stalls heartbeat writes from every client (see CR-1). Concurrent large polls can cause memory spikes.
- **Severity / confidence:** med / med
- **Validation assumptions:** A single token has a large row count in practice. Serialization cost depends on the Pydantic/FastAPI versions.

**CR-5**
- **File / lines:** `/tmp/aging-review-5mpim4s1/app/app.py` lines 12, 26, 48, 66
- **Fault and evidence:** `DB_PATH = "db.sqlite3"` (line 12) is a relative path, so every `sqlite3.connect(DB_PATH)` resolves it against the process's current working directory when the call runs. `sqlite3.connect` silently creates a new empty file if none exists.
- **Execution path:** Startup `init_db` → connect, then each request's connect.
- **Affected state/resource:** Which database file is used, so data persistence.
- **Triggering conditions:** Launching the app from a different working directory (a service manager, container WORKDIR or a different launcher), or any code or library calling `os.chdir` at runtime.
- **Existing bounding logic:** None. `init_db` creates the table in whichever file the path resolves to, so the error is hidden.
- **Plausible runtime consequence:** Heartbeat history seems to disappear after a restart or redeploy. `/services` returns an empty list, so services look as if they never reported. Several instances may also write to different files without anyone noticing.
- **Severity / confidence:** low / med
- **Validation assumptions:** The launch directory differs between runs. This depends on deployment.

**CR-6**
- **File / lines:** `/tmp/aging-review-5mpim4s1/app/app.py` lines 160–168, 171–172
- **Fault and evidence:** The fallback parse at line 166, `datetime.fromisoformat(last_notification.replace("Z", "+00:00"))`, sits outside any `try` block. A stored value that fails both parses, or a `None` or non-string value that raises `TypeError`/`AttributeError`, propagates to `except Exception` (line 171) and turns the whole response into HTTP 500. The comment on line 165 claims it will "treat as UTC naive and attach UTC", but no tzinfo is attached on either path. A value stored without an offset therefore comes back naive, so the timezone handling is inconsistent across rows.
- **Execution path:** POST /services → loop over rows → `fromisoformat` fails → fallback fails → 500.
- **Affected state/resource:** The correctness and availability of `/services` for one token.
- **Triggering conditions:** Any `last_notification` value not written by `utc_now_iso()`, for example from manual edits, an older schema, or another writer to the shared file (see CR-2 and CR-5).
- **Existing bounding logic:** There is one fallback attempt, but it covers only the `Z` suffix case and is not itself guarded. Nothing skips or isolates a bad row.
- **Plausible runtime consequence:** One malformed row permanently breaks `/services` for its token until the row is fixed by hand. Naive and aware datetimes can also be serialized side by side and misread by clients.
- **Severity / confidence:** low / med
- **Validation assumptions:** Rows not written by this code exist. Values written by this code always parse on Python 3.7+, so the trigger needs an external or legacy writer.

## Handoff B: fastapi-developer (FA-*)

FA-1
- File and lines: /tmp/aging-review-5mpim4s1/app/app.py, lines 46-61, 64-79 (also 24-43)
- Fault: Every DB call does `sqlite3.connect(DB_PATH)` and `conn.close()` (lines 48, 66) while holding the global `threading.Lock` `_db_lock` (lines 47, 65). All reads and writes are serialized behind that one lock. Each call also pays for a file open and a commit with fsync (line 59).
- Execution path: `POST /heartbeat` and `POST /services` are sync `def` handlers, so Starlette runs them on the anyio threadpool (default 40 threads). Each handler calls `upsert_heartbeat` or `list_services_for_token`, which blocks on `_db_lock`.
- Affected state or resource: `_db_lock`, the threadpool tokens, and the sqlite file db.sqlite3. The relative `DB_PATH` also depends on the process cwd.
- Triggering conditions: concurrent requests. The cost grows with heartbeat write rate. Each write holds the lock for a full journal commit with fsync, and readers queue behind it.
- Existing cleanup, lifecycle, or bounding logic: the connection is closed in `finally`, so there is no connection leak. The lock gives mutual exclusion, but it is the cause of the serialization, not a bound on it. Nothing limits the queue of waiting threads and there is no timeout. The sqlite default busy timeout (5s) is never reached because the lock already serializes access.
- Plausible runtime consequence: throughput is capped at roughly one disk commit per request, and latency rises under load. Up to 40 threads can block on the lock. Once those are exhausted, all other requests queue in the threadpool, including the ones that need no DB access, such as validation and docs.
- Severity and confidence: med severity, high confidence.
- Assumptions needing validation: the actual heartbeat rate, the disk fsync latency, and the default anyio thread limit (40). If the workload is light, the impact is small.

FA-2
- File and lines: /tmp/aging-review-5mpim4s1/app/app.py, lines 64-79 and 152-172; schema at lines 30-39
- Fault: `list_services_for_token` runs `fetchall()` with no LIMIT and returns every row for a token. The handler then builds a `ServiceOut` per row and serializes the whole list through `response_model=List[ServiceOut]`. The work is done twice: Pydantic validation, then response serialization.
- Execution path: `POST /services` goes to `list_services_for_token`, which holds `_db_lock` for the whole fetch. The handler then iterates the rows and calls `datetime.fromisoformat` for each.
- Affected state or resource: the in-memory result list, the CPU and GIL of a threadpool thread, and `_db_lock`.
- Triggering conditions: a token with many registered service ids. `serviceId` and `token` have no length or count limits, and the primary key is (service_id, token), so rows are unbounded.
- Existing cleanup, lifecycle, or bounding logic: none. There is no pagination, no row cap, and no retention or deletion of stale heartbeats anywhere, so the table only grows.
- Plausible runtime consequence: large responses with high latency and memory use. The query holds the global lock while it materializes all rows, which blocks heartbeat writes. The ORDER BY on service_id for a token lookup uses idx_heartbeats_token, which is not a covering index for that sort, so large tokens add sort cost.
- Severity and confidence: low to med severity, med confidence.
- Assumptions needing validation: per-token row counts in production, and whether the table is ever cleaned outside this code.

FA-3
- File and lines: /tmp/aging-review-5mpim4s1/app/app.py, lines 17, 24-43, 113-115
- Fault: the deprecated `@app.on_event("startup")` hook calls `init_db()`, which blocks on `_db_lock` and does sync sqlite I/O. The startup hook is a sync function, so Starlette runs it in the threadpool. Schema creation happens only in this hook, with no handling for failure, and the `if __name__` block passes the `app` object directly to `uvicorn.run`.
- Execution path: on app startup the hook runs `init_db()`, which does the CREATE TABLE and CREATE INDEX. If the hook raises, for example because the cwd is not writable or the DB is locked, startup fails. Any lifespan context a framework version provides is bypassed.
- Affected state or resource: the `heartbeats` table and its index.
- Triggering conditions: running with multiple workers (uvicorn `--workers`, gunicorn) or a read-only or different cwd. Each worker process runs `init_db()` separately. Table creation is guarded by IF NOT EXISTS, but the processes have separate `threading.Lock` instances, so cross-process contention is possible.
- Existing cleanup, lifecycle, or bounding logic: the IF NOT EXISTS clauses make repeated init idempotent. The `threading.Lock` only protects threads inside one process. With several worker processes, sqlite's file locking is the only protection, and the 5s default busy timeout then applies.
- Plausible runtime consequence: under multi-worker startup or load, "database is locked" `OperationalError` can occur. The handlers catch it and return 500 without retry. Under multi-worker use the in-process lock gives a false sense of safety, and writes in different processes can fail with 500s.
- Severity and confidence: low to med severity, low to med confidence.
- Assumptions needing validation: the deployment (number of workers) and the sqlite journal mode (default rollback journal, not WAL).

FA-4
- File and lines: /tmp/aging-review-5mpim4s1/app/app.py, lines 133-139 and 157-172
- Fault: the broad `except Exception` converts every error into a generic 500 and discards logging. `fromisoformat` failure handling is also wrong. The fallback on line 166 can raise an uncaught `ValueError`, and that `ValueError` is caught by the outer `except Exception` and turned into a 500 for the entire request. One corrupt row therefore makes the whole token's listing fail permanently.
- Execution path: `POST /services` loops over the rows, and a bad `last_notification` raises in the inner `except` handler (line 166). The outer `except Exception` returns 500.
- Affected state or resource: the response for every request carrying that token, and the sqlite rows.
- Triggering conditions: any stored value that `fromisoformat` rejects. The code writes only `utc_now_iso()`, so this needs external modification of the DB or a format change.
- Existing cleanup, lifecycle, or bounding logic: none for bad rows, and there is no skip, repair or log. The "defensive" branch only handles a trailing "Z", which Python 3.11+ already accepts in `fromisoformat`, so it is dead code for realistic inputs.
- Plausible runtime consequence: a persistent 500 for the affected token with no diagnostic output, because errors, including lock timeouts, are swallowed without logging. This is not a likely path in normal operation.
- Severity and confidence: low severity, low confidence.
- Assumptions needing validation: whether anything other than this code writes to the DB.

FA-5
- File and lines: /tmp/aging-review-5mpim4s1/app/app.py, lines 124-131 and 125 (service_id normalization), with the key at lines 30-35
- Fault: the handler strips `serviceId` (line 125) but not `token`, and the key is (service_id, token). The listing query filters on `token` only. Service ids and tokens are unbounded strings, so each distinct pair adds a row, and rows are never deleted or expired.
- Execution path: `POST /heartbeat` goes to `upsert_heartbeat`, which inserts a new row for every new (service_id, token) pair.
- Affected state or resource: the size of the sqlite file and the index idx_heartbeats_token, which are maintained on every insert.
- Triggering conditions: sustained traffic with varied service ids or tokens, for example clients that embed a changing value in the id.
- Existing cleanup, lifecycle, or bounding logic: none. The upsert prevents duplicates only for identical pairs.
- Plausible runtime consequence: unbounded disk growth over time and slower writes. The secondary index adds write cost per insert, and the table and index grow without limit. This degrades `/services` latency (see FA-2) and fsync time.
- Severity and confidence: low severity, med confidence.
- Assumptions needing validation: whether the id and token spaces are bounded in real use.

FA-6
- File and lines: /tmp/aging-review-5mpim4s1/app/app.py, lines 20-21 and 46-61
- Fault: the timestamp is generated in `heartbeat` before the handler waits for `_db_lock` and the commit (line 134 evaluates `utc_now_iso()` as an argument before `upsert_heartbeat` acquires the lock). When requests queue behind the lock (FA-1), threads are released in arbitrary order, so an older timestamp can be written after a newer one. The upsert overwrites unconditionally (`SET last_notification = excluded.last_notification`) with no monotonic check.
- Execution path: two concurrent `POST /heartbeat` calls for the same (service_id, token). Thread A computes t1, thread B computes t2 > t1, and B acquires the lock first. A then overwrites with t1.
- Affected state or resource: the `last_notification` column for the pair.
- Triggering conditions: contention on the lock, so that the delay between timestamp creation and write is comparable to the gap between requests. `threading.Lock` is not FIFO.
- Existing cleanup, lifecycle, or bounding logic: the lock serializes the writes but does not order them by timestamp, so it does not prevent this.
- Plausible runtime consequence: `/services` can report a slightly stale last notification for a service. The error is bounded by the queueing delay, which is small unless the system is overloaded, so the impact is minor.
- Severity and confidence: low severity, med confidence.
- Assumptions needing validation: the real lock queue delay under production load.