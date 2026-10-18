## 1. Performance-engineer final consolidated report (verbatim)

**Static-analysis report (hypotheses requiring runtime confirmation). Code cited: /tmp/aging-review-wjovewb6/app/app.py**

**Consolidated finding 1: Unbounded `phone_numbers` list (CR-1 + FA-2, merged)**
- Static validity: confirmed. The list has `min_length=1` but no maximum (line 104). Lines 150 and 162 build N+1 bound parameters.
- Performance relevance: conditional.
- Resource and trigger: request-sized CPU and memory, plus SQLite's variable limit. The trigger is one request with a very large list. The limit is a build-dependent assumption (999 before SQLite 3.32, 32766 from 3.32).
- Cleanup and bounds: deduplication (lines 113-118) removes repeats only. Nothing caps the list size. There is no `OperationalError` handler. Both reviewers inferred the resulting 500 from this absence, which I did not verify. Any body-size limit would sit outside this code.
- Direct evidence: the unbounded list and the per-phone placeholders.
- Assumptions: the SQLite version, any proxy body limit, and the claimed 500 response.
- Mechanism: transient or repeated overhead, with a possible failure past the limit.
- Aging relevance: non-aging performance fault. The cost is released when the request ends, so nothing accumulates.
- Severity: low-med. Confidence: high on the code, med on the impact.

**Consolidated finding 2: SQLite write-lock contention and threadpool occupancy (CR-2 + FA-1, merged; FA-5 contributes)**
- Static validity: qualified.
- Direct code evidence:
  - Each request opens its own connection (line 49) with the default 5 s timeout.
  - Both handlers are sync `def`, so they run in the bounded threadpool.
  - The commit is at line 54.
  - WAL mode is set at line 27.
  - There is no retry or error handling.
- Assumptions: concurrent writers, the threadpool size (about 40), and the fsync cost. FA-1's claim that a write overlaps a long read is weak in WAL mode, because readers do not block the writer.
- Performance relevance: conditional.
- Resource and trigger: the SQLite writer lock and threadpool slots. The trigger is sustained concurrent writes. A blocked thread waits up to 5 s and is then released.
- Mechanism: finite-resource exhaustion, transient under load. It is bounded by the timeout and releases when the burst ends.
- Aging relevance: non-aging performance fault. The trigger is load, not elapsed time, and there is no persistent accumulation.
- Severity: med. Confidence: med.

**Consolidated finding 3: Unbounded result set and table growth (CR-4 + FA-3, merged; CR-5 partly)**
- Static validity: confirmed.
- Direct code evidence:
  - The query has no `LIMIT`.
  - `fetchall()` loads all matching rows (line 162).
  - There is no delete or retention path anywhere in the file.
  - `GROUP BY` and `ORDER BY` add a sort over the matching rows.
- Resource and trigger: per-request memory and latency, plus database size. They grow with how many cards a queried phone has, so the data grows only through repeated `/associate_card` calls.
- Cleanup and bounds: none on table rows or result size. Per-request memory is freed after the response.
- Assumptions: the real distribution of cards per phone and the insert volume.
- Mechanism: cumulative resource retention for the table (persistent data growth). The per-request cost is transient.
- Aging relevance: conditionally plausible aging mechanism. It has a repeatable trigger (inserts), persistent accumulation (rows) and no cleanup. Whether it degrades latency depends on the workload skewing toward heavily shared phones. Lookups go through `idx_assoc_phone`, so unrelated queries should degrade only slowly.
- Severity: low-med. Confidence: med.

**Consolidated finding 4: WAL growth and checkpoint behavior (CR-3)**
- Static validity: qualified.
- Direct code evidence: WAL is set at line 27, and each request opens and closes a connection (lines 49 and 56). Only SQLite's automatic checkpointing applies.
- Assumption: continuously overlapping readers prevent the WAL from being reset. This depends on SQLite internals and traffic patterns, and it is not shown in the code. Each request holds its connection only briefly, and passive checkpoints reset the WAL whenever there is a gap with no active readers.
- Performance relevance: conditional.
- Resource and trigger: the `-wal` file size and read latency.
- Mechanism: potential cumulative retention, but only under sustained overlapping reads.
- Aging relevance: conditionally plausible aging mechanism, with a weak repeatable trigger. The 1000-page auto-checkpoint is a bound, and it is defeated only by a never-idle reader pattern.
- Severity: low. Confidence: low.

