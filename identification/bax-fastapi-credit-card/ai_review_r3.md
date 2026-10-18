# Adjudication Report

I checked every finding against `/tmp/aging-review-oaimrqsw/app/app.py`, the only file involved. These are static-analysis hypotheses. None of them is empirically confirmed aging, and each needs runtime confirmation.

## Consolidated findings

### 1. Unbounded `phone_numbers` list (merged: CR-1 + FA-1)
- **Static validity:** Confirmed. `min_length=1` is the only bound (line 104). The placeholders are built from `len(phones)` (line 150) and bound at line 162. Nothing catches `sqlite3.OperationalError`.
- **Performance relevance:** Conditional.
- **Resource and conditions:** The request thread, request memory and CPU. The triggers are a request with more unique phones than the SQLite variable limit, or a very large list below that limit.
- **Cleanup and bounds:** Dedupe only. `db_conn` closes the connection in `finally`. Each request is self-contained and releases everything when it ends.
- **Direct code evidence:** No upper bound. The SQL text length scales with N. The statement is rebuilt for every request.
- **Assumptions:** The SQLite build's variable limit (999 or 32766). Any body-size limit at the server or proxy. The 500 is the standard outcome for an unhandled exception, which I did not verify at runtime.
- **Mechanism class:** Transient or repeated overhead. A 500 on an over-limit request is a correctness and availability problem, not a performance one.
- **Aging relevance:** Non-aging performance fault. It has a repeatable trigger, but nothing persists between requests.
- **Severity and confidence:** Low-medium severity, high confidence on the code facts. The reviewers' "med" severity is trimmed because the cost is per-request and bounded by body size.

### 2. SQLite write-lock contention with the default 5 s timeout in the threadpool (merged: CR-2 + FA-2)
- **Static validity:** Qualified. These facts are supported:
  - `sqlite3.connect` is called without `timeout=` (line 49), so the default busy timeout applies.
  - Handlers are sync `def`, so they run in the framework threadpool.
  - The write path is `INSERT OR IGNORE` plus `commit` (lines 131-137 and 54).
  - `OperationalError` is not handled.
- **Unsupported claims:**
  - Pool size, WAL checkpoint behavior and the sync exception handler running in the pool are framework assumptions.
  - FA-2's "long-held reader" is weak, because WAL readers do not block writers.
  - The transactions are short, so contention needs a heavy write burst or slow disk I/O.
- **Performance relevance:** Conditional.
- **Resource and conditions:** The write lock and threadpool slots. The trigger is sustained concurrent writes or slow fsync.
- **Cleanup and bounds:** Each wait is bounded by about 5 s, and the connection is closed in `finally`. Blocked threads are released when the timeout expires.
- **Mechanism class:** Finite-resource exhaustion, but only transient and load-driven. Threads are released after the timeout.
- **Aging relevance:** Non-aging performance fault, with a conditionally plausible link only if write cost grows over time, for example through finding 3. There is no persistent accumulation by itself.
- **Severity and confidence:** Medium severity, low-medium confidence.

### 3. Unbounded table growth with redundant indexes (CR-4, standalone)
- **Static validity:** Qualified.
  - Confirmed in code: no length limit on `phone` or `credit_card`, no delete, expiry or quota path, and `INSERT OR IGNORE` dedupes only exact pairs.
  - Confirmed: `UNIQUE(credit_card, phone)` plus `idx_assoc_phone` and `idx_assoc_card` (lines 36, 40 and 41). The card-only index is redundant with the leading column of the UNIQUE index.
  - Disk-full failure and the slowdown of later inserts are runtime consequences that depend on volume.
