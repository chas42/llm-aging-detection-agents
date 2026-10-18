- **Cards:**
  - `bax-fastapi-credit-card-F1` — conditional — 2 — Each new (card, phone) pair inserted by `/associate_card` adds a permanent row plus 3 index entries, and nothing ever deletes them. `/retrieve_cards` then reads, groups, sorts and loads every matching row with no LIMIT, so the same probe request costs more as hot phones collect cards. Write-side growth and read-side cost are merged into one card because one workload validates both. It needs distinct pairs and a high number of cards per phone.
  - `bax-fastapi-credit-card-F2` — conditional — 4 — The database runs in WAL mode with one connection per request and no checkpoint or size-limit setting. The `-wal` file can stay at or grow past its peak size if overlapping requests mean a connection is always open. Monitor it with a file-size check only, because an extra SQLite connection would itself keep the WAL from being cleaned up.
- **Non-aging / unsupported:** 17 in total.
  - Non-aging, 12: N1 (unbounded phone list), N2 (write-lock contention), N3 (connection opened and closed per request), N4 (redundant index and AUTOINCREMENT write overhead), N5 (unbounded field length), N6 (relative database path and startup race), N7 (phone and digit normalization), N8 (extra Response objects), T4 (unused parameters), T6 (covering-index opportunity), T7 (uncompressed text columns), T9 (GDPR and naming notes).
  - Unsupported, 5: T1 (SlowQL REL-IDEM-001: "no idempotency guard", but the INSERT is idempotent), T2 (CodeQL untrusted-data-to-external-api), T3 (SQL injection, flagged by 3 tools; only `?` placeholders are interpolated), T5 (SlowQL says `idx_assoc_phone` is unused, but the retrieve query uses it), T8 (SlowQL says the INSERT has no column list, but it has one).
- **Source contribution:** AI only 1 (F2), tools only 0, both 1 (F1, found by all 3 AI reviews and the semgrep-aging rule `aging-py-insert-without-retention`).
- **Open questions:**
  - Does the bundled SQLite actually fail to reset the WAL when many short transactions overlap without a break? This decides whether F2 can be triggered at all.
  - What realistic number of cards per phone should the F1 workload use? The code shows the cost grows with it but gives no realistic value.
  - How many uvicorn workers run, and from which working directory? This affects N2 and N6 only, not the cards.
  - F1 gets priority 2 rather than 1 because no mechanism is fully `supported`. It is still the first one to validate.

The cards follow the schema's `app: corpus/<app-id>/src` convention, although the source here was read from `app/`.

Files are in `out/`:
- bax-fastapi-credit-card-F1.yaml
- bax-fastapi-credit-card-F2.yaml
- triage.md