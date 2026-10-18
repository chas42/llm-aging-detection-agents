**Phase 2 adjudication: performance and software aging.** All conclusions are static-analysis hypotheses and need runtime confirmation. The only file reviewed was /tmp/aging-review-ddjhs9ob/app/app.py.

---

**Consolidated finding 1: Unbounded phone list in /retrieve_cards (CR-1 + FA-1)**
- Static validity: confirmed. Line 104 sets only `min_length=1`. Lines 150 and 162 create one bound parameter per unique phone, plus one for the count. Nothing bounds the list size.
- Merged from CR-1 and FA-1.
- Performance relevance: conditional.
- Affected resource: the request body and SQL text (CPU and memory), and a threadpool worker.
- Trigger: a request with a very large list of distinct phone strings.
  - Above the SQLite variable limit (999 on older builds, 32766 on newer ones), `OperationalError` is unhandled and the response is HTTP 500. The `finally` at line 56 closes the connection, so nothing leaks.
  - Below the limit, SQL build, parse and plan cost grows linearly with list size.
- Direct code evidence: the lack of any cap, the f-string SQL, and the unhandled exception path.
- Assumptions: the linked SQLite limit, and any body-size limit set by a proxy or server.
- Mechanism class: transient or repeated overhead.
- Aging relevance: non-aging performance fault. Each request is independent and its resources are released afterwards.
- Severity: low-med. Confidence: high for the code fact, medium for the performance impact.

**Consolidated finding 2: Write contention and threadpool occupancy (CR-2 + FA-2, with CR-5 partly merged)**
- Static validity: qualified.
  - Confirmed in the code: per-request connections (line 49), the default 5 s busy timeout, an implicit write transaction from `INSERT OR IGNORE`, the commit at line 54, no handling of `OperationalError`, and synchronous handlers.
  - Not shown in the code: any real write concurrency. A single-row insert holds the lock only briefly.
- Merged from CR-2 and FA-2, plus the per-request connect overhead from CR-5.
- Performance relevance: conditional.
- Affected resource: the SQLite writer lock and threadpool threads, which are finite.
- Trigger: bursts of concurrent writes, slow fsync, or several processes sharing the DB file.
  - Blocked writers occupy threads for up to 5 s, then return HTTP 500.
  - This effect is transient. It resolves when the burst ends.
- Existing bounding: WAL mode lets readers run alongside a writer. The timeout bounds the wait. The pool is about 40 threads (assumed from the framework default, not shown in the code).
- Mechanism class: finite-resource exhaustion, transient and load-dependent. It is not cumulative.
- Aging relevance: non-aging performance fault. It has a repeatable trigger and a finite resource, but no persistent accumulation. The pool drains once the load drops.
- Severity: med. Confidence: med.
- CR-5 verdict: fixed per-request overhead only. The connection is always closed. The extra PRAGMA is trivial. Severity is low, and it is a non-aging performance fault.

**Consolidated finding 3: Unbounded result set and no retention policy (FA-4 substantive part + CR-4 growth part)**
- Static validity: qualified.
  - Confirmed in the code: `fetchall()` has no LIMIT. Rows are never deleted. There is a redundant index: `UNIQUE(credit_card, phone)` already leads with `credit_card`, and `idx_assoc_card` duplicates it.
  - Overstated by CR-4: the claim that the extra index meaningfully lengthens the lock hold time. Nothing in the code supports that.
- Merged from FA-4 and CR-4.
- Performance relevance: conditional.
- Affected resource: database and index size, and per-request memory for large result sets.
- Trigger:
  - Continued insertion of distinct pairs makes the database and indexes grow without any deletion or expiry.
  - A phone with very many cards produces a large response.