**Consolidated finding 5: Per-request connection churn and write amplification (CR-5 + FA-5, merged)**
- Static validity: qualified.
- Direct code evidence:
  - A new connection and PRAGMA run on every request.
  - `idx_assoc_card` (line 41) is redundant with the prefix of the `UNIQUE(credit_card, phone)` index.
  - Duplicate inserts still take the write lock and commit.
  - `AUTOINCREMENT` adds `sqlite_sequence` bookkeeping.
- Unsupported claims:
  - FA-5's "fsync per duplicate" is an assumption. `INSERT OR IGNORE` that changes nothing likely writes little.
  - CR-5's "very long digit strings" are unbounded in the code, but they are only a request-size effect.
- Performance relevance: conditional, a constant-factor overhead.
- Mechanism: transient or repeated overhead. The redundant index adds storage that grows with the table.
- Aging relevance: non-aging performance fault.
- Severity: low. Confidence: med.

**Consolidated finding 6: Startup, working directory and phone-format issues (CR-6 + FA-4 + FA-6)**
- Static validity: confirmed as code facts, but they are functional or deployment issues rather than performance issues.
- Direct code evidence: the relative `DB_PATH` (line 12), unhandled startup lock contention, and `strip()`-only phone normalization (line 76).
- FA-6: rejected as negligible. The extra `Response` objects are a trivial constant.
- Performance relevance: none for the phone-format and 201-status points. Startup contention is a failure mode, not a performance one.
- Mechanism: no performance impact.
- Aging relevance: unrelated to performance.
- Severity: low. Confidence: low-med.

**SELF-SOURCED — UNVALIDATED**
- None that I can support beyond the findings above.

---

## 2. Appendix: Raw Phase-1 handoffs (unedited)

### Handoff A: code-reviewer (CR-*)

**CR-1**
- **File / lines:** /tmp/aging-review-wjovewb6/app/app.py, lines 104, 106-121, 148-162
- **Fault and evidence:** The `phone_numbers` list has a minimum size (`min_length=1`, line 104) but no maximum. Line 150 builds one bound parameter per phone with `",".join(["?"] * len(phones))`. Line 162 binds `(*phones, len(phones))`, which is N+1 parameters. Line 109 normalizes every element and lines 113-118 deduplicate them, so the whole list is processed before the query runs.
- **Execution path:** POST /retrieve_cards → Pydantic `validate_phones` → `retrieve_cards` → f-string query (lines 152-159) → `conn.execute(...).fetchall()`.
- **Affected state or resource:** SQLite's bound-parameter limit, the SQL statement compile cost, worker memory and CPU.
- **Triggering conditions:** A request with more unique phones than the SQLite build's `SQLITE_MAX_VARIABLE_NUMBER` minus 1. That limit is 999 before SQLite 3.32 and 32766 from 3.32 on. Large lists below the limit still trigger the cost.
- **Existing bounding logic:** Deduplication (lines 113-118) only removes repeats. Nothing caps list size or element length. The code does not catch `sqlite3.OperationalError`.
- **Runtime consequence:** Past the limit, SQLite raises "too many SQL variables" and the client gets an unhandled 500 instead of the documented 400 or 404. Below the limit, large lists cause a large statement compile, a big IN-list scan and high per-request memory and CPU, all inside a threadpool worker.
- **Severity / confidence:** med / high
- **Assumptions to validate:** The SQLite version linked into the deployed Python, and whether a reverse proxy or server limits request body size.

**CR-2**
- **File / lines:** /tmp/aging-review-wjovewb6/app/app.py, lines 47-56, 128-137
- **Fault and evidence:** Every request opens a new connection with `sqlite3.connect(DB_PATH, check_same_thread=False)` (line 49), which uses the default 5-second busy timeout. Writes run `INSERT OR IGNORE` and then `conn.commit()` (line 54). Both endpoints are plain `def` (lines 129, 148), so they run in Starlette's bounded anyio threadpool. The default is 40 tokens. No code handles `sqlite3.OperationalError` ("database is locked").
- **Execution path:** Concurrent POST /associate_card → threadpool worker → `db_conn()` → INSERT → commit, which needs the single WAL write lock.
- **Affected state or resource:** The SQLite writer lock and the threadpool worker slots.
- **Triggering conditions:** Sustained concurrent writes, or one slow writer such as a checkpoint (see CR-3). Waiting writers block for up to 5 seconds in `sqlite3`'s busy handler.
- **Existing lifecycle logic:** WAL mode (line 27) lets readers run alongside the writer but still allows only one writer. The busy timeout is never raised and there is no retry. The 5-second wait is not enough under write bursts.
- **Runtime consequence:** Writers serialize. Blocked threads hold threadpool tokens for up to 5 seconds each, which also stalls unrelated /retrieve_cards requests waiting for a slot. Past the timeout, requests fail with unhandled 500 "database is locked". Under load, tail latency rises and throughput collapses.
- **Severity / confidence:** med / med
- **Assumptions to validate:** The real write concurrency, the threadpool size, and whether more than one uvicorn worker process shares the file.

