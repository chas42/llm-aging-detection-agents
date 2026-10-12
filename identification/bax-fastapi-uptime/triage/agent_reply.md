- **Cards:**
  - `bax-fastapi-uptime-F1` — conditional — priority 2 — each `POST /heartbeat` with a new `(serviceId, token)` pair adds a permanent row to `heartbeats` and both of its indexes. No code deletes rows, caps them or limits field length. `POST /services` then reads all of that token's rows with no LIMIT while holding the global `_db_lock` (`app.py:46-79`, `app.py:152-172`). As a token gains rows, `/services` latency and response size grow, and so do the latency spikes on concurrent heartbeat writes waiting for the lock. Write-side growth and read-side cost are one causal chain that one workload validates, so they share one card. It is conditional only because the workload must keep sending new pairs. HTTP traffic alone controls that, so it is workload-controllable. No mechanism is `supported`, so nothing got priority 1.
- **Non-aging / unsupported:** 17 in total.
  - **12 non-aging:** C2 (global lock and threadpool contention), C3 (several processes sharing the SQLite file), C4 (relative `DB_PATH`), C5 (unguarded timestamp parse fallback), C6 (timestamp taken before the lock), C7 (schema created only at startup), C8 (`serviceId`/`token` whitespace asymmetry), C9 (redundant checks), T1 (CodeQL untrusted-data, parameterized queries), T4 (slowql COST-COMPRESS-001), T5 (slowql QUAL-NAME-003), T7 (slowql COST-IDX-003).
  - **5 unsupported:** C2a (claim that requests not using the database starve), T2 (CodeQL timing attack; the token is only compared with `""`), T3 (slowql foreign-key rule; there is no other table), T6 (slowql "INSERT without column list"; the column list is on line 52), T8 (slowql "index never used"; the `WHERE token = ?` query uses it).
- **Source contribution:** AI only 0, tools only 0, both 1. F1 was found by r1, r2 and r3, by the two semgrep-aging rules (insert-without-retention, select-without-limit) and by slowql PERF-SCAN-003. There were no LeakAudit findings in the input.
- **Open questions:**
  - Whether real traffic keeps producing new `(serviceId, token)` pairs, which decides whether F1 activates outside a synthetic workload.
  - Which plan SQLite picks for the SELECT at `app.py:68-76`. If it uses the token index plus a sort, cost grows with that token's rows. If it scans the primary-key index, cost would grow with the whole table, so new tokens alone would slow `/services` for every token. One `EXPLAIN QUERY PLAN` at runtime will settle this.
  - Whether any upstream proxy limits request body size, which sets how many bytes each row can add.
  - Whether the app is deployed with one worker or several. This doesn't affect F1, but it decides whether C3 matters.

  One more note: the card's `app` field follows the schema's `corpus/bax-fastapi-uptime/src` pattern, but the source I read was `app/`.

Files are in `out/`:
- `bax-fastapi-uptime-F1.yaml`
- `triage.md`