- Existing bounding: none for retention or result size. The UNIQUE constraint only blocks exact duplicate pairs.
- Mechanism class: cumulative resource retention. This is the data growth the service is designed for. Retrieval cost grows with per-phone cardinality.
- Aging relevance: conditionally plausible aging mechanism. All four elements are present in the code: a repeatable trigger, accumulation, no cleanup, and a long-running context. The degradation is likely slow and depends on the insert rate and data distribution.
- The Response-parameter part of FA-4 has no performance impact.
- Severity: low. Confidence: med.

**Consolidated finding 4: WAL growth under continuous overlapping connections (CR-3)**
- Static validity: qualified.
  - Confirmed in the code: WAL mode is set (line 27), there is no checkpoint configuration, and connections are per-request.
  - Assumed: that readers are always present. Default passive autocheckpoint, and WAL reset when no reader pins old frames, are SQLite behavior outside this code. Short transactions here make unbounded WAL growth unlikely unless a reader is held open continuously.
- Performance relevance: conditional.
- Affected resource: WAL file size, the shm index, and read cost.
- Trigger: sustained overlapping connections that prevent the WAL from resetting.
- Existing bounding: the autocheckpoint of about 1000 pages and the checkpoint on last close, both from SQLite defaults.
- Mechanism class: cumulative resource retention, speculative.
- Aging relevance: conditionally plausible aging mechanism, weakly. The trigger and accumulation depend on a runtime condition the code does not establish.
- Severity: low. Confidence: low.

**Consolidated finding 5: Unbounded input string length (FA-5)**
- Static validity: qualified. No maximum length exists on `credit_card` or `phone`, and the `normalize_card` loop (line 72) is O(n). The impact is limited by the server's body-size limit, which is unknown.
- Performance relevance: conditional.
- Affected resource: request CPU, plus database and index size per row.
- Trigger: oversized string payloads.
- Existing bounding: none in the app.
- Mechanism class: transient overhead per request, plus storage bloat from each large row that is inserted.
- Aging relevance: non-aging performance fault. Retained bloat is only a variant of finding 3.
- Severity: low. Confidence: low.

**Consolidated finding 6: FA-3, startup lock contention in a multi-worker deployment**
- Static validity: qualified. The code only shows a single-process path (the `__main__` block).
- Performance relevance: none to conditional. This is a startup-time failure, not a runtime degradation.
- Mechanism class: no performance impact.
- Aging relevance: unrelated to performance.
- Severity: low. Confidence: low.

**Rejected finding**
- CR-6 (`isdigit()` accepts Unicode digits): the code fact is confirmed (line 90). The consequence is correctness and data integrity, with no runtime performance effect.
- Performance relevance: none.
- Mechanism class: no performance impact.
- Aging relevance: unrelated to performance.
- Rejected as a performance finding.

---

**SELF-SOURCED — UNVALIDATED**

None.

---

# Appendix: Raw Phase 1 handoffs

## Handoff A: code-reviewer (CR-*)

**CR-1**
- **File / lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 104, 106-121, 150-162
- **Fault and evidence:** `phone_numbers: List[str] = Field(..., description="Phone numbers", min_length=1)` (line 104) has no `max_length`. The validator (lines 106-121) removes duplicates but sets no upper limit. Lines 150-162 then build one bound parameter per unique phone, plus one more for the count: `placeholders = ",".join(["?"] * len(phones))` (line 150), `WHERE phone IN ({placeholders})` (line 155), `HAVING COUNT(DISTINCT phone) = ?` (line 157), and `conn.execute(query, (*phones, len(phones)))` (line 162).
- **Execution path:** POST /retrieve_cards → pydantic validation (`validate_phones`) → `retrieve_cards` → f-string query is built → `db_conn()` → `conn.execute(...).fetchall()`.
- **Affected state or resource:** The SQLite statement's bound-parameter count, the size of the SQL text that has to be parsed, process memory for the request body and lists, and a worker thread.
- **Triggering conditions:** A request whose unique phone count + 1 is more than SQLITE_MAX_VARIABLE_NUMBER (999 in SQLite builds before 3.32, 32766 in later builds). Large lists below that limit still cost O(n) to parse and plan on every request.
- **Existing bounding logic and why it falls short:** Only a minimum length (1) and de-duplication exist. Nothing limits list length, request body size, or parameter count.
- **Plausible runtime consequence:** `sqlite3.OperationalError: too many SQL variables` is not handled and returns HTTP 500. Below the limit, very large lists cause slow statement compilation, a wide index scan with a GROUP BY temp B-tree, and high memory use per request. This ties up threadpool workers.
- **Severity / confidence:** med / high
- **Needs runtime validation:** The SQLITE_MAX_VARIABLE_NUMBER of the linked SQLite library, and any request-size limits set by a proxy or server in front of the app.

