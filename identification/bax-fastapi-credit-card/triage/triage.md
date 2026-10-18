# Triage — bax-fastapi-credit-card

The code reviewed is `app/app.py` (171 lines). It has a single module, two endpoints, SQLite in WAL mode and one connection per request. Every claim below was checked against that file.
The cards use the schema's `app: corpus/<app-id>/src` convention. In this invocation, the source was read from `app/`.
The cards are static hypotheses for runtime validation. None of them is confirmed aging.

## 1. All candidates

| Merged id | Sources | Verdict | Reason |
|---|---|---|---|
| **F1** | r1:Finding 3, r2:Finding 3, r3:Finding 3, r3:Finding 4 (read side); phase-1 r1:CR-4/FA-3/CR-5(part), r2:CR-4/FA-4, r3:CR-4/CR-5/FA-4; semgrep-aging:aging-py-insert-without-retention | conditional (card F1, P2) | The INSERT at lines 131-137 has no DELETE or retention anywhere. The retrieve at 152-167 has no LIMIT and uses fetchall. Growth needs distinct pairs, and the read-side cost needs a high phone fan-out. |
| **F2** | r1:Finding 4, r2:Finding 4 (phase-1 r1:CR-3, r2:CR-3) | conditional (card F2, P4) | WAL mode (l.27), per-request connections (l.49/56), and no checkpoint configuration. Growth needs continuously overlapping connections, and whether it happens depends on SQLite's checkpoint and restart behaviour. |
| N1 | r1:Finding 1, r2:Finding 1, r3:Finding 1 | non-aging | `phone_numbers` has no max length (l.104), and above the SQLite variable limit there is an unhandled OperationalError. All cost is per request, and `finally` closes the connection (l.55-56). |
| N2 | r1:Finding 2, r2:Finding 2, r3:Finding 2 | non-aging | Write-lock contention with the default 5 s busy timeout and sync handlers in the threadpool. This is load-driven exhaustion that is present from the first burst and drains when the burst ends. |
| N3 | r1:Finding 5 (connection part), r2:Finding 2 (CR-5 verdict), r3:Finding 5 | non-aging | Connect, PRAGMA, commit and close on every request (l.47-56), including a commit on the read path. The `finally` close is deterministic, so this is a constant per-call overhead and nothing leaks. |
| N4 | r1:Finding 5 (index part), r2:Finding 3 (index part), r3:Finding 3 (index part); slowql:QUAL-SQLITE-001, slowql:QUAL-DEAD-001 (idx_assoc_card) | non-aging | `idx_assoc_card` (l.41) is redundant with the UNIQUE(credit_card, phone) prefix, and AUTOINCREMENT adds `sqlite_sequence` bookkeeping. This is a constant write amplification per insert. Its storage share is folded into F1. |
| N5 | r2:Finding 5; r1:CR-5 (part), r3:CR-4 (part) | non-aging | `credit_card` and `phone` have no length limit (l.83-100). The per-request O(n) cost is transient. Stored bloat only increases F1's growth rate. |
| N6 | r1:Finding 6, r2:Finding 6, r3 rejected (CR-3/FA-5) | non-aging | The relative `DB_PATH` (l.12) and the multi-worker startup lock in `init_db` are deployment and correctness issues that happen once at startup. |
| N7 | r1:Finding 6 (phone format), r2 rejected CR-6, r3 rejected CR-6/FA-6 | non-aging | `strip()`-only phone normalization (l.76) and `isdigit()` accepting Unicode digits (l.90) are data-correctness issues. At most they add a few more distinct rows to F1. |
| N8 | r1:Finding 6 (FA-6), r2:FA-4 (Response part) | non-aging | The discarded `Response` parameter and the extra Response objects (l.138-140, 165) are a negligible constant cost. |
| T1 | slowql:REL-IDEM-001 (l.132) | unsupported | It claims "INSERT without idempotency guard". The statement is `INSERT OR IGNORE` against UNIQUE(credit_card, phone) (l.36, 133), which is idempotent. |
| T2 | codeql:py/untrusted-data-to-external-api (l.136) | unsupported | It names a connection. The values are bound parameters, the connection is closed in `finally`, and there is no resource effect. |
| T3 | codeql:githubsecuritylab/audit/sql-injection (l.162), semgrep-default:sqlalchemy-execute-raw-query (l.162), slowql:SEC-INJ-001 (l.152) | unsupported | The f-string only interpolates a run of `?` placeholders (l.150). All values are bound, and the app does not use SQLAlchemy. It does not name a resource and has no aging relevance. |
| T4 | codeql:py/unused-parameter ×4 (l.66, 85, 96, 108) | non-aging | The claim is true, but `cls` on classmethod validators and the `_request`/`_exc` handler arguments follow normal conventions. There is no resource or performance effect. |
| T5 | slowql:QUAL-DEAD-001 (idx_assoc_phone, l.40) | unsupported | `idx_assoc_phone` is the index the retrieve query's `WHERE phone IN (...)` (l.155) can use, so it is not dead. The idx_assoc_card half is valid and is covered in N4. |
| T6 | slowql:COST-IDX-003 (l.152) | non-aging | A covering-index opportunity: each matching row costs one table lookup, which is a constant factor per row. It amplifies F1's read side but is not accumulation by itself. |
| T7 | slowql:COST-COMPRESS-001 (l.30) | non-aging | Uncompressed text columns change the bytes per row (F1's growth rate), not whether anything accumulates. |
| T8 | slowql:QUAL-STYLE-005 (l.132) | unsupported | It claims "INSERT without column list", but the column list `(credit_card, phone)` is present at l.133. |
| T9 | slowql:COMP-GDPR-001 ×7, slowql:QUAL-NAME-003 ×2 | non-aging (out of scope) | Reviewed briefly: these are PII-compliance and naming notes, with no resource involved. |

There were no LeakAudit (`resource-leak`) findings and no `performance`-category tool findings. No AI repetition produced a SELF-SOURCED item: r1, r2 and r3 all report "None".

## 2. Source contribution per card

| Card | r1 | r2 | r3 | Tools | Class |
|---|---|---|---|---|---|
| F1 table growth → retrieve cost | Finding 3 | Finding 3 | Finding 3 + Finding 4 | semgrep-aging:aging-py-insert-without-retention | both |
| F2 WAL growth | Finding 4 | Finding 4 | — (dismissed inside its Finding 4 adjudication) | — | AI only |

Totals: AI only = 1, tools only = 0, both = 1.

## 3. Disagreements and resolution

**Between AI repetitions**
- **F1 read side.** r1 and r2 merge the unbounded result set with table growth into one aging finding. r3 keeps table growth as conditionally aging (Finding 3) and rates the retrieve query non-aging on its own (Finding 4).
  - Resolution: merged into F1 as one causal chain. Writes accumulate rows, and reads pay for those rows per request, so one workload validates both: distinct inserts on hot phones plus a fixed retrieve probe.
  - r3 is right that the read path does not accumulate anything by itself. Within the merged chain, it is the degradation channel.
- **F1 verdict.** All three call it "conditionally plausible". r2 says all four elements are present in the code.
  - Resolution: conditional. Accumulation needs distinct keys (INSERT OR IGNORE drops repeats), and visible degradation needs high per-phone cardinality. Both are workload conditions.
- **WAL (F2).** r1 and r2 keep it as weakly conditional. r3's final report has no WAL finding: its CR-3 is about DB_PATH, and it calls FA-4's long-read-snapshot WAL claim speculative.
  - Resolution: kept as conditional at priority 4. The code does lack any checkpoint or size-limit configuration. Growth depends on SQLite's checkpoint starvation behaviour, which short per-request transactions make unlikely.
- **Write contention (N2).** r3 suggests a conditional aging link "only if write cost grows over time (via finding 3)". r1 and r2 call it plainly non-aging.
  - Resolution: non-aging. Insert cost grows only logarithmically with table size, and contention exists from the first burst. If runtime shows a rise in `database is locked` errors over time, F1's run will expose it.
- **Severity.** The repetitions differ slightly (r3 lowers N1 to low-med, and r2 calls FA-4's lock-hold claim overstated). This does not change any verdict.

**Between AI and tools**
- semgrep-aging (C2, insert without retention) agrees with F1. It is the only tool finding that maps to a card.
- SlowQL REL-IDEM-001 and QUAL-STYLE-005 contradict the code (T1, T8). The AI reviews correctly describe `INSERT OR IGNORE` with an explicit column list.
- SlowQL QUAL-DEAD-001 marks both secondary indexes as unused. The AI reviews flag only `idx_assoc_card` as redundant. Resolution: the AI reviews are right, because `idx_assoc_phone` serves the retrieve WHERE clause (T5).
- Three tools flag SQL injection, and no AI review does. Resolution: false positive, since only placeholders are interpolated (T3).
- No tool detected the WAL mechanism (F2), connection churn (N3) or lock contention (N2). LeakAudit produced nothing, which matches the AI conclusion that connections are always closed in `finally`.

## Open questions
- Does the linked SQLite build actually starve the WAL restart under continuous short overlapping transactions? This decides whether F2 can be activated and can only be answered at runtime.
- Which phone fan-out distribution should the F1 workload use? The code does not show what realistic per-phone cardinality is, and F1's read-side degradation depends on it.
- The deployment topology (one or more uvicorn workers, the working directory) is not fixed by the code. It affects N2 and N6 but no card.
