# Aging triage: `bax-fastapi-uptime`

**Inputs:**
- Source: `app/app.py` (177 lines) and `app/requirements.txt`
- AI reviews r1, r2 and r3
- `tool_findings.json` (15 findings)
- `_schema.yaml`

I opened and verified every cited line in `app/app.py`. Everything below is a static hypothesis that needs runtime validation. None of it confirms aging.

**Result:** one card, `bax-fastapi-uptime-F1`, classified `conditional` with priority 2. No mechanism qualifies as `supported`, so no card has priority 1. The only element that depends on the workload is key diversity: there must be new `(serviceId, token)` pairs. HTTP traffic alone controls that.

## 1. All candidates

Merged ids: `C*` comes from AI reviews and possibly tools, and `T*` comes from tools only. Line numbers refer to `app/app.py`.

| Merged id | Sources | Verdict | One-line reason (verified in code) |
|---|---|---|---|
| **C1 → F1** | r1:F1 (CR-3, FA-5, FA-2 part), r1:F3 (CR-4, FA-2), r2:F1 (CR-2, FA-2), r2:SELF-SOURCED 1, r3:F2 (CR-4, CR-3, FA-3), r3:F6 length part (FA-6), semgrep-aging:aging-py-insert-without-retention (L50), semgrep-aging:aging-py-select-without-limit (L68), slowql:PERF-SCAN-003 (L69) | **conditional** | Each new pair inserts a permanent row (L50-59). No DELETE/TTL exists anywhere, and the fields have no length cap (L82-95). `/services` runs `fetchall` with no LIMIT under `_db_lock` (L65-79) and builds per-row models (L159-168). The read side is merged with the write side because one workload validates both. The condition is a continuing stream of distinct pairs. r2's self-sourced item and r3:F6's unbounded field length raise bytes per row on the same insert path, so they are folded in as aggravators. |
| C2 | r1:F2 (CR-1, FA-1), r2:F2 (CR-1, FA-1), r3:F1 (CR-1, FA-1) | non-aging | `_db_lock` is held across connect, execute, commit and close (L47-61, L65-79), and the handlers are sync `def` (L124, L152). This is real serialization and threadpool contention. It starts with the first concurrent burst and clears when load drops. Nothing is retained, because the lock is released in `with` and the connection is closed in `finally`. It degrades over time only through C1, because lock-hold time grows with the rows read. |
| C2a | r1:FA-1 sub-claim, repeated in r2:FA-1 and r3:FA-1 ("non-DB requests and framework sync work starve") | unsupported | FastAPI 0.115 parses and validates the request body in the async path before it dispatches a sync endpoint to the threadpool, and `/docs`/`openapi.json` are async. Only sync endpoints wait for a thread token. r1 rejected this claim; r2 and r3 did not address it. It is not aging either way. |
| C3 | r1:F4 (CR-2, FA-3), r2:F5 (CR-5, FA-1/FA-3 clauses), r3:F3 multi-process part (CR-5, FA-2) | non-aging | `threading.Lock` is per-process (L17), `connect` uses the default timeout and no WAL is set (L26, L48, L66). Contention needs a multi-worker deployment, but `__main__` runs one process (L177). Each stall is transient and bounded by the busy timeout, and nothing accumulates. |
| C4 | r1:rejected CR-5, r2:F4 (CR-4, FA-4), r3:F3 cwd part (CR-5, FA-2) | non-aging | `DB_PATH = "db.sqlite3"` is relative (L12). This is a deployment and data-location correctness issue with no per-request resource cost. The claim that stray DB files build up needs a launch from a different cwd and does not grow at runtime. |
| C5 | r1:F6 (CR-6, FA-4), r2:F3 (CR-3), r3:F4 (CR-2, FA-4) | non-aging | The fallback `fromisoformat` at L166 is unguarded, and L171-172 turns a failure into a 500. The defect is real, but the only writer (`utc_now_iso`, L21) always writes valid ISO text, so triggering it needs an external writer. It is a correctness issue with no accumulation. |
| C6 | r1:F5 (FA-6) | non-aging | The timestamp is computed at L134 before the lock is acquired, and the upsert overwrites unconditionally (L55). This can produce slightly stale values under contention. It is a bounded correctness issue with no accumulation. |
| C7 | r2:F6 (CR-6, FA-3), r1:FA-3 rejected sub-claims, r3:F6 startup part (FA-5) | non-aging | The schema is created only in the deprecated `on_event("startup")` hook (L113-115). It is a one-off startup cost or failure, and "no such table" needs an external event such as deleting the file or skipping startup. |
| C8 | r3:F5 (CR-6, FA-6 part) | non-aging | `serviceId` is stripped (L125) and `token` is not (L126, L72). This is a key-normalization correctness issue. It adds C1 rows only when clients send whitespace variants, so it is not an independent mechanism. |
| C9 | r3:F6 redundant-check part (FA-6) | non-aging | The `isinstance` checks (L130, L154) and the `except HTTPException: raise` clauses (L136, L169) are effectively dead. They cost a negligible constant amount per call. |
| T1 | CodeQL py/untrusted-data-to-external-api (L57, L75) | non-aging (out of scope) | A security rule. The queries are parameterized (`?` placeholders, L53, L72), so no resource is involved and nothing accumulates. The resource-related part, untrusted keys creating rows, is already covered by C1. |
| T2 | CodeQL py/possible-timing-attack-sensitive-info and py/timing-attack-sensitive-info (L130, L154; 4 findings) | unsupported | `token == ""` compares against an empty literal, not a secret. `APP_SECRET` (L15) is never compared. This is not aging-related either. |
| T3 | slowql QUAL-SCHEMA-002 (L29) | unsupported | `service_id` is not a foreign key, because no other table exists. This is a quality rule that names no resource. |
| T4 | slowql COST-COMPRESS-001 (L29) | non-aging | TEXT columns are stored without compression, which is a constant factor per row. It at most scales C1's bytes per row and is not an independent mechanism. |
| T5 | slowql QUAL-NAME-003 (L39) | non-aging (out of scope) | A naming-pattern match that states no fault. |
| T6 | slowql QUAL-STYLE-005 (L51) | unsupported | The claim of an INSERT without a column list is false: L52 names `heartbeats(service_id, token, last_notification)`. |
| T7 | slowql COST-IDX-003 (L69) | non-aging | A covering index on `token` could remove the table lookup and possibly the sort. This is a constant-factor cost per row on C1's read path, so it is an amplifier and not a separate accumulation. |
| T8 | slowql QUAL-DEAD-001 (L39) | unsupported | `idx_heartbeats_token` is the natural access path for `WHERE token = ?` (L72). The tool counts only explicit index references. The plan the planner actually picks is an open question below. |