**CR-2**
- **File / lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 47-56, 128-140, 148-162
- **Fault and evidence:** Every request opens its own connection with the default busy timeout: `conn = sqlite3.connect(DB_PATH, check_same_thread=False)` (line 49). It commits after the `yield` (line 54). Both handlers are synchronous `def` functions (lines 129 and 148), so they run in the AnyIO threadpool. `associate_card` takes SQLite's single database-wide write lock through `INSERT OR IGNORE` (lines 131-137). There is no retry and no handling for `OperationalError` around the `execute` or `commit` calls.
- **Execution path:** Concurrent POST /associate_card → threadpool worker → `db_conn()` → `INSERT` (implicitly begins a write transaction) → `conn.commit()` at line 54 → `close()` at line 56.
- **Affected state or resource:** The SQLite write lock, the default 5-second busy timeout, and the threadpool's worker threads (40 by default in AnyIO).
- **Triggering conditions:** Many concurrent writers, a slow disk or fsync, or WAL checkpoints running alongside writes (see CR-3).
- **Existing lifecycle logic and why it falls short:** WAL mode (line 27) lets readers run alongside a writer, but writes are still serialized. Blocked writers spin for up to 5 seconds while holding a threadpool slot. The `finally: conn.close()` releases the connection, but nothing bounds how many requests can queue for the lock.
- **Plausible runtime consequence:** `sqlite3.OperationalError: database is locked` returns HTTP 500 under write bursts. Threads stuck in busy-wait fill the threadpool, which also delays /retrieve_cards. Latency rises sharply and throughput falls as load grows.
- **Severity / confidence:** med / med
- **Needs runtime validation:** The real write concurrency, disk fsync latency, the AnyIO threadpool size, and how often lock errors occur under the target load.

**CR-3**
- **File / lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 27, 47-56
- **Fault and evidence:** `PRAGMA journal_mode=WAL;` (line 27) makes the database persistently WAL-mode. Every request then opens and closes its own connection (lines 49 and 56). Nothing configures checkpointing: there is no `wal_autocheckpoint` and no explicit `wal_checkpoint`. Only the default passive autocheckpoint (about 1000 pages) runs.
- **Execution path:** Each request's `db_conn()` connects, runs statements, commits, and closes. The automatic passive checkpoint runs at commit time, and SQLite also tries a checkpoint and WAL cleanup when the last connection closes.
- **Affected state or resource:** The size of `db.sqlite3-wal` on disk, the `-shm` index, and read performance (readers have to search a larger WAL).
- **Triggering conditions:** Sustained traffic where some read or write connection is always open (overlapping requests from the threadpool), so a passive checkpoint can never reset the WAL to its start.
- **Existing lifecycle logic and why it falls short:** Passive autocheckpoint copies frames back to the database but cannot reset or truncate the WAL while readers hold older snapshots. The cleanup on last connection close only happens when concurrency drops to zero, which may never happen under continuous load.
- **Plausible runtime consequence:** The WAL file keeps growing over long uptimes. Reads get slower as the WAL index grows, disk use climbs, and checkpoints become large and occasional, causing latency spikes. This is an aging pattern.
- **Severity / confidence:** med / low
- **Needs runtime validation:** Monitoring of WAL file size over time under production-like overlapping traffic, and whether idle gaps occur often enough to let the WAL reset.

