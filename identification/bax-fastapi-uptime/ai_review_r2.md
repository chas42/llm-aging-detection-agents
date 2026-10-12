# 1. Performance‑engineer final consolidated report (verbatim)

# Adjudication report: static-analysis hypotheses (not runtime-confirmed)

Scope: `/tmp/aging-review-fskzf8s2/app/app.py`, the only file read. I did not check `requirements.txt`. Every conclusion below is a static-analysis hypothesis that needs runtime confirmation.

## Consolidated findings

### F1. Table growth with no retention, and unbounded `/services` reads (merges CR-2, FA-2)
- **Static validity:** Confirmed.
  - Lines 50-58 only insert or update.
  - No code anywhere deletes or expires rows.
  - Line 77 uses `fetchall()` with no LIMIT.
  - Lines 159-168 build a second list of objects, and `response_model` (line 149) validates and serializes the list again.
  - `serviceId` and `token` have no length bounds (lines 83-92).
- **Standalone or merged:** Merged (CR-2, FA-2).
- **Performance relevance:** Direct on the read path. The growth side is conditional on how many distinct (service_id, token) pairs appear.
- **Resource:** SQLite file size, per-request memory and CPU, and time spent holding `_db_lock`. `fetchall` runs inside the lock, but object building and serialization run after it is released.
- **Trigger:** Repeated `POST /heartbeat` with new serviceId/token pairs, and `POST /services` for a token with many rows.
- **Cleanup and bounds:** The primary key de-duplicates only identical pairs. The token index speeds lookup but does not limit result size. Nothing else bounds it.
- **Direct evidence versus assumption:**
  - Direct evidence: no deletion, no LIMIT, no length caps.
  - Assumptions: the rate at which new pairs appear, services per token, and the absence of external cleanup or an upstream body-size limit.
  - CR-2 says there are "three copies" in memory. That is loosely stated. Per-request copies are transient, and only the on-disk table persists.
  - FA-2 says `ORDER BY` needs a sort. That is unverified. The index on `token` alone does not provide `service_id` order, but the autoindex on (service_id, token) might be chosen instead.
- **Mechanism class:**
  - Persistent table growth is cumulative resource retention (on disk).
  - Per-request memory is transient overhead that scales with the retained row count.
- **Aging relevance:** Conditionally plausible aging mechanism. Trigger, accumulation, no cleanup and long-run degradation are all present in the code. The weak link is whether the trigger (distinct pairs growing over time) actually occurs. If the service/token set is fixed, growth stops, and the code does not show which case applies.
- **Final severity / confidence:** Medium / medium. Confidence is lowered from CR-2's "high" because the growth rate is workload-dependent.

### F2. Global lock held across connect, I/O, commit and close, with a fresh connection per request (merges CR-1, FA-1)
- **Static validity:** Confirmed.
  - `_db_lock` is held around connect, execute, commit and close in `upsert_heartbeat` (lines 47-61).
  - It is held around connect, SELECT, `fetchall` and close in `list_services_for_token` (lines 65-79).
  - Reads and writes are fully serialized.
  - Both handlers are sync `def`, so they run in a thread pool.
- **Standalone or merged:** Merged (CR-1, FA-1).
- **Performance relevance:** Direct, as throughput and latency overhead. Thread-pool saturation is conditional on concurrency.
- **Resource:** `_db_lock`, thread-pool workers, and file I/O (journal and fsync per commit under default settings).
- **Trigger:** Concurrent requests.
- **Cleanup and bounds:**
  - `finally: conn.close()` prevents connection leaks. FA-1 correctly notes this.
  - Concurrency is bounded only by the thread pool.
  - Lock waits are unbounded.
- **Direct evidence versus assumption:**
  - Direct evidence: the lock scope and a commit per request.
  - Assumptions: the 40-token default thread pool, fsync cost on the storage, and request rate. The framework default was not verified here.
  - CR-1's chain (clients time out, then services are misread as down) is speculative.
- **Mechanism class:** Transient or repeated overhead. At most, finite-resource exhaustion (thread pool) under burst load. Nothing accumulates across time, and the pool recovers when load drops.
- **Aging relevance:** Non-aging performance fault. There is no persistent accumulation. It can compound F1 by lengthening lock holds as the table grows.
- **Final severity / confidence:** Medium / high that the serialization exists. CR-1's "high" severity is not supported without load data.

