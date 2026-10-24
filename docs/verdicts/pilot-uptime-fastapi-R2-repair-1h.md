# Verdict: uptime-fastapi-F4, repair assessment (R1 before vs R2 after)

**Verdict: REPAIR_EFFECTIVE**, with a small constant overhead on the write path (judged acceptable) and a
functional trade-off on `/services` above the cap (§6).

## Runs compared

| role | run | app | `app.py` hash | start (local) |
|---|---|---|---|---|
| before (targeted) | `runs/20261012-224353_R1_F4_targeted_v1` | `apps/uptime-fastapi/instrumented` | `6799e8e1…` | 2026-10-12 22:43 |
| after (repaired) | `runs/20261013-013324_R2_F4_repaired` | `apps/uptime-fastapi/repaired` | `94da000b…` | 2026-10-13 01:33 |
| matched control | `runs/20261012-214141_R0b_F4_matched_control` | instrumented, `--key-space 4` | `6799e8e1…` | 2026-10-12 21:41 |
| original control | `runs/20261012-203930_R0_F4_control_jmx` | instrumented, JMeter-faithful | `6799e8e1…` | 2026-10-12 20:39 |

**Run validity:**
- R2 used the same workload as R1: `targeted_v1`, hash `c163ab5e…`, seed 42, 3600 s, no extra arguments.
- Both runs used the same server host (`cesar-pc`) in two-PC mode with the same `aging_probe.py` (`8127cc9a…`).
- In R2, `workload_exit=0`, the server was alive at stop and `server_exit=0`.
- R2 had 0 errors in 55 950 requests, and 35 960 grow keys were sent, all acknowledged with 200.
- R1 sent 35 811 grow keys, with its pacer dropping slots in the last 10 minutes. R2 dropped none.
- The R2 clock offset was −0.7 ms, with 4.8 ms of drift.
- Statistics come from `.venv/bin/python harness/analyze_run.py <R1> --compare <R2>`, which wrote
  `analysis/summary.json`, `summary.md`, `comparison_vs_20261012-224353_R1_F4_targeted_v1.{md,png}` and
  `series.png`. The per-window and per-phase breakdowns below are descriptive and computed from the raw
  `client.csv`, `monitor.csv` and `instrumentation.jsonl`. They are not extra tests.

**The repair** (`diff -ru instrumented repaired`, which touches no `AGING-PROBE` lines):
- `MAX_SERVICES_PER_TOKEN = 1000` (environment-overridable).
- A new index `heartbeats(token, last_notification)`.
- In `upsert_heartbeat`: an existence `SELECT` before the upsert. Only when the row is new, a
  `DELETE … ORDER BY last_notification DESC LIMIT -1 OFFSET 1000` evicts the least-recently-heartbeated rows.
- `LIMIT ?` on the `/services` query.

## 1. Path executed and the resource is bounded

**Both suspect paths ran exactly as often as in R1:**
- `instr.calls.upsert_heartbeat`: 1.14e3 → 5.06e4 (R1: 5.04e4).
- `instr.calls.list_services_for_token`: 101 → 5.40e3 (R1: identical).
- `instr.rows_fetched.total` still grows (cumulative), but at 2.17e6/h against R1's 3.34e7/h, about 15× less.

**The cap bounds the resource and takes effect early.** From the 10 s snapshots
(`instr.rows.max_per_token` / `instr.rows.total`):

| t (s) | 60 | 70 | 80 | 90 | 100 | **110** | 120 … 3600 |
|---|---|---|---|---|---|---|---|
| rows.max_per_token | 570 | 670 | 770 | 870 | 970 | **1000** | 1000 (constant) |
| rows.total | 774 | 874 | 974 | 1074 | 1174 | **1204** | 1204 (constant) |

- The cap is reached at about 105–110 s, 45–50 s after the 60 s warm-up cut.
- After the cap: `rows.max_per_token` and `fetch_rows.max` / `serialized_rows.max` stay at 1000, `db.bytes` at
  311 296 B, and `services_grow.resp_bytes` at 91 001 B in every window, through the end of the run.
- About 34 960 grow keys were evicted. This is inferred as 35 960 sent minus 1000 retained; there is no
  eviction counter.

**The card's growth rule is no longer met.**
- `rows.max_per_token` Sen slope is 0/h and MK-HR p = 0.083 (no trend). The raw MK p of 5.6e-4 comes only from
  the 5 ramp snapshots before the cap.
- First → last is 670 → 1000, a ratio of 1.49, against the ≥ 10 the rule requires. All of that ratio
  accrues in the 60–110 s ramp.