**CR-4**
- **File / lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 31-41, 131-137
- **Fault and evidence:** `UNIQUE(credit_card, phone)` (line 36) already creates an automatic index whose leading column is `credit_card`. Line 41 adds a second index on the same leading column: `CREATE INDEX IF NOT EXISTS idx_assoc_card ON associations(credit_card);`. Together with `idx_assoc_phone` (line 40), the rowid table, and `AUTOINCREMENT` (line 32), which updates `sqlite_sequence`, each successful `INSERT OR IGNORE` (lines 133-134) writes to four B-trees. Rows are never deleted or expired.
- **Execution path:** POST /associate_card → `INSERT OR IGNORE` → probe the unique index → insert into the table, three indexes, and `sqlite_sequence`.
- **Affected state or resource:** Database file size, the number of dirty pages per transaction, the amount of WAL written, and how long the write lock is held.
- **Triggering conditions:** A steady stream of distinct (card, phone) pairs over the life of the service.
- **Existing bounding logic and why it falls short:** The UNIQUE constraint only blocks exact duplicates. There is no retention policy or cleanup, and the redundant index is maintained on every insert.
- **Plausible runtime consequence:** Extra write amplification lengthens the time the write lock is held, which worsens CR-2 and adds WAL volume for CR-3. The table and indexes grow without limit, so /retrieve_cards on frequently used phones has to aggregate more rows over time.
- **Severity / confidence:** low / high
- **Needs runtime validation:** The insert rate and table growth in production, and how much measurable per-insert cost the redundant index adds.

**CR-5**
- **File / lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 47-56, 130, 161
- **Fault and evidence:** `db_conn()` creates a brand-new connection on every request with `sqlite3.connect(DB_PATH, check_same_thread=False)` (line 49). It runs an extra `conn.execute("PRAGMA foreign_keys=ON;")` (line 52) even though the schema has no foreign keys, and closes the connection afterwards (line 56). It is called from both handlers (lines 130 and 161), and connections are never pooled or reused.
- **Execution path:** Every request to either endpoint → open the file, read the schema, attach the WAL and shared-memory mapping → PRAGMA → query → commit → close (which may trigger checkpoint or WAL-cleanup work).
- **Affected state or resource:** File descriptors, CPU spent on connection setup and schema parsing, the page cache (each new connection starts with an empty cache), and the cost of closing.
- **Triggering conditions:** A high request rate.
- **Existing lifecycle logic and why it falls short:** `finally: conn.close()` reliably releases each connection, so nothing leaks. But the per-request setup and teardown, the cold cache, and the useless PRAGMA add fixed overhead to every call.
- **Plausible runtime consequence:** Higher per-request latency and CPU use, and lower peak throughput. Closing the last connection can trigger checkpoint work during a request.
- **Severity / confidence:** low / med
- **Needs runtime validation:** Profiling the time spent in connect and close compared with the query itself at the expected request rate.

**CR-6**
- **File / lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 70-72, 83-92
- **Fault and evidence:** `if not v.isdigit():` (line 90) is the only content check after `normalize_card` (line 72), which removes only `" "` and `"-"`. `str.isdigit()` returns True for non-ASCII Unicode digits, such as Arabic-Indic `"١٢٣"`, fullwidth `"１２３"`, and superscript `"²"`. These strings pass validation and are stored exactly as given.
- **Execution path:** POST /associate_card → `validate_card` → `INSERT OR IGNORE` with the non-ASCII card string. Later, POST /retrieve_cards returns that string as-is.
- **Affected state or resource:** Integrity of the `credit_card` column and of the UNIQUE(credit_card, phone) de-duplication.
- **Triggering conditions:** A client sends card numbers containing Unicode digit characters, either fullwidth or other numeral systems.
- **Existing bounding logic and why it falls short:** The `isdigit()` check is meant to enforce digits only, but it accepts the whole Unicode digit category, and there is no ASCII or NFKC normalization.
- **Plausible runtime consequence:** The same logical card can be stored as several distinct rows (ASCII and fullwidth forms), which breaks uniqueness. Retrieval returns non-ASCII card strings or groups the same card under separate keys, giving incorrect /retrieve_cards results.
- **Severity / confidence:** low / high
- **Needs runtime validation:** Whether real clients ever send non-ASCII digits, and whether systems downstream of the response treat such values as equivalent.