**Counts:**
- 1 card: C1 → F1.
- 12 non-aging: C2, C3, C4, C5, C6, C7, C8, C9, T1, T4, T5, T7.
- 5 unsupported: C2a, T2, T3, T6, T8.
- All 15 tool findings are accounted for: 3 merged into F1 and 12 in rows T1–T8.

## 2. Source contribution per card

| Card | r1 | r2 | r3 | semgrep-aging | LeakAudit | slowql | CodeQL | Channel |
|---|---|---|---|---|---|---|---|---|
| bax-fastapi-uptime-F1 | F1 (write side) + F3 (read side) | F1 + SELF-SOURCED 1 | F2 + F6 (length aggravator) | insert-without-retention, select-without-limit | — (no resource-leak findings in the input) | PERF-SCAN-003 | — | **both** |

**Totals:** AI only 0, tools only 0, both 1.

The AI reviews and the tools found the same aging mechanism independently. The tools added nothing aging-related that the AI reviews missed.

## 3. Disagreements and resolutions

### Between AI repetitions

1. **The verdict on table growth.** r1:F1 calls it "static aging mechanism supported", with a caveat about workload strength. r2:F1 and r3:F2 call it "conditionally plausible".
   - **Resolution:** `conditional`. All four elements are present in the code, but accumulation needs a continuing supply of distinct pairs, and nothing in the code produces that. The condition can be controlled through the workload, which is why priority is 2 and not lower.
