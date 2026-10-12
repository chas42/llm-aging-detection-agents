# Aging rule set: taxonomy, protocol and catalog

Static rules (Semgrep) that flag **candidate** code-level aging mechanisms in backend services.
A match is a *candidate*, never a confirmed aging mechanism: it enters the triage step, which
checks the four required elements (repeatable trigger, persistent accumulation or progressive
exhaustion, insufficient cleanup/bounding, plausible degradation over long-running execution)
and, if supported, the runtime validation pipeline.

## Taxonomy (where the rules come from)

The classes follow the software aging / aging-related bug (ARB) literature, which distinguishes
aging by the **resource that accumulates or is exhausted**, and the mechanism-class vocabulary
already used in the previous paper's AI adjudication step:

| class | mechanism (paper vocabulary) | resource | typical code-level cause |
|---|---|---|---|
| **C1 Memory / state retention** | cumulative resource retention | heap: in-process collections, caches | state added on a repeatable path and never evicted (no bound, no TTL) |
| **C2 Persistent storage growth** | cumulative resource retention | disk: DB rows, files, temp files | records/files created per request with no retention, rotation or cleanup |
| **C3 Logical resource leak** | finite-resource exhaustion | file descriptors, sockets, DB connections, processes | resource acquired without a guaranteed release on every path |
| **C4 Concurrency resource exhaustion** | finite-resource exhaustion | threads, worker slots, tasks, timers, listeners | unbounded creation, or blocking on an external dependency with no timeout |
| **C5 Unbounded work per request** | (amplifies C1/C2) | CPU/memory per call | reads whose cost grows with accumulated state (no LIMIT/pagination) |

Reference basis (to be cited in the paper; check exact bibliographic details when writing):
- M. Grottke, R. Matias, K. Trivedi, *The fundamentals of software aging*, WoSAR/ISSRE workshops 2008.
- D. Cotroneo, R. Natella, R. Pietrantuono, S. Russo, *A survey of software aging and rejuvenation
  studies*, ACM JETC, 2014.
- D. Cotroneo, M. Grottke, R. Natella, R. Pietrantuono, K. Trivedi, *Fault triggers in open-source
  software: an experience report*, ISSRE 2013 (aging-related bug classification).
- F. Machida et al., *Aging-related bugs in cloud computing software*, WoSAR 2012.

Numerical error accumulation (another ARB class) is **out of scope**: it is not expressible as
a reliable syntactic pattern in these service apps.

## Protocol (threat-to-validity mitigation)

1. Classes and rules are derived from the taxonomy above; each rule cites its class.
2. Every rule has synthetic test cases (`tests/<lang>/`) with positive (`ruleid:`) and negative
   (`ok:`) annotations, written **before** the rule set is run on the corpus.
3. The rule set is **frozen** (`VERSION`) before the first corpus run. Afterwards only bug fixes are
   allowed, each logged in the changelog below with its reason; no rule is added, removed or
   tuned because of what it did or did not find in the corpus.
4. Known limitation: the author of the rules had read the previous paper's report for the pilot
   app (Uptime FastAPI) before writing them, so the rule set is not blind to the corpus. This is
   reported as a threat to validity; the human-written open-source apps and the JS/Rust apps,
   whose reports were not read, act as a less-biased subset.

## Extension: file-level conditions

Several mechanisms are "X is added **and nothing in the file ever removes it**". Semgrep cannot
express a file-wide negative directly, so rules may declare in their metadata

```yaml
metadata:
  aging:
    unless_file_matches: '\b$X\.(pop|remove|clear)\b'   # regex; $X = the rule's captured metavariable
```

`static/run_tools.py` drops a match when the file content matches that regex (metavariables are
substituted with their escaped captured values). Tests run through the same filter.

## Catalog

See the rule files (`python.yaml`, `javascript.yaml`, `rust.yaml`) for patterns and messages.