## Handoff B: fastapi-developer (FA-*)

**FA-1**
- **File and lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 104, 148-162. The model at line 104 is `phone_numbers: List[str] = Field(..., min_length=1)`.
- **Fault and evidence:**
  - Line 104 sets only a minimum length and no maximum on the list.
  - Line 150 builds `placeholders = ",".join(["?"] * len(phones))`.
  - Line 162 runs `conn.execute(query, (*phones, len(phones)))`.
  - The number of bound parameters is `len(unique phones) + 1` and is not capped. SQLite has a limit on bound variables (`SQLITE_MAX_VARIABLE_NUMBER`). It is 999 on older builds and 32766 on newer ones.
- **Execution path:** POST /retrieve_cards -&gt; validator deduplicates the list -&gt; `retrieve_cards` builds the SQL -&gt; `conn.execute` raises `sqlite3.OperationalError` ("too many SQL variables").
- **Affected state or resource:** the request thread and the per-request connection. The exception passes through `db_conn`'s `finally`, so the connection is closed.
- **Triggering conditions:** a request with more distinct phone strings than the SQLite variable limit.
- **Existing cleanup or bounding:** `db_conn` closes the connection. Nothing bounds the list size, and no handler maps `sqlite3.Error`. The validator catches only empty items, so the cleanup is not enough to prevent the failure.
- **Runtime consequence:** an unhandled exception gives HTTP 500 instead of the 400 or 404 the service otherwise returns. Before the failure, a large body is fully parsed, deduplicated and formatted into SQL, which costs CPU and memory per request.
- **Severity / confidence:** low-med / high.
- **Assumptions to validate:** the linked SQLite's variable limit, and whether any upstream proxy or server body-size limit makes the threshold unreachable.

**FA-2**
- **File and lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 47-56 and 128-140.
- **Fault and evidence:**
  - `db_conn` opens a fresh `sqlite3.connect(DB_PATH, check_same_thread=False)` on every call, with the default busy timeout of 5 seconds.
  - There is no `BEGIN IMMEDIATE`. The connection runs in the default implicit-transaction mode.
  - The `INSERT OR IGNORE` at line 131 takes the write lock, which is held until `conn.commit()` at line 54.
  - Sync endpoints (`def`) run on AnyIO's default threadpool, which has 40 threads, so up to 40 writers can contend for the single SQLite writer.
- **Execution path:** concurrent POST /associate_card requests -&gt; threadpool threads -&gt; each opens a connection and inserts. The write lock is held until the commit after the `with` body, and commit is followed by `close`.
- **Affected state or resource:** the SQLite database file, the WAL write lock, and threadpool threads blocked in the busy-wait.
- **Triggering conditions:** bursts of writes, or long-running reads and writes from other processes such as multiple uvicorn workers. Waiting beyond 5 seconds raises `sqlite3.OperationalError: database is locked`.
- **Existing cleanup or bounding:** WAL mode is enabled in `init_db`, which lets readers run alongside a writer. The 5-second default timeout bounds the wait. It does not serialize writers or handle the error, so it is insufficient under sustained write load.
- **Runtime consequence:** blocked threads reduce the threadpool capacity available to all sync endpoints, including reads. Timed-out writes return 500. Each request also pays connection setup and a `PRAGMA` round trip.
- **Severity / confidence:** med / med.
- **Assumptions to validate:** actual write concurrency, the number of workers, and disk fsync latency. Short single-row inserts may rarely exceed 5 seconds.