- **Performance relevance:** Conditional.
- **Resource and conditions:** The database file, the WAL and the page cache. Every new distinct pair adds a row and three index entries.
- **Cleanup and bounds:** None in the application. Growth follows the number of distinct pairs written. Any retention policy would be external, and I saw none.
- **Direct code evidence:** The schema and the insert path. Whether it matters depends on the write volume and the disk.
- **Mechanism class:** Cumulative resource retention. It is persistent data growth, and this is a service whose purpose is to store these rows, so some of the growth is by design.
- **Aging relevance:** Conditionally plausible aging mechanism. It has a repeatable trigger, accumulation, and no cleanup or bound. The degradation (insert and lookup slowdown, disk exhaustion) over long runs is plausible but depends on volume. Index lookups scale roughly logarithmically, so the slowdown is gradual.
- **Severity and confidence:** Low-medium severity, medium confidence.

### 4. Retrieve query shape: full materialization, no LIMIT, temp structures (merged: CR-5 + FA-4)
- **Static validity:** Qualified.
  - Confirmed: no LIMIT, `fetchall()` at line 162, and GROUP BY, HAVING, COUNT(DISTINCT) and ORDER BY.
  - Unverified: whether the planner uses `idx_assoc_phone`, and whether temp B-trees are needed. The reviewers acknowledge that EXPLAIN is needed.
  - FA-4's claim that a long read snapshot lets the WAL grow is speculative for typical sub-second reads.
- **Performance relevance:** Conditional.
- **Resource and conditions:** CPU, memory and thread time. The trigger is phones linked to many cards, or many phones per request.
- **Cleanup and bounds:** Everything is per-request and freed on return. The result size is bounded only by the table contents.
- **Mechanism class:** Transient or repeated overhead. Cost grows with data size, which is not the same as accumulation within a process.
- **Aging relevance:** Non-aging performance fault. A latency increase tied to finding 3's data growth is possible, but this mechanism is not itself accumulating.
- **Severity and confidence:** Low severity, medium confidence.

### 5. Per-request connection create/close, with a commit on the read path (merged: FA-3 + the connection-overhead part of CR-5)
- **Static validity:** Confirmed on the code facts. `db_conn` connects, runs the PRAGMA, commits and closes on every request (lines 47-56).
- **Not supported:** The WAL checkpoint and file deletion on last close is a SQLite behavior I did not verify. FA-3's file-descriptor concern is not supported, because the connection is closed in `finally`.
- **Performance relevance:** Conditional. It is a fixed per-request overhead.
- **Mechanism class:** Transient or repeated overhead.
- **Aging relevance:** Non-aging performance fault. The cleanup is deterministic, so there is no accumulation.
- **Severity and confidence:** Low severity, medium confidence.

## Rejected or no performance impact (not counted among the consolidated findings)
- **CR-3 and FA-5 (relative `DB_PATH`, startup race):** Correctness and deployment issues. The relative path is confirmed (lines 12, 25 and 49), but the consequence is a different or empty database, not a performance effect. The multi-worker startup race is speculative, since `__main__` runs a single process. Unrelated to performance. Confidence is low.
- **CR-6 and FA-6 (phone normalization and `isdigit` variance):** Confirmed as code facts (line 76, and `isdigit` at line 90), but they are correctness and data-semantics issues. The only possible performance link is slightly more duplicate rows, which is marginal. Unrelated to performance.

## SELF-SOURCED — UNVALIDATED
None. Nothing else aging-relevant was noticed in the cited code.

---

# Appendix A — Phase-1 handoff: code-reviewer (CR-*)

