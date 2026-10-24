# Credit Card (bax-fastapi-credit-card-F1): repair and review

**Repair** (`apps/credit-card-fastapi/repaired`, aging-repair-agent, 24/10/2026). **Partial, within the
spec**, as the researchers decided: no pagination, cap, new parameters or deletion.
- The `/retrieve_cards` 200 body is pre-rendered as bytes inside the worker thread, in 4,096-card
  `json.dumps` chunks, by a `Response` subclass. FastAPI skips `response_model` validation and
  serialization and encoding on the event loop.
- New covering index `idx_assoc_phone_card(phone, credit_card)` (additive, `IF NOT EXISTS`) and a
  single-phone fast path (`WHERE phone = ? ORDER BY credit_card`).

**Review** (aging-repair-reviewer): **APPROVE**.
- Byte-identical status, headers and body for valid and invalid inputs, including the real R1 data
  (5,469,282 B). OpenAPI identical.
- Worst stall seen by a concurrent thread: 37.4 ms → 6.6 ms.
- Plans verified on SQLite 3.37.2 and 3.45.1. Fast path 164 → 38 ms (3.37). The probe's snapshot
  plans are unchanged.
- Costs: db +25% (index 11.3 MB at 288k rows). Insert CPU unchanged. The multi-phone path
  including the hot phone is still O(cards on the hot phone); it is not exercised by the workload.

**Independent checks** (Claude, 24/10):
- HTTP differential test against both apps running (13 responses: 200/400/404, multi-phone
  including the hot phone, duplicates, non-ASCII): 0 differences.
- BaxBench functional test PASS, with probes off and on.

**Conditions for the R2 assessment (from the review):**
1. Do NOT use `retrieve.handler_ms` or "client − handler" as R1-vs-R2 evidence. The untouched probe
   line `_probe_resp = RetrieveCardsResponse(...)` now wraps the JSON encoding, so these series
   change meaning (`aging_probe.py:54` says "excl. JSON encoding", which no longer holds in R2).
2. Use instead: hot end-to-end latency; "client − `retrieve.db_ms`"; associate p95 and ref4 in the
   group of requests that overlap a hot request vs the non-overlapping group and vs R0b; RSS peak
   per response-MB; `instr.associate.db_ms.max`; `rows.associations`; `resp_bytes_mean` (must be
   identical in growth).
3. Expected outcome: REPAIR_PARTIAL. Interference "within a few ms of R0b" is the bar, not zero;
   latency and response size keep growing, as the spec requires.
4. Record the server's `sqlite3.sqlite_version` and CPU model in R2.