| id | class | language | flags |
|---|---|---|---|
| aging-py-global-collection-growth | C1 | Python | module-level dict/list/set/deque mutated inside a function, with no removal in the file |
| aging-py-unbounded-cache | C1 | Python | `functools.cache` / `lru_cache(maxsize=None)` |
| aging-py-insert-without-retention | C2 | Python | SQL `INSERT` (or ORM create) with no `DELETE` anywhere in the file |
| aging-py-tempfile-without-cleanup | C2 | Python | `mkstemp`/`mkdtemp`/`NamedTemporaryFile(delete=False)` with no removal in the file |
| aging-py-http-without-timeout | C4 | Python | `requests.*` / `urlopen` without `timeout=` |
| aging-py-subprocess-without-timeout | C4 | Python | `subprocess.run/call/check_output` or `communicate()` without `timeout=` |
| aging-py-executor-per-call | C4 | Python | thread pool / process pool / thread created inside a function, not in `with` |
| aging-py-fire-and-forget-task | C4 | Python | `asyncio.create_task`/`ensure_future` whose result is discarded |
| aging-py-select-without-limit | C5 | Python | SQL `SELECT` without `LIMIT` followed by `fetchall()` |
| aging-js-global-collection-growth | C1 | JavaScript | module-level Map/Set/array/object grown inside a function, with no removal in the file |
| aging-js-insert-without-retention | C2 | JavaScript | SQL `INSERT` with no `DELETE` anywhere in the file |
| aging-js-select-without-limit | C5 | JavaScript | `db.all(...)` with a `SELECT` without `LIMIT` |
| aging-js-interval-without-clear | C4 | JavaScript | `setInterval` with no `clearInterval` in the file |
| aging-js-listener-in-handler | C4 | JavaScript | `.on(`/`.addListener(` on a long-lived emitter inside a route handler |
| aging-js-child-process-without-timeout | C4 | JavaScript | `exec`/`execFile`/`spawn` without a `timeout` option |
| aging-js-fetch-without-timeout | C4 | JavaScript | `fetch(...)` without `signal` |
| aging-js-tempfile-without-cleanup | C2 | JavaScript | writes under `os.tmpdir()`/`mkdtemp` with no `unlink`/`rm` in the file |
| aging-rs-shared-map-growth | C1 | Rust | `.insert(`/`.push(` on lock-guarded shared state, with no `remove`/`retain`/`clear` in the file |
| aging-rs-command-without-timeout | C4 | Rust | `std::process::Command` `.output()`/`.status()` (blocking, no timeout) |
| aging-rs-thread-per-call | C4 | Rust | `std::thread::spawn` inside a function whose handle is never joined |
| aging-rs-explicit-leak | C1 | Rust | `Box::leak` / `std::mem::forget` |
| aging-rs-tempfile-without-cleanup | C2 | Rust | files under `env::temp_dir()` with no `remove_file`/`remove_dir_all` in the file |
| aging-rs-insert-without-retention | C2 | Rust | SQL `INSERT` with no `DELETE` anywhere in the file |

C3 (resource leaks: files, sockets, DB connections, processes) in Python is already covered by
**LeakAudit** (paper suite) and is not duplicated here.

## Changelog

- 1.0 (2026-10-12): initial rule set, frozen before the first corpus run.
  23 rules (Python 9, JavaScript 8, Rust 6), 92 test assertions passing. Hashes in RULES.sha256.
  During test development (before freezing) the filter gained: metavariables parsed from
  message tags (Semgrep CE 1.168 no longer exports them in JSON), comments ignored by file-level
  conditions, scoped regex flags.

## Known false negatives of 1.0 (found in the first corpus run, NOT fixed in 1.0)

Per the protocol, misses observed on the corpus are logged here and are candidates for a 1.1
evaluated separately; 1.0 stays unchanged so its corpus results remain an honest measurement.

| app | mechanism missed | why the rule missed it |
|---|---|---|
| paper-actix-monitor | `Command::new("ps")` bound to a variable, then `cmd.output()` with no timeout (C4) | `aging-rs-command-without-timeout` only recognizes the builder when chained in the same expression |
| os-fastapi-uptime | records upserted in MongoDB (`motor`) with no retention (C2) | the C2 rules cover SQL strings and the Django ORM only, not NoSQL clients |