**CR-1**
- **File / lines:** /tmp/aging-review-oaimrqsw/app/app.py, lines 103-121 and 148-162
- **Fault and evidence:** There is no upper limit on how many phone numbers a request can send. Line 104 is `phone_numbers: List[str] = Field(..., min_length=1)`, with a minimum but no maximum. Line 150 builds `",".join(["?"] * len(phones))`. Line 162 binds `(*phones, len(phones))`, which is N+1 parameters. SQLite caps the number of bound parameters per statement (`SQLITE_MAX_VARIABLE_NUMBER`): 999 before SQLite 3.32, 32766 after.
- **Execution path:** POST /retrieve_cards → Pydantic validation (lines 106-121: normalize the whole list, then dedupe it) → f-string query build (line 152) → `conn.execute` (line 162).
- **Affected state / resource:** Worker threadpool thread, request memory, the SQLite statement compiler.
- **Triggering conditions:** A request with ≥999 unique phones (older SQLite) or ≥32766 (newer). Very large lists also cost CPU and memory well below those caps.
- **Existing bounding logic:** Only `min_length=1` and the dedupe. Neither limits size. The only error handler covers `RequestValidationError` (lines 65-67), so `sqlite3.OperationalError: too many SQL variables` goes unhandled.
- **Plausible consequence:** HTTP 500 for valid large requests. Large bodies cause high CPU and memory use, and SQLite has to compile a huge IN list, which holds a threadpool slot.
- **Severity:** med. **Confidence:** high.
- **Assumptions to validate:** The SQLite version bundled with the deployed Python, which sets the variable limit. Any body-size limit added by a reverse proxy.

**CR-2**
- **File / lines:** /tmp/aging-review-oaimrqsw/app/app.py, lines 47-56 and 128-140
- **Fault and evidence:** Every request opens a new connection (line 49, `sqlite3.connect(DB_PATH, check_same_thread=False)`) with no `timeout=` set, so the default 5 s busy timeout applies. Writes in `associate_card` (lines 131-137) need SQLite's single writer lock. The handlers are synchronous `def` functions (lines 129, 148), so they run in Starlette's limited threadpool (40 tokens by default in anyio). `sqlite3.OperationalError` ("database is locked") is not handled anywhere.
- **Execution path:** Concurrent POST /associate_card → `db_conn()` → `INSERT OR IGNORE` → `conn.commit()` (line 54). An auto-checkpoint can run inside a committing writer and make it slower.
- **Affected state / resource:** SQLite write lock, threadpool slots, file descriptors (one connection per request, plus WAL/SHM handles).
- **Triggering conditions:** A sustained burst of concurrent writes, or one slow writer or checkpoint holding the lock, so that waiters pass 5 s.
- **Existing lifecycle logic:** WAL mode (line 27) lets readers run alongside the writer, but writes are still serialized. `finally: conn.close()` (lines 55-56) stops connections leaking, but nothing bounds lock waits beyond the default timeout and there is no handling or retry for lock errors.
- **Plausible consequence:** Threads blocked for up to 5 s each fill the threadpool. Unrelated requests, including reads and validation-error responses (the sync exception handler also runs in the threadpool), queue up and latency rises. Requests that hit the timeout return 500 and the write is lost.
- **Severity:** med. **Confidence:** med.
- **Assumptions to validate:** Real write concurrency, disk fsync latency, checkpoint duration, and the threadpool size in the deployment.

**CR-3**
- **File / lines:** /tmp/aging-review-oaimrqsw/app/app.py, lines 12, 25 and 49
- **Fault and evidence:** `DB_PATH = "db.sqlite3"` is a relative path. It is resolved against the process working directory, not the application directory, both in `init_db` (line 25) and on every request (line 49).
- **Execution path:** Startup `init_db` (lines 59-61) and every request through `db_conn()`.
- **Affected state / resource:** Where the persistent database lives, and so whether data is kept.
- **Triggering conditions:** Launching the app from a different working directory (systemd unit, container `WORKDIR`, `uvicorn app.app:app` run from a parent directory, test runners), or anything that changes the working directory at runtime.
- **Existing lifecycle logic:** None. `CREATE TABLE IF NOT EXISTS` (line 31) quietly creates a new empty database in whatever directory is current, which hides the problem.
- **Plausible consequence:** Previously stored associations seem to vanish (retrieve returns 404). Separate deployments or restarts write to different files, so data is split across them.
- **Severity:** med. **Confidence:** med.
- **Assumptions to validate:** How the service is launched, and whether the working directory is stable and writable.