**CR-3**
- **File / lines:** /tmp/aging-review-wjovewb6/app/app.py, lines 27, 49, 55-56
- **Fault and evidence:** WAL mode is set permanently on the file (`PRAGMA journal_mode=WAL;`, line 27). Each request opens a new connection (line 49) and closes it (line 56). Checkpointing is left to SQLite's automatic behavior:
  - A passive auto-checkpoint runs at about 1000 pages, and it cannot pass pages still in use by an open reader.
  - When the last connection closes, SQLite checkpoints and deletes the WAL file on that request's thread.
- **Execution path:** Any request → `db_conn()` → `conn.close()` (line 56). Interleaved /retrieve_cards readers and /associate_card writers.
- **Affected state or resource:** The `db.sqlite3-wal` file size, disk space, and read latency (readers scan a larger WAL index).
- **Triggering conditions:**
  - Steady overlapping traffic means there is always at least one open reader, so passive checkpoints never reach the end of the WAL.
  - At idle, whichever request closes the last connection pays for the full checkpoint.
- **Existing lifecycle logic:** Only the implicit auto-checkpoint and the checkpoint on last close. There is no periodic or forced checkpoint. This is not enough under continuous concurrent load.
- **Runtime consequence:** The WAL file can keep growing over long uptimes and reads slow down progressively (an aging effect). Requests that happen to close the last connection see latency spikes.
- **Severity / confidence:** low-med / low-med
- **Assumptions to validate:** The real traffic overlap pattern and the WAL file size over time. This needs file-size monitoring under sustained load.

**CR-4**
- **File / lines:** /tmp/aging-review-wjovewb6/app/app.py, lines 152-167
- **Fault and evidence:** The query has no `LIMIT` (lines 152-159). `.fetchall()` (line 162) loads every matching row into memory. Line 167 then builds a Python list, a Pydantic model, and a JSON-serialized response from it. `GROUP BY credit_card ... ORDER BY credit_card` (lines 156-158) needs a temporary B-tree sort over all rows matching the IN filter. The filter goes through `idx_assoc_phone` (line 40), which is not a covering index, so every matching row also costs a lookup into the table.
- **Execution path:** POST /retrieve_cards with one phone, or a small set of phones, that is linked to many cards.
- **Affected state or resource:** Worker memory, CPU, and the SQLite temp store.
- **Triggering conditions:** As the `associations` table grows without limit (no retention or delete path exists), heavily shared phones match more and more rows.
- **Existing bounding logic:** None. There is no pagination or row cap, and nothing limits how many cards one phone can be linked to.
- **Runtime consequence:** Memory per request and latency both grow with data volume. Concurrent large requests multiply the effect across up to 40 threadpool workers.
- **Severity / confidence:** low-med / med
- **Assumptions to validate:** The real distribution of cards per phone and the table size over time.

**CR-5**
- **File / lines:** /tmp/aging-review-wjovewb6/app/app.py, lines 31-41, 79-100, 133-136
- **Fault and evidence:** The table already has `UNIQUE(credit_card, phone)` (line 36), which creates an automatic index that starts with `credit_card`. Line 41 also creates `idx_assoc_card ON associations(credit_card)`, which is redundant with it. Each insert therefore maintains three B-trees plus the table itself. Neither field has a length limit: `credit_card` is only checked with `isdigit()` (line 90) and `phone` only with `strip()` (line 76). There is no delete or retention path, so all data is kept forever.
- **Execution path:** Every POST /associate_card → INSERT (lines 133-136).
- **Affected state or resource:** Database file size, write amplification, and how long the write lock is held (which feeds CR-2).
- **Triggering conditions:** A high insert volume, or very long digit strings or phone strings in requests.
- **Existing bounding logic:** `INSERT OR IGNORE` only removes exact duplicate pairs. Nothing bounds field size or row count.
- **Runtime consequence:** The database grows without limit, each insert costs more I/O than needed and holds the write lock longer, and very large field values bloat the indexes.
- **Severity / confidence:** low / med
- **Assumptions to validate:** The real insert rate and the sizes of incoming payloads.