2. **Whether to split the read side.** r1 keeps the unbounded `/services` read as a separate finding (F3, "a downstream amplifier, not an independent accumulation"). r2 and r3 merge it with the growth.
   - **Resolution:** merged into F1. It is the same resource (the `heartbeats` rows) and the same workload validates both sides. r1's own adjudication says the read side has no accumulation of its own.
3. **Whether `ORDER BY service_id` needs a temp sort.** r1 CR-4, r1 FA-2, r2 FA-2, r3 CR-3 and r3 FA-3 assert a sort. All three adjudicators say the planner choice cannot be verified statically.
   - **Resolution:** left open (see Open questions). The answer changes which workload degrades `/services`, but it does not change the classification.
4. **Where the sync startup hook runs.** r1 FA-3 says it runs in the threadpool. r2 FA-3 and r3 FA-5 say it runs on the event loop.
   - **Resolution:** Starlette 0.41's router calls a sync startup handler directly, so it runs on the loop. Either way it is a one-off cost (C7, non-aging).
5. **Non-DB requests starving.** r1 rejects FA-1's claim that non-DB requests starve. r2 and r3 repeat it without adjudicating it.
   - **Resolution:** unsupported (C2a). Lock contention is non-aging in every repetition.
6. **How long the 500 from a malformed row lasts.** r1, r2 and r3 CR-3 say the 500 persists until the row is fixed by hand. r3 CR-2 says it lasts until a new heartbeat overwrites the row.
   - **Resolution:** r3 CR-2 is right, because the upsert at L55 overwrites `last_notification`. Not aging either way (C5).
7. **Items raised in only one repetition.**
   - Out-of-order timestamps: r1 only.
   - Schema created only at startup: r2 only.
   - Whitespace asymmetry and unbounded field length: r3 only.
   - **Resolution:** all are non-aging, except that r3's field-length point is folded into F1 as an aggravator.

### Between AI and tools

1. **QUAL-DEAD-001, "index never used".** Every AI reviewer treats `idx_heartbeats_token` as the lookup path. **Resolution:** the tool finding is unsupported (T8). The real plan is still to be checked at runtime.
2. **QUAL-STYLE-005, "INSERT without column list".** No AI reviewer raised it. **Resolution:** the code contradicts it (T6).
3. **CodeQL timing-attack findings.** No AI reviewer raised them. **Resolution:** unsupported, because no secret is compared (T2).
4. **COST-IDX-003 (covering index).** This agrees with the AI reviewers' point that the token index does not cover the `ORDER BY`. **Resolution:** a constant-factor amplifier on F1's read side (T7).
5. **Lock serialization and multi-process contention (C2, C3).** All AI repetitions found these and no tool did. **Resolution:** both are non-aging, so the gap does not affect the card list.

## Open questions (not decidable from the code)

- **Real-world pair cardinality.** Whether production traffic keeps producing new `(serviceId, token)` pairs, which decides if F1 activates at all outside a synthetic workload.
- **The query plan for the SELECT at L68-76.** SQLite may pick `idx_heartbeats_token` plus a temp B-tree sort, in which case cost scales with rows per token. It may instead scan the `(service_id, token)` PK autoindex in order to avoid the sort, in which case cost would scale with the total table size, including other tokens' rows. Running `EXPLAIN QUERY PLAN` once at runtime settles this, and the answer decides whether a distinct-token workload alone degrades `/services`.
- **Upstream body-size limits.** Whether any proxy limits body size, which affects how fast the database grows in bytes per row.
- **Deployment topology.** Single or multiple workers. This is irrelevant to F1 but decides whether C3 matters.