**CR-4**
- **File / lines:** /tmp/aging-review-oaimrqsw/app/app.py, lines 31-41 and 79-100
- **Fault and evidence:** `credit_card` and `phone` have no length limit. The validators (lines 85-100) only check for non-empty values and, for cards, `isdigit()`. Each row's text is stored in the table and copied into three B-trees: the `UNIQUE(credit_card, phone)` auto-index (line 36), `idx_assoc_phone` (line 40), and `idx_assoc_card` (line 41). `idx_assoc_card` duplicates the leading column of the UNIQUE index. The schema has `created_at` (line 35) but nothing ever expires or deletes rows.
- **Execution path:** POST /associate_card → `INSERT OR IGNORE` (line 133) → four B-tree writes per new row.
- **Affected state / resource:** Size of the database file and WAL, write amplification, page cache.
- **Triggering conditions:** Ongoing use, especially with many distinct pairs or very long values. Strings of any length pass validation, and `isdigit()` also accepts Unicode digits.
- **Existing bounding logic:** None. `INSERT OR IGNORE` only blocks exact duplicates.
- **Plausible consequence:** The database grows without limit. Every insert does extra index I/O. Over time inserts slow down, cache efficiency drops, and the disk can fill, after which writes fail with 500s.
- **Severity:** low-med. **Confidence:** med.
- **Assumptions to validate:** Real data volume and value sizes, and disk capacity.

**CR-5**
- **File / lines:** /tmp/aging-review-oaimrqsw/app/app.py, lines 40, 152-162 and 49-53
- **Fault and evidence:** The retrieve query (`WHERE phone IN (...) GROUP BY credit_card HAVING COUNT(DISTINCT phone) = ? ORDER BY credit_card`) can use `idx_assoc_phone(phone)`. That index does not cover `credit_card`, so each matching row needs a lookup back into the table, plus a temporary B-tree for GROUP BY/ORDER BY and for COUNT(DISTINCT). Every call also pays for a new connection, `PRAGMA foreign_keys=ON` (line 52), a statement prepare (the f-string changes with N, so the statement cache rarely helps across requests), and a `commit()` on a read (line 54).
- **Execution path:** POST /retrieve_cards → `db_conn()` → `execute(...).fetchall()` (line 162). All rows are pulled into memory before the 404/200 decision.
- **Affected state / resource:** CPU, temp storage and memory for sorting, threadpool time.
- **Triggering conditions:** Popular phones linked to many cards, many phones per request, or a high request rate.
- **Existing bounding logic:** Phone dedupe (lines 113-118) only. No LIMIT and no result-size cap, and `fetchall` loads the full result.
- **Plausible consequence:** Retrieve latency grows with how many cards the requested phones fan out to, rather than with the size of the result. Under load, threadpool slots are held longer and connection-setup overhead adds up.
- **Severity:** low. **Confidence:** med.
- **Assumptions to validate:** The SQLite query plan (EXPLAIN QUERY PLAN), the cardinality distribution, and the request rate.

**CR-6**
- **File / lines:** /tmp/aging-review-oaimrqsw/app/app.py, lines 70-76, 94-100, 106-121 and 155-157
- **Fault and evidence:** Cards and phones are normalized differently. `normalize_card` (line 72) removes spaces and hyphens. `normalize_phone` (line 76) only calls `strip()`, so `"555-1234"`, `"555 1234"` and `"5551234"` are stored as separate phones (line 133). The retrieval dedupe (lines 113-118) and `HAVING COUNT(DISTINCT phone) = ?` (line 157) compare exact strings, so the same phone written in different formats counts as different phones.
- **Execution path:** associate_card stores the raw, stripped phone. retrieve_cards matches with `IN` and counts DISTINCT on that raw form.
- **Affected state / resource:** Correctness of the association data and of the "associated with all given phones" intersection.
- **Triggering conditions:** Clients send the same phone in different formats across calls, or within one retrieve request.
- **Existing logic:** Only whitespace trimming, which does not make phone formats consistent.
- **Plausible consequence:** Wrong 404s, or cards missing from results. A card linked to "555-1234" is not returned for "5551234". A request listing both forms of one phone needs two distinct stored matches and returns nothing. Duplicate logical associations build up.
- **Severity:** low-med. **Confidence:** med.
- **Assumptions to validate:** Whether clients' phone formats actually vary, and whether exact-string phone semantics are what the spec intends.