**CR-6**
- **File / lines:** /tmp/aging-review-wjovewb6/app/app.py, lines 12, 24-44, 59-61, 75-76, 128-140
- **Fault and evidence:**
  - `DB_PATH = "db.sqlite3"` (line 12) is a relative path, so where the database lives depends on the process's working directory at runtime.
  - `init_db` runs from `@app.on_event("startup")` (lines 59-61). If several workers start at once, each runs the `CREATE`/`PRAGMA journal_mode=WAL` statements on the shared file with the default timeout. A worker can fail its startup with "database is locked" and nothing handles it.
  - Phone normalization is only `strip()` (line 76). Formatting variants of the same number, such as "+1 555-0100" and "+15550100", are stored and matched as different phones. Lookups through the `COUNT(DISTINCT phone)` check (line 157) then miss existing associations and return 404.
  - `associate_card` returns 201 even when `INSERT OR IGNORE` changed nothing (lines 133, 140). Callers cannot tell a new association from a duplicate.
- **Execution path:** Process startup → `init_db`. POST /associate_card followed by POST /retrieve_cards with a different formatting of the same phone.
- **Affected state or resource:** Which database file is used, startup reliability, and how consistent lookups are.
- **Triggering conditions:**
  - The working directory differs between launches or deployments.
  - The app runs with multiple workers.
  - Clients format phone numbers inconsistently.
- **Existing logic:** `CREATE ... IF NOT EXISTS` makes schema creation safe to repeat but does not prevent lock errors. Phone normalization does not make formats canonical.
- **Runtime consequence:** Data can appear "lost" when a different working directory creates a new empty database, and every lookup then returns 404. Workers can fail at startup in multi-worker runs. Lookups return false negatives for phone numbers that are already stored.
- **Severity / confidence:** low-med / med for the phone-format mismatch and the working-directory dependence; low / low for the startup race.
- **Assumptions to validate:** The deployment's working directory and worker count, and whether client phone formats are consistent.

### Handoff B: fastapi-developer (FA-*)

FA-1
- File and lines: /tmp/aging-review-wjovewb6/app/app.py, lines 128-140 and 47-56
- Fault: `associate_card` is a sync `def` that does a write through a new SQLite connection on every request. `db_conn` calls `sqlite3.connect(DB_PATH, check_same_thread=False)` with no `timeout` argument (line 49), so the 5 s default applies. The commit happens in the context manager (line 54), after the `INSERT OR IGNORE` (lines 131-137).
- Execution path: POST /associate_card runs in the anyio threadpool, which has about 40 threads by default. Each request opens a connection, takes the SQLite write lock for the insert, commits, and closes the connection.
- Affected state or resource: the single SQLite write lock (WAL mode), the threadpool threads, and the per-request file descriptors and connections.
- Triggering conditions: many concurrent writes, or a write that overlaps a long `retrieve_cards` read.
- Existing cleanup, lifecycle, or bounding logic: the `finally` always closes the connection. The default 5 s busy timeout bounds each wait. Nothing bounds concurrency or reuses connections, so threads block on the lock and the pool can fill.
- Plausible runtime consequence: writers serialize on the lock. Under bursts, requests wait up to 5 s and then fail with `sqlite3.OperationalError: database is locked`, which returns a 500. Blocked threads hold the threadpool, which delays every other sync endpoint. Each request also pays connect, PRAGMA and close overhead.
- Severity and confidence: med severity, med confidence.
- Assumptions needing validation: real write concurrency, the disk's fsync latency, and whether WAL is actually active when the application connects.

FA-2
- File and lines: /tmp/aging-review-wjovewb6/app/app.py, lines 148-167
- Fault: `phone_numbers` has `min_length=1` (line 104) but no maximum. The query is built with one `?` placeholder per phone (lines 150-155), and the parameter tuple is `(*phones, len(phones))` (line 162).
- Execution path: POST /retrieve_cards parses the unbounded list, deduplicates it in the validator, builds the SQL string, then `execute(...).fetchall()` runs in a threadpool thread.
- Affected state or resource: the SQLite bound-variable limit (999 on older builds, 32766 on newer ones), CPU and memory for the SQL string and tuple, and the threadpool thread.
- Triggering conditions: a request with more unique phone numbers than the limit.
- Existing cleanup, lifecycle, or bounding logic: none on list size. Validation errors are converted to 400, but this overflow occurs after validation, inside `execute`.
- Plausible runtime consequence: `sqlite3.OperationalError` ("too many SQL variables") is raised as an unhandled exception and returns a 500. Very large lists cost CPU and memory in a worker thread. The `IN` list with `GROUP BY` and `COUNT(DISTINCT)` scans many index entries, so each request gets slower as the list grows.
- Severity and confidence: med severity, high confidence on the limit overflow.
- Assumptions needing validation: the linked SQLite version and its compile-time variable limit, and the server's request body size limits.