### F3. Per-row timestamp fallback re-raises, so one bad row fails the whole `/services` call (CR-3)
- **Static validity:** Confirmed as a code defect. Line 166 raises `ValueError` again for non-"Z" malformed values, and line 171 turns it into a 500. The comment at line 165 does not match the code.
- **Standalone or merged:** Standalone.
- **Performance relevance:** None. This is an availability and correctness issue.
- **Resource:** Not applicable. The only effect is a failed request.
- **Trigger:** A malformed `last_notification` value. The only writer in this file is `utc_now_iso()`, which always produces valid ISO strings, so this needs out-of-band writes.
- **Cleanup and bounds:** Not applicable.
- **Direct evidence versus assumption:** The code path is direct evidence. The trigger is an assumption.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity / confidence:** Low / high. The reviewer's "low-med" is reduced because no writer in the file can produce a bad value.

### F4. Relative `DB_PATH` (merges CR-4, FA-4)
- **Static validity:** Qualified. The relative path (line 12) is real and is resolved at every connect. The performance claims are weak.
  - CR-4's "stray DB files build up" is speculative.
  - FA-4's read-only working directory and "readers blocked without WAL" claims are generic. The second also overlaps with F2.
- **Standalone or merged:** Merged (CR-4, FA-4).
- **Performance relevance:** None. This is a data-location and correctness concern.
- **Resource:** Not applicable.
- **Trigger:** Starting the process from a different working directory.
- **Cleanup and bounds:** None.
- **Direct evidence versus assumption:** The relative path is direct evidence. The launch method is an assumption.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity / confidence:** Low / medium.