---

# Appendix B — Phase-1 handoff: fastapi-developer (FA-*)

**FA-1**
- File and lines: `/tmp/aging-review-oaimrqsw/app/app.py`, lines 103-121 and 148-162.
- Fault: `phone_numbers` has `min_length=1` but no upper bound (line 104). The validator dedupes the list (lines 112-121) and never caps it. Line 150 builds `",".join(["?"] * len(phones))`, and line 162 binds `(*phones, len(phones))`.
- Execution path: `POST /retrieve_cards` → `RetrieveCardsRequest` validation → `retrieve_cards` runs in the threadpool → `conn.execute(query, params)`.
- Affected state or resource: the SQLite bound-parameter limit, the request thread, and the per-request connection.
- Triggering conditions: a request with more distinct phone strings than SQLite's `SQLITE_MAX_VARIABLE_NUMBER`. That limit is 999 on older builds and 32766 on newer ones.
- Existing cleanup or bounding logic: `db_conn` closes the connection in `finally` (line 56), so there is no leak. Nothing bounds the list length. The `RequestValidationError` handler only covers pydantic failures. An `sqlite3.OperationalError` ("too many SQL variables") is not handled.
- Runtime consequence: an unhandled exception gives HTTP 500 for large but otherwise valid requests. Large lists also produce very large SQL strings that are re-parsed on every call.
- Severity and confidence: med severity, high confidence.
- Assumptions to validate: the SQLite build's compile-time variable limit, and whether the server or proxy has a body-size limit that caps the list first.

**FA-2**
- File and lines: `/tmp/aging-review-oaimrqsw/app/app.py`, lines 47-56 and 128-140.
- Fault: `sqlite3.connect(DB_PATH, check_same_thread=False)` (line 49) uses the default 5-second busy timeout, and no `timeout=` is passed. Each request opens a new connection and writes with `INSERT OR IGNORE` (lines 131-137). The commit happens only after the `yield` (line 54).
- Execution path: concurrent `POST /associate_card` requests run in the AnyIO threadpool (about 40 threads by default). Each thread opens its own connection and takes the single SQLite writer lock.
- Affected state or resource: the SQLite database write lock, threadpool threads, and file handles.
- Triggering conditions: bursts of concurrent writes, or a long-held reader or writer such as a large `retrieve_cards` scan or a checkpoint. A writer that has to wait longer than 5 seconds fails.
- Existing cleanup or bounding logic: WAL mode (line 27) lets readers and a writer run together, but writers are still serialized. The only bounding is the implicit 5-second wait. `conn.close()` runs in `finally`, but there is no retry and no handling of `OperationalError`.
- Runtime consequence: `sqlite3.OperationalError: database is locked` gives 500 responses under write contention. Blocked threads can also exhaust the threadpool and delay every other sync endpoint.
- Severity and confidence: med severity, med confidence.
- Assumptions to validate: real write concurrency and how long each transaction stays open. The `commit()` on line 54 is brief, so the failure depends on load.

**FA-3**
- File and lines: `/tmp/aging-review-oaimrqsw/app/app.py`, lines 47-56, 130 and 161.
- Fault: there is no connection pool or app-scoped connection. Every request calls `sqlite3.connect`, runs `PRAGMA foreign_keys=ON`, and closes the connection. `retrieve_cards` also runs a commit on a read-only path (line 54).
- Execution path: every request to either endpoint goes through `db_conn()`.
- Affected state or resource: file descriptors for the main, WAL and SHM files, plus per-connection page and schema caches.
- Triggering conditions: sustained high request rates.
- Existing cleanup or bounding logic: `finally: conn.close()` guarantees cleanup. Nothing amortizes the connect cost, and the schema is re-read and the page cache is cold on every request.
- Runtime consequence: added latency per request and extra file-system calls, which reduces throughput. Closing the last connection on a WAL database can also trigger a checkpoint and WAL file deletion on each close when there is no concurrent connection, which adds I/O.
- Severity and confidence: low severity, med confidence.
- Assumptions to validate: profiling of connect and close cost, and the actual request rate.