FA-3
- File and lines: /tmp/aging-review-wjovewb6/app/app.py, lines 152-162 and 164-167
- Fault: the query has no `LIMIT`. `fetchall()` materializes every matching card, and the response model is then built in memory with a list of all of them. A single phone can be associated with an unbounded number of cards, since the unique key is (card, phone).
- Execution path: POST /retrieve_cards runs `fetchall()` (line 162), builds a list comprehension (line 167), validates it through the Pydantic model, then FastAPI re-validates and serializes it through `response_model`.
- Affected state or resource: memory and CPU in the worker thread, and the response size.
- Triggering conditions: a popular phone with a large number of associated cards, or a table that has grown over time.
- Existing cleanup, lifecycle, or bounding logic: none. The table grows without limit because `associate_card` only inserts and nothing ever deletes.
- Plausible runtime consequence: latency and memory per request grow linearly with the table size. `ORDER BY credit_card` also needs a sort step after the `GROUP BY`. Several large responses at once can exhaust memory.
- Severity and confidence: low to med severity, med confidence.
- Assumptions needing validation: the real data distribution and the volume of associations per phone.

FA-4
- File and lines: /tmp/aging-review-wjovewb6/app/app.py, lines 24-44 and 59-61
- Fault: startup runs `init_db()`, which sets WAL mode and creates the schema, but there is no shutdown hook. `@app.on_event("startup")` is deprecated in favor of lifespan. `PRAGMA journal_mode=WAL` is persistent, but `foreign_keys` is per connection and is set again in `db_conn`. A relative `DB_PATH = "db.sqlite3"` (line 12) depends on the process working directory.
- Execution path: on startup the schema is created. If initialization fails, for example because the directory is unwritable or the file is locked by another worker, startup raises.
- Affected state or resource: the `db.sqlite3` file and the `-wal` and `-shm` files in the working directory.
- Triggering conditions: running multiple uvicorn workers or processes that start at the same time, or running from a different working directory.
- Existing cleanup, lifecycle, or bounding logic: `init_db` closes its connection in a `finally`. `CREATE ... IF NOT EXISTS` makes it idempotent. The `journal_mode` switch at startup takes a lock with the default 5 s timeout, which is generally enough but not guaranteed.
- Plausible runtime consequence: concurrent worker startup can hit "database is locked" during the WAL switch and crash a worker. Different working directories create separate databases, so data is split across them.
- Severity and confidence: low severity, low confidence.
- Assumptions needing validation: the deployment topology, specifically the worker count and the working directory.

FA-5
- File and lines: /tmp/aging-review-wjovewb6/app/app.py, lines 128-140
- Fault: `INSERT OR IGNORE` on a table with the `UNIQUE(credit_card, phone)` constraint (lines 31-37) returns 201 whether or not a row was inserted. The handler never checks `cursor.rowcount`. Its `AUTOINCREMENT` primary key also forces extra bookkeeping in `sqlite_sequence` on every insert. Each write also maintains two secondary indexes plus the unique index (lines 36, 40, 41).
- Execution path: every POST /associate_card does a write that updates three indexes, plus `sqlite_sequence` for any row that is actually inserted.
- Affected state or resource: write amplification on the single writer, and disk growth.
- Triggering conditions: sustained write load, or repeated duplicate posts.
- Existing cleanup, lifecycle, or bounding logic: none. Duplicate posts are ignored by the constraint, but they still take the write lock and a transaction commit.
- Plausible runtime consequence: duplicate or high-rate writes still serialize on the lock and cost an fsync each, which reduces write throughput. This compounds FA-1.
- Severity and confidence: low severity, low confidence.
- Assumptions needing validation: write throughput under load, the `synchronous` setting, and the disk's fsync cost.

FA-6
- File and lines: /tmp/aging-review-wjovewb6/app/app.py, lines 128-140 and 143-167
- Fault: the handlers return `Response` or `JSONResponse` objects directly (lines 140 and 165) on routes that declare `response_model`. `associate_card` also takes an injected `response` parameter that is then discarded, because a returned `Response` overrides it. This is a minor inefficiency rather than a functional fault. The 404 branch also does the full query work before returning an error.
- Execution path: every request builds an unused temporary response object, and the success path in `retrieve_cards` validates and serializes through the model.
- Affected state or resource: negligible CPU.
- Triggering conditions: every request.
- Existing cleanup, lifecycle, or bounding logic: not applicable.
- Plausible runtime consequence: negligible overhead. I include it only for completeness, and it is unlikely to matter.
- Severity and confidence: low severity, low confidence.
- Assumptions needing validation: profiling would be needed to show any measurable effect.