- In R1 the same series had a 53× ratio and kept rising all hour (§2 of R1's verdict).

## 2. Symptoms that qualified in R1, re-checked in R2 (point 1)

Criteria: (a) MK-HR p < 0.05, (b) the trend is in the degrading direction, (c) `sen_change_pct` ≥ 10%,
(d) the trend is absent in control.

| series | R1 (before) | R2 (after): first → last, Sen /h, change %, MK-HR p, trend | R2 result |
|---|---|---|---|
| `client.services_grow.latency_p50_ms` | +468 ms/h, +193%, SYMPTOM | 27.3 → 27.8 ms, −3.65, **−12.4%**, 4.8e-10, decreasing | fails (b): it improves |
| `client.services_grow.latency_p95_ms` | +471 ms/h, +176%, SYMPTOM | 45.0 → 39.5 ms, −12.5, −25.5%, 2.7e-4, decreasing | fails (b) |
| `client.services_grow.resp_bytes_mean` | +3.28 MB/h, +190%, SYMPTOM | 77 kB → 91 kB, 0, 0%, 0.094, no trend | fails (a) and (c) |
| `server.watched_files_mb` (db) | +5.37 MB/h, +191%, SYMPTOM | 0.191 → 0.297 MB, 0, 0%, 0.26, no trend | fails (a) and (c) |
| `client.heartbeat.latency_p95_ms` | +203 ms/h, +142%, SYMPTOM | 37.2 → 35.1 ms, −10.1, −36.8%, 0.014, decreasing | fails (b) |
| `server.rss_mb` | +59.9 MB/h, +62.5%, SYMPTOM | 55.2 → 57.9 MB, +1.96, **+3.3%**, 1.4e-4, increasing | fails (c), and (d) is partly shared with R0b. See §3 |
| `server.uss_mb` | +59.3 MB/h, +70.3% | 44.4 → 47.0 MB, +1.74, +3.65%, 8.0e-6, increasing | fails (c), same as RSS |
| `client.*.error_rate` | 0 | 0 (constant) | none |
| `client.heartbeat.throughput_rps` | 14.0 → 13.8 at the end | 14.0, no trend (p = 0.25) | none |

**Server-side mechanism series** (10-minute medians, R2):

| series | R2 medians | R2 statistics | R1 for comparison |
|---|---|---|---|
| `instr.fetch_ms.max` | 3.08, 3.09, 3.08, 3.08, 3.16, 3.09 | flagged increasing (HR p = 0.016) but +1.9%: *statistically significant but negligible* | 5.6 → 82 |
| `instr.upsert_ms.max` | 15.9 to 16.2 | no trend (p = 0.47) | 33 → 215 |
| `instr.lock_wait_ms.upsert.max` | 33.7 → 21.9 | decreasing | 56 → 217 |
| `instr.services.handler_ms.max` | 37.6 → 26.9 | decreasing | 52 → 264 |

**Is the residual trend a ramp artifact?** The first post-warm-up window (60–120 s) is partly pre-cap: rows go
from 570 to 1000 inside it.
- In that window `services_grow` p50 is 27.3 ms, against 28.9–29.7 ms in the windows from 120 to 1200 s. Its
  mean response size is 77 kB, against 91 kB afterwards.
- So the ramp lowers the first point, which biases the test *toward* "increasing". Even so, the latency
  series come out *decreasing*, and the size series has no trend. The ramp produces no spurious degrading
  trend.

**The decreasing trends are steps, not ramps.**
- `heartbeat` p95 steps from about 37 to about 26 ms between the 1440 s and 1500 s minutes, and stays there.
- `services_grow` p95 steps from about 50 to about 40 ms around 1800 s.
- Neither has a counterpart in row count, which is constant at 1000.
- R0b shows the same kind of step: heartbeat p95 drops from 33.4 to 23.8 ms around 940–960 s.
- In both runs the step coincides with a one-off VMS jump of about 72 MB (R2 at 1478 s: 523 → 596 MB; R0b at
  937 s: 522 → 594 MB). This looks like a runtime or allocator event, such as a new malloc arena or threadpool
  worker. That is a hypothesis; it was not instrumented. It does not degrade anything.

**Conclusion for point 1:** none of R1's qualifying symptoms satisfies (a) to (d) in R2. Every R1 symptom
series is now flat, bounded, or moving in the improving direction.

**Other R2 series:**
- `services_ref4` p50 has the same non-monotonic phase-alignment step seen in R1: about 4 → about 14 ms from
  about 1980 to about 2800 s, then back to about 4.4 ms. No trend (p50 HR p = 0.19; p95 HR p = 0.165), so it
  fails (a).
- `services_ref200` has no trend (p50 −0.9%, p = 0.38). In R1 this series had the small +3.7% drift.

## 3. Residual server RSS growth (point 2)

**The numbers:**
- `server.rss_mb`: Sen +1.96 MB/h, `sen_change_pct` = **+3.33%** over a 57.7 MB median, MK-HR p = 1.4e-4.
- `server.uss_mb`: +3.65%.
- RSS − USS stays at 10.8 → 10.9 MB.

**Shape: a monotonic staircase of a few small steps that flattens, not a sawtooth.** After 60 s there are
12 upward steps, 0 decreases and 695 flat samples, at 13 distinct levels. R1 had 380 increases, 263 decreases
and 598 levels.

| t (s) | RSS (MB) | what happens |
|---|---|---|
| 0 → 105 | 54.1 → 56.0 | rows fill to the cap (allocation follows row count up to 1000) |
| 175, 230 | → 56.2 | small steps |
| 671, 706 | → 56.5 | small steps |
| **1478–1508** | **56.5 → 57.7** | a single +1.25 MB step together with VMS 523 → 596 MB, the same one-off event as in §2 |
| 2690 | 57.7 → 57.9 | +0.12 MB, the only change in the last 2100 s |

**Linear slope by period:**

| period (s) | slope (MB/h) |
|---|---|
| 60–900 | 2.8 |
| 900–1800 | 6.9 (all from the single step) |
| 1800–2700 | 0.05 |
| 2700–3600 | 0.00 |

In R1 the 5-minute envelope ceiling rose from 60 to 131 MB. In R2 the per-5-minute max is 56.2 to 57.9 MB, so
the per-call peak is now bounded by the cap.

**Comparison with the controls:**
- R0b (matched): +0.29 MB/h, +0.52%, HR p = 8e-8, also flagged "increasing". The same sign and kind of slow
  creep therefore exists without key growth, so (d) is only partly met.
- R2 exceeds R0b by about 1.7 MB/h, almost entirely the single 1.25 MB step at 1478 s.
- R0 (JMeter-faithful) is flat.

**Judgement:**
- The series fails criterion (c): +3.3% is well below 10%.
- Its shape is a plateau reached early plus one-off allocator steps, with essentially zero slope over the
  last 30 minutes. R1's RSS tracked row count instead (r = 0.94 with response size).
- Classification: *statistically significant but negligible*. This is **not a residual symptom**.
- The final level of 57.9 MB is about 3 MB above R0b's 54.9 MB. That is consistent with materialising 1000
  instead of 200 rows per `/services` call, which is a bounded working set.

## 4. Regressions introduced by the repair (point 3)

| metric | R0b (control, 200 rows) | R1 (before) | R2 (after) | assessment |
|---|---|---|---|---|
| heartbeat p50, window median | 13.88 ms | 14.53 ms | 14.89 ms | +0.36 ms (+2.5%) vs R1; +1.0 ms vs R0b |
| heartbeat p50, raw after 60 s | — | 14.48 ms | 15.07 ms | +0.59 ms (+4%) |
| heartbeat p50, `grow` phase only, 60–600 s | — | 14.04 ms | 15.09 ms | **+1.05 ms (+7.5%)**. This is the purest estimate: new-key inserts, while R1's table was still small (< 6k rows) |
| heartbeat p50, `ref` / `refresh` phase, 60–600 s | 25.1 / 27.6 ms | 31.9 / 32.1 ms | 34.3 / 33.9 ms | +1.8 to +2.4 ms |
| heartbeat p50 trend | no trend | no trend | −7% (decreasing; it settles at 14.5–14.8 ms after 1800 s) | not a degrading trend |
| `instr.upsert_ms.max`, median of the per-10 s max | 11.6 ms | 128 ms (rising) | 16.1 ms (flat, p = 0.47) | about +4.5 ms worst-case per window vs R0b; about +1 ms vs R1 at about 1000 rows (14.5–15.7 ms at t ≈ 100–110 s) |
| `server.cpu_percent`, median | 3.8% | 14.2% (rising to 24%) | 5.0% (no trend) | +1.2 points vs R0b, constant |
| `services_grow` p50 | 15.0 ms (200 rows) | 231 ms (rising) | 27.9 ms (flat) | bounded. The level is set by the 1000-row cap, a design parameter, not a regression |

**Source of the overhead:**
- Every heartbeat now runs an extra point `SELECT` on the primary key.
- Every new-key heartbeat also runs a `DELETE` whose subquery walks the token's 1000 most recent index entries
  (`OFFSET 1000`), plus maintenance of the new `(token, last_notification)` index. All of this happens inside
  `_db_lock`.

**Judgement: acceptable.**
- The overhead is constant, about +0.4 to +1.0 ms on heartbeat p50 (+2.5 to +7.5%) and about +1 point of
  server CPU.
- It has no trend, and it is below the 10% effect threshold. The client measured no throughput loss: 14.0
  req/s, and 0 slots dropped against R1's late drop.
- It replaces a degradation of hundreds of ms per hour in R1.
- It matches the card's `repair_expectation` ("a small constant overhead on /heartbeat").
- The cost of the eviction subquery scales with the cap (O(cap) per new key), not with uptime. A much larger
  `MAX_SERVICES_PER_TOKEN` would raise this constant; this is noted for tuning, not as an aging issue.

## 5. Confounders between R1 and R2 (point 4)

| confounder | could it explain… | assessment |
|---|---|---|
| **Client CPU governor** (R1 `powersave`, R2 `performance`) | small absolute shifts in client-measured latency, a fraction of a ms | It **cannot** create or remove a trend that tracks row count. R1's row-tracking trends also appear in server-side probes measured on the unchanged server host (`fetch_ms`, `upsert_ms`, `lock_wait_ms`, `handler_ms`, RSS, db size). Those flatten in R2 for the same reason the client series do: the rows stopped growing. The faster governor would *lower* R2's client latency, so the heartbeat p50 overhead in §4 is, if anything, an underestimate. R1's own fixed-size probes (`ref200`, `ref4`) already showed the governor causes no row-correlated trend. |
| **Time of day** (R1 22:43, R2 01:33 local) | absolute latency differences from LAN or host background activity | It cannot produce a slope proportional to rows. RTT was stable at 1.5–1.8 ms in R2 and 1.2–1.9 ms in R1. `ref200` medians are 6.4 ms (R1) vs 6.8 ms (R2): a +0.4 ms level shift common to every endpoint is within this kind of noise, and could account for part of the +0.4 ms heartbeat p50 difference. |
| **Clock offset** (R1 13.1 ms, R2 −0.7 ms; drift ≤ 26 ms) | at most a ~10 ms shift in aligning server samples with client windows | Negligible against 5 s monitor samples, 10 s snapshots and 60 s windows. It cannot affect trends. |
| **Different server bundle** (built `20261013-0119`) | — | The only app difference is `app.py` (the repair). The probe and workload hashes are identical. |
| **One-off VMS/allocator step** (§2, §3) | the R2 p95 step-downs and the 1.25 MB RSS step | It appears in R0b too, independent of the repair. It is not degrading. |

**What cannot be ruled out:** whether part of the +0.4 ms heartbeat p50 difference is environmental (time of
day, governor) rather than repair overhead. The grow-phase comparison (+1.05 ms) and the R0b comparison
(+1.0 ms) put the repair's overhead at about 1 ms at most.

## 6. Functional caveat (point 5): a behaviour trade-off, not a performance regression

Above `MAX_SERVICES_PER_TOKEN` (1000) distinct serviceIds per token:
- `/services` no longer lists all services the token has ever reported.
- The least-recently-heartbeated services are evicted from storage, permanently, not just hidden.
- In this run about 34 960 of the 35 960 grow services were dropped. The response stayed at the 1000 most
  recently active ones (91 kB).

A client that heartbeats more than 1000 live services per token would therefore see an incomplete list, and an
evicted service that heartbeats again reappears as new. The `LIMIT` on `/services` is redundant with the
write-side cap. It also applies `ORDER BY service_id`, so if the cap were ever exceeded transiently, the
truncation would follow service_id order, not recency.

The report should present this as a semantics change: bounded retention with LRU eviction, made configurable
through the environment, in place of unbounded history. The BaxBench functional tests are a separate step
(aging-functional-validator) and were not assessed here.

## Alternative explanations (repair run)

| explanation | status | evidence |
|---|---|---|
| Client saturation | ruled out | `loadgen.cpu_percent` median 3.6%, p95 3.8%, max 13.8%. 0 errors and no timeouts (max latency 485 ms). Every target rate was met. |
| Host effects on the server | ruled out | The server is the same host as R1. Server CPU median is 5%, p95 5.6%. The db is 0.3 MB. |
| Warm-up / ramp transient | ruled out as a source of degrading trends | The cap is reached at about 110 s. The ramp biases toward "increasing", yet no degrading trend remains (§2). |
| Instrumentation overhead | ruled out | `snapshot.query_ms` median 0.52 ms every 10 s, +1.6% (negligible). `snapshot.lock_wait_ms` about 0.008 ms. |
| Governor, time of day, clock | cannot explain removal of the trends; may explain ≤ 0.4 ms level shifts | §5 |

## Next step

The repair is effective. Proceed to the report. Record these items:
- The constant overhead: about +1 ms heartbeat p50 at most, and about +1 point of server CPU.
- The functional trade-off: LRU eviction above 1000 services per token.
- The RSS plateau: about 58 MB, against R1's 107 MB and still rising.

Optional, not required for the verdict:
- A 2-hour R2 repetition would confirm the RSS plateau beyond the one-off allocator step.
- An R1 re-run with both governors set to `performance` would remove the remaining governor asymmetry from the
  heartbeat-overhead estimate.