### F5. Multi-process SQLite contention (CR-5, plus the multi-worker aspects of FA-1 and FA-3)
- **Static validity:** Qualified. The lock is process-local, and the code sets no timeout or WAL, so sqlite3's default timeout applies (the 5 s figure is a library default and was not checked in this file). This only matters if multiple workers or replicas share the file. The `__main__` launch (line 177) is single-process.
- **Standalone or merged:** Merged (CR-5, FA-3's multi-worker clause). FA-3's event-loop blocking at startup is negligible and not an aging issue.
- **Performance relevance:** Conditional.
- **Resource:** SQLite file locks and request failures.
- **Trigger:** A multi-process deployment under write load.
- **Cleanup and bounds:** The sqlite3 busy timeout is the only bound. Errors become 500s.
- **Direct evidence versus assumption:** The topology is not shown anywhere in the code, so it is an assumption.
- **Mechanism class:** Transient or repeated overhead (lock wait and failed requests). Nothing accumulates.
- **Aging relevance:** Non-aging performance fault, and only if multi-process.
- **Final severity / confidence:** Low-medium / low.

### F6. Schema created only at startup (CR-6, FA-3)
- **Static validity:** Qualified. `init_db` runs only in the startup hook (lines 113-115). The "no such table" failure needs a bypassed startup, or a deleted or replaced DB file while the process is running. Both are external events. The deprecation point is not a runtime concern.
- **Standalone or merged:** Merged (CR-6, FA-3).
- **Performance relevance:** None.
- **Resource:** Not applicable. The only effect is requests failing with 500.
- **Trigger:** External manipulation of the DB file, or a deployment that skips startup.
- **Cleanup and bounds:** Not applicable.
- **Direct evidence versus assumption:** The startup-only schema creation is direct evidence. The triggers are assumptions.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity / confidence:** Low / low.

## Summary of aging classification
- The only candidate aging mechanism is F1, and it is conditional.
  - The repeatable trigger (heartbeats with new IDs or tokens) is plausible.
  - Accumulation (rows on disk) is directly evidenced.
  - Cleanup is absent.
  - Degradation (longer queries, bigger responses, longer lock holds) is plausible.
  - It depends on distinct-pair churn, which the code cannot show.
- F2 and F5 are non-aging performance faults.
- F3, F4 and F6 are unrelated to performance.
- Neither handoff's claims of thread-pool exhaustion, missed heartbeats, or "gradual aging" have been verified at runtime.

## SELF-SOURCED — UNVALIDATED
1. **Per-request token string retention in the `token` index and the primary key.** The token is stored twice per row (the table and the index), plus the autoindex for the primary key. A large token multiplies per-row disk growth. This is an aggravator of F1, not an independent mechanism. It has no cross-check.
2. None further.

---

# 2. Appendix: raw Phase‑1 handoffs (unedited)

## Handoff A: code-reviewer (CR-*)

**CR-1**
- **File / lines:** /tmp/aging-review-fskzf8s2/app/app.py, lines 17, 46-61, 64-79, 124-139, 152-172
- **Fault and evidence:** One process-wide `_db_lock = threading.Lock()` (line 17) is held for the whole DB operation in both data paths. In `upsert_heartbeat` the lock is taken at line 47 and covers `sqlite3.connect(DB_PATH)` (48), `conn.execute(... INSERT ... ON CONFLICT ...)` (50-58), `conn.commit()` (59) and `conn.close()` (61). In `list_services_for_token` the lock is taken at line 65 and covers connect (66), the SELECT (68-76), `cur.fetchall()` (77) and close (79). Every request opens and closes a new connection, and every heartbeat commits on its own (synchronous journal write/fsync) while still holding the lock. As a result, reads and writes from all clients run one at a time.
- **Execution path:** The handlers `heartbeat` (line 124) and `services` (line 152) are sync `def`. Starlette sends them to the anyio worker thread pool (40 tokens by default). Each worker thread then blocks on `_db_lock`.
- **Affected state/resource:** `_db_lock`; the anyio thread-pool tokens; SQLite file I/O (connect, journal, fsync).
- **Triggering conditions:** Many services sending heartbeats at the same time, or `/services` calls that return many rows (see CR-2) while heartbeats are arriving.
- **Existing bounding logic and why it falls short:** The lock prevents SQLite write contention inside one process. However, it has no timeout and no read/write separation (reads also take the exclusive lock). Nothing reuses connections or batches commits, so the work done inside the critical section is never bounded.
- **Plausible runtime consequence:** Throughput is capped at one DB operation at a time, and each write includes connect plus fsync. Queueing delay grows with load. When threads blocked on the lock use up the 40 thread-pool tokens, other requests also queue, latency rises sharply and clients may time out. Missed heartbeat deadlines could then be misread as services being down.
- **Severity / confidence:** high / high
- **Assumptions to validate:** Real heartbeat rate and concurrency; fsync cost on the deployment's storage; the anyio thread-limiter default not being overridden.

**CR-2**
- **File / lines:** /tmp/aging-review-fskzf8s2/app/app.py, lines 28-37, 46-58, 64-77, 82-87, 157-168
- **Fault and evidence:** The `heartbeats` table (lines 30-35) gains a new row for every distinct `(service_id, token)` pair (`INSERT ... ON CONFLICT(service_id, token)`, lines 52-55). No code anywhere deletes rows, expires them, or applies retention. The input models (lines 83-87) have no `max_length` on `serviceId` or `token`, so each row can be any size. On the read side, `cur.fetchall()` (line 77) loads every matching row into memory at once with no LIMIT or pagination. Lines 159-167 then build a second full list of `ServiceOut` objects, and FastAPI serializes and validates that list again (`response_model=List[ServiceOut]`, line 149).
- **Execution path:** `POST /heartbeat` with new serviceId/token values leads to `upsert_heartbeat`, which adds rows. `POST /services` leads to `list_services_for_token`, which runs `fetchall()`; the loop at lines 160-167 then builds the response.
- **Affected state/resource:** Size of the SQLite file on disk; process memory during `/services`; time spent holding `_db_lock` (fetchall runs inside the lock).
- **Triggering conditions:** A long-running deployment with changing service IDs (for example per-instance or per-deploy IDs), clients that rotate tokens, or very large serviceId/token strings.
- **Existing bounding logic and why it falls short:** The composite primary key removes duplicates only for identical pairs. The token index (line 39) speeds up lookups but does not limit result size. There is no TTL, cleanup job, size check or row cap.
- **Plausible runtime consequence:** The DB file grows over time with no limit. For a token with many services, each `/services` call holds the global lock longer (making CR-1 worse), uses memory that grows with row count (three copies: raw tuples, model objects, serialized JSON), and gets slower. This is gradual aging and degradation.
- **Severity / confidence:** med / high
- **Assumptions to validate:** How fast distinct service/token pairs are created in production; how large services-per-token gets; payload sizes allowed by any upstream proxy.

**CR-3**
- **File / lines:** /tmp/aging-review-fskzf8s2/app/app.py, lines 157-168 (specifically 162-167, 171-172)
- **Fault and evidence:** The timestamp parsing for each row has a fallback that does not actually recover. If `datetime.fromisoformat(last_notification)` (line 163) raises `ValueError`, the fallback at line 166 calls `fromisoformat` again with only `"Z"` replaced. For any value that is not a "Z" problem, this raises `ValueError` again inside the `except` block. That error is not caught per row, so it reaches `except Exception` at line 171 and becomes `HTTPException(500)` at line 172 for the whole request. The comment at line 165 ("treat as UTC naive and attach UTC") also does not match the code: no tzinfo is attached, so a naive stored value would come back naive instead of UTC.
- **Execution path:** `POST /services`, then the loop at lines 160-167, then a row with a malformed or unexpected `last_notification`, then the outer handler returns 500.
- **Affected state/resource:** The availability of `/services` for every service under that token.
- **Triggering conditions:** Any row whose `last_notification` is not valid ISO 8601: manual edits, data migrated from another writer, a different Python version's isoformat output, or a shared DB file (see CR-4/CR-5).
- **Existing bounding logic and why it falls short:** The inner try/except is meant to be defensive, but its fallback can raise the same exception again, and there is no skip-or-continue per row.
- **Plausible runtime consequence:** One bad row makes `/services` fail permanently with 500 for that token until the row is fixed by hand. The monitoring overview becomes unavailable.
- **Severity / confidence:** low-med / high
- **Assumptions to validate:** Whether anything other than `utc_now_iso()` writes to the table; the Python version (3.11+ `fromisoformat` already accepts "Z", so the fallback is reached only for genuinely malformed data).

**CR-4**
- **File / lines:** /tmp/aging-review-fskzf8s2/app/app.py, lines 12, 26, 48, 66
- **Fault and evidence:** `DB_PATH = "db.sqlite3"` (line 12) is a relative path. It is resolved against the process's current working directory each time `sqlite3.connect(DB_PATH)` runs (lines 26, 48, 66). The DB location therefore depends on where the process was started (or on any later `chdir`), not on where the app lives.
- **Execution path:** Startup `init_db()` (line 115) creates the table in whatever the CWD is. All later reads and writes also resolve against the CWD.
- **Affected state/resource:** Persistent heartbeat data.
- **Triggering conditions:** Starting the app from different directories (systemd unit, container WORKDIR changes, `uvicorn app.app:app` run from the parent directory), or restarts with a different CWD.
- **Existing bounding logic and why it falls short:** None. No absolute path is built and the location cannot be configured.
- **Plausible runtime consequence:** After a restart from another CWD, a new empty DB is created silently. `/services` returns `[]` for every token, so the history looks lost and monitored services appear never to have reported. Stray DB files also build up in different directories.
- **Severity / confidence:** med / med
- **Assumptions to validate:** How the deployment launches the process and whether its CWD is stable.

**CR-5**
- **File / lines:** /tmp/aging-review-fskzf8s2/app/app.py, lines 17, 47-59, 65-77
- **Fault and evidence:** The serialization relies only on `threading.Lock()` (line 17), which works within one process. If the app runs with several workers or processes (uvicorn `--workers N`, gunicorn, several container replicas on a shared volume), the processes compete for the SQLite file without coordination. Connections use the default `sqlite3.connect` timeout of 5 seconds and the default rollback journal (no WAL is set anywhere in lines 24-43). A writer's commit (line 59) takes an exclusive file lock that blocks readers (line 68) and other writers. If the lock lasts longer than 5 seconds, `sqlite3.OperationalError: database is locked` is raised, and lines 138-139 or 171-172 turn it into a 500.
- **Execution path:** Concurrent `POST /heartbeat` and `POST /services` in different worker processes, each running `sqlite3.connect`, then execute/commit.
- **Affected state/resource:** SQLite file locks; heartbeat writes may be lost.
- **Triggering conditions:** A multi-process deployment combined with write bursts or slow storage. Not triggered when started through `__main__` (line 177), which runs a single process.
- **Existing bounding logic and why it falls short:** `_db_lock` only covers threads within one process. SQLite's 5-second busy timeout is the only coordination between processes, and failures are not retried.
- **Plausible runtime consequence:** Under load, some heartbeats are dropped with 500s, causing false "stale service" readings, and some `/services` requests fail.
- **Severity / confidence:** med / low-med
- **Assumptions to validate:** Whether production runs more than one worker or replica against the same DB file; storage latency.

**CR-6**
- **File / lines:** /tmp/aging-review-fskzf8s2/app/app.py, lines 113-115, 24-43
- **Fault and evidence:** Schema creation runs only in `@app.on_event("startup")` (lines 113-115). `on_event` is deprecated in FastAPI 0.115.6 (requirements.txt line 7) in favor of lifespan handlers, but it still runs under uvicorn. There are two problems. First, if `init_db()` fails, nothing retries it. Second, any code path that skips the startup event (for example a test client used without its context manager, or mounting the app as a sub-application, where the sub-app's startup events are not run) leaves the table missing. In that case every `INSERT` (line 50) and `SELECT` (line 68) raises `sqlite3.OperationalError: no such table`, which is reported only as a generic 500 (lines 139, 172). Related to CR-4, if the CWD-relative DB file is deleted or replaced while the process is running, the next connect at line 48 or 66 creates a new empty file with no schema, and every request then returns 500 until restart.
- **Execution path:** Process start, with the startup hook skipped or failing, or the DB file replaced at runtime. After that, every `/heartbeat` and `/services` request ends in OperationalError and a 500.
- **Affected state/resource:** The DB schema and endpoint availability.
- **Triggering conditions:** Sub-app mounting, a startup hook bypassed or failing, or the DB file being removed or rotated at runtime.
- **Existing bounding logic and why it falls short:** `CREATE TABLE IF NOT EXISTS` runs only once at startup and is never re-run lazily or checked again.
- **Plausible runtime consequence:** All requests fail with 500 until the process restarts, and the generic error message hides the cause.
- **Severity / confidence:** low / low-med
- **Assumptions to validate:** The deployment topology (mounted or standalone) and whether any external process manages or rotates the DB file.

## Handoff B: fastapi-developer (FA-*)

FA-1
- File and lines: /tmp/aging-review-fskzf8s2/app/app.py, lines 17, 46-61, 64-79 (called from sync handlers at 124-139 and 152-172)
- Fault: Every request opens a new `sqlite3.connect(DB_PATH)` and closes it. All DB access is serialized behind one process-wide `threading.Lock` (`_db_lock`). The lock is held across connect, execute, commit and close, so it includes disk fsync on `conn.commit()` (line 59). Reads (`list_services_for_token`, lines 65-79) take the same lock, so they queue behind writes.
- Execution path: `POST /heartbeat` or `POST /services` → sync `def` handler → Starlette runs it in the AnyIO threadpool (default 40 tokens) → `upsert_heartbeat` or `list_services_for_token` → `with _db_lock`.
- Affected state or resource: the AnyIO threadpool, `_db_lock`, and the SQLite file `db.sqlite3` (journal fsync per commit).
- Triggering conditions: concurrent requests, which are normal for heartbeat traffic from many services.
- Existing cleanup or bounding logic: `conn.close()` in `finally` blocks prevents connection leaks. There is no pooling, no WAL mode, no busy timeout and no bound on lock wait. The `finally` clauses are enough for cleanup but don't address serialization. The lock makes all requests single-file, and only the 40-thread pool bounds concurrency.
- Plausible runtime consequence: throughput is capped at the rate of serialized fsync commits. Under bursts, up to 40 threads block on the lock. The threadpool is shared by all sync endpoints and by FastAPI's own sync work, so latency rises and requests queue. Per-request connect and close adds more overhead.
- Severity: med. Confidence: high.
- Assumptions needing validation: the actual request rate, disk fsync latency, and the threadpool size. The lock also only covers a single process, so a multi-worker deployment would fall back on SQLite file locking. The default 5 s sqlite3 timeout could then produce "database is locked" errors that surface as 500s (lines 138-139).

FA-2
- File and lines: /tmp/aging-review-fskzf8s2/app/app.py, lines 64-79, 152-168, 149
- Fault: `list_services_for_token` runs `cur.fetchall()` and returns an unbounded result set. The handler then builds a `ServiceOut` object per row, and the `response_model=List[ServiceOut]` (line 149) validates and serializes every row again. There is no LIMIT or pagination. The `heartbeats` table also grows without bound, because rows are only ever inserted or updated (lines 50-58) and never expire or get deleted.
- Execution path: `POST /services` → `list_services_for_token` → loop at lines 160-167 → response model validation and serialization.
- Affected state or resource: memory and CPU in the worker thread, the table size, and the time `_db_lock` is held (the lock is held through `fetchall`).
- Triggering conditions: a token with many registered services (the primary key is `(service_id, token)`, so cardinality is unbounded), or a table that has grown over long uptime.
- Existing cleanup or bounding logic: none. The index on `token` (lines 38-40) speeds lookup, but `ORDER BY service_id` still needs a sort for large result sets.
- Plausible runtime consequence: large responses and long lock holds starve other requests (compounding FA-1). Memory use spikes. Latency grows with table size over time, and stale rows from dead services accumulate forever.
- Severity: med. Confidence: med.
- Assumptions needing validation: the real number of services per token, how long the service stays up, and whether any external cleanup exists.

FA-3
- File and lines: /tmp/aging-review-fskzf8s2/app/app.py, lines 113-115 and 24-43
- Fault: Startup uses the deprecated `@app.on_event("startup")` hook instead of a lifespan context. `init_db()` runs synchronously on the event loop thread, and it blocks on `_db_lock` plus sqlite connect, DDL and commit. There is no shutdown hook or lifespan teardown.
- Execution path: ASGI lifespan startup → `_startup` (a sync function, which Starlette calls directly on the loop) → `init_db`.
- Affected state or resource: the event loop during startup, and the `db.sqlite3` file.
- Triggering conditions: slow or locked disk, or another process holding the SQLite file lock (for example a multi-worker start, where every worker runs DDL at once). The default 5 s lock timeout then applies.
- Existing cleanup or bounding logic: `conn.close()` is in `finally`. An exception propagates and aborts startup, and there is no retry. Because DDL uses `IF NOT EXISTS`, repeated runs are idempotent, but concurrent workers can still hit "database is locked", which crashes startup.
- Plausible runtime consequence: a delayed or failed startup. The blocking is limited to startup, so the impact is small. If `on_event` is ever combined with a `lifespan` argument it would be silently ignored.
- Severity: low. Confidence: med.
- Assumptions needing validation: the deployment worker count and the FastAPI and Starlette versions in use.

FA-4
- File and lines: /tmp/aging-review-fskzf8s2/app/app.py, lines 50-58 and 52-55 (`INSERT ... ON CONFLICT DO UPDATE`), 12
- Fault: `DB_PATH = "db.sqlite3"` is a relative path, so it resolves against the process's current working directory at each call, not against the app directory. Connections use the default journal mode (rollback journal), with no WAL.
- Execution path: every `sqlite3.connect(DB_PATH)` in `init_db`, `upsert_heartbeat` and `list_services_for_token`.
- Affected state or resource: which database file is used, and the journal file.
- Triggering conditions: the process's working directory changes, or it is started from a different directory (for example `uvicorn app.app:app` versus `python app.py`), or the working directory is read-only or ephemeral.
- Existing cleanup or bounding logic: none.
- Plausible runtime consequence: heartbeat data lands in different files depending on the start directory. After a restart from a different directory, state appears lost, and services look absent from `/services`. A read-only working directory causes 500s on every request. Readers are blocked during writes because there is no WAL.
- Severity: low. Confidence: low.
- Assumptions needing validation: how the process is launched and which working directory it uses.