**FA-3**
- **File and lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 24-44 and 59-61.
- **Fault and evidence:**
  - `init_db` runs `PRAGMA journal_mode=WAL` on startup (line 27).
  - `db_conn` never sets `journal_mode` or a busy timeout. WAL is persistent in the database file, so it only takes effect if `init_db` succeeded.
  - With several workers (`uvicorn --workers N` or a process manager importing `app`, since `__main__` runs a single process), each worker's startup runs `init_db` concurrently.
  - Switching to WAL requires an exclusive lock, and `sqlite3.connect` there uses the default 5-second timeout.
- **Execution path:** the startup event runs in every worker process at the same time -&gt; `PRAGMA journal_mode=WAL` and the DDL contend for the lock.
- **Affected state or resource:** the db file, and the application startup.
- **Triggering conditions:** multi-worker or multi-process deployment on a fresh database, or startup while other connections are active.
- **Existing cleanup or bounding:** `CREATE ... IF NOT EXISTS` makes the DDL idempotent. It does not prevent a lock error, and a startup exception aborts that worker.
- **Runtime consequence:** a worker may fail to start with "database is locked". The startup hook uses the deprecated `on_event`, which works in current FastAPI.
- **Severity / confidence:** low / low.
- **Assumptions to validate:** the deployment topology. A single process is the only path shown in the code.

**FA-4**
- **File and lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 128-140 and 161-167.
- **Fault and evidence:**
  - `associate_card` takes a `Response` parameter, sets `response.status_code` (line 138), and then returns a new `Response` (line 140). The parameter's changes are discarded because a returned `Response` bypasses them.
  - `retrieve_cards` declares `response_model=RetrieveCardsResponse` but returns a `JSONResponse` for 404. This bypasses validation and is harmless.
  - The substantive concern is the unbounded result set. `fetchall()` at line 162 materializes every matching card into memory, and the response is built as a full list.
- **Execution path:** POST /retrieve_cards with a phone set matching many cards -&gt; `fetchall` -&gt; list comprehension -&gt; Pydantic model -&gt; serialization.
- **Affected state or resource:** per-request memory, and a threadpool thread held while the result is processed.
- **Triggering conditions:** a single phone number (or a small set) associated with a very large number of cards.
- **Existing cleanup or bounding:** none. There is no `LIMIT` or pagination, and the list is materialized three times: rows, the list of strings, and the serialized JSON.
- **Runtime consequence:** large memory spikes and long request times that occupy threadpool threads, as the table grows.
- **Severity / confidence:** low / med.
- **Assumptions to validate:** the real data distribution and per-phone cardinality.

**FA-5**
- **File and lines:** /tmp/aging-review-ddjhs9ob/app/app.py, lines 70-76, 83-100, and 152-159.
- **Fault and evidence:**
  - There is no maximum length on `credit_card` or `phone`.
  - `normalize_card` iterates character by character in Python (line 72), and `isdigit()` (line 90) also accepts non-ASCII digit characters such as superscripts.
  - Arbitrarily long strings are stored in the `associations` table and in both indexes.
- **Execution path:** POST /associate_card with a very large string -&gt; a Python-level O(n) loop in the validator -&gt; a large row insert plus index entries.
- **Affected state or resource:** CPU in the request thread, the db file, and the index sizes.
- **Triggering conditions:** oversized string payloads. The body size limit is whatever the server allows.
- **Existing cleanup or bounding:** none in the application.
- **Runtime consequence:** CPU cost per request, database and index bloat, and slower lookups in `retrieve_cards`, which uses `IN` and `GROUP BY` over these columns.
- **Severity / confidence:** low / low-med.
- **Assumptions to validate:** the server or proxy body limits, and whether clients ever send such inputs.