**FA-4**
- File and lines: `/tmp/aging-review-oaimrqsw/app/app.py`, lines 152-167.
- Fault: the query has no `LIMIT` or pagination. `.fetchall()` materializes every matching card (line 162). The rows are then copied into a list and into a pydantic response model (line 167).
- Execution path: `POST /retrieve_cards` → query → `fetchall` → list comprehension → `RetrieveCardsResponse` → JSON serialization.
- Affected state or resource: worker memory, the threadpool thread, and response size.
- Triggering conditions: a phone number (or set of phone numbers) associated with a very large number of cards. Writes are unbounded and `associate_card` has no quota.
- Existing cleanup or bounding logic: none. The `ORDER BY credit_card` forces a sort over the whole grouped result set.
- Runtime consequence: memory spikes, a long-running synchronous request that holds a thread and a read snapshot, and slow responses. A long-held read snapshot can block WAL checkpoint progress and let the WAL file grow.
- Severity and confidence: low severity, med confidence.
- Assumptions to validate: the data distribution and the expected cardinality per phone.

**FA-5**
- File and lines: `/tmp/aging-review-oaimrqsw/app/app.py`, lines 12, 24-44, 49 and 59-61.
- Fault: `DB_PATH = "db.sqlite3"` is relative to the process's current directory. `init_db` runs in an `@app.on_event("startup")` hook (a deprecated mechanism, not a `lifespan`). It runs `PRAGMA journal_mode=WAL` and the DDL with no busy handling beyond the default timeout.
- Execution path: when the server starts (including with multiple workers), each process runs `init_db` against the same file at the same time.
- Affected state or resource: the database file and its schema and journal-mode state.
- Triggering conditions: multi-worker or multi-process startup, which races on the journal-mode switch and the DDL. Also a working directory that differs between runs, or one that is read-only.
- Existing cleanup or bounding logic: `IF NOT EXISTS` makes the DDL idempotent, and the connection is closed in `finally`. A failed `init_db` aborts startup. No retry is configured for concurrent starters, and the default 5-second busy timeout is the only protection.
- Runtime consequence: a worker can fail to start with `database is locked`. If the cwd differs, the service silently creates a new empty database and returns 404 for existing data.
- Severity and confidence: low severity, low confidence.
- Assumptions to validate: the deployment topology (number of workers) and cwd. `uvicorn.run(app, ...)` in `__main__` (line 171) runs a single process, so the race only applies if it is launched another way.

**FA-6**
- File and lines: `/tmp/aging-review-oaimrqsw/app/app.py`, lines 70-76, 83-92, 94-100 and 128-137.
- Fault: the stored key is not canonical. `str.isdigit()` (line 90) accepts non-ASCII digits such as superscripts and Arabic-Indic digits. Phones are only stripped (line 76), so `"+1 555"` and `"+1555"` are different keys. The UNIQUE constraint on `(credit_card, phone)` (line 36) then treats equivalent values as distinct.
- Execution path: `POST /associate_card` → validators → `INSERT OR IGNORE` with the normalized strings. `POST /retrieve_cards` → exact `IN` match.
- Affected state or resource: the `associations` table contents and the correctness of query results.
- Triggering conditions: any input that uses equivalent but differently formatted phone strings, or Unicode digit variants.
- Existing cleanup or bounding logic: the UNIQUE constraint and `INSERT OR IGNORE` dedupe only exact string matches. Validation checks only the card, not the phone format.
- Runtime consequence: duplicate or unmatched associations. `retrieve_cards` can return 404 or an incomplete card set when the same phone was stored in a different format. Cards with non-ASCII digits can be stored as separate entries.
- Severity and confidence: low severity, med confidence.
- Assumptions to validate: the intended phone format contract and how clients actually send phone numbers.