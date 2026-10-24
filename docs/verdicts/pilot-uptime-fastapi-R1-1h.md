# Verdict: uptime-fastapi-F4, loop iteration 1 (targeted_v1)

**Verdict: ACTIVATED_WITH_AGING**

The runs used here:

| role | run | workload |
|---|---|---|
| targeted | `runs/20261012-224353_R1_F4_targeted_v1` | targeted_v1 defaults (10 new keys/s, probes every 2 s) |
| matched control | `runs/20261012-214141_R0b_F4_matched_control` | same workload and rates, `--key-space 4` |
| original control | `runs/20261012-203930_R0_F4_control_jmx` | JMeter-faithful: 4 fixed services, closed loop |

All three runs: 3600 s, seed 42, `workload_exit=0`, server alive at the end with `server_exit=0`, instrumented,
and the same app hashes (`app.py 6799e8e1…`, `aging_probe.py 8127cc9a…`). Mode was two-machine. Server
timestamps were corrected by `clock.offset_s` (13.1 / 23.1 / 28.8 ms) and drift was ≤ 26 ms. The statistics
come from `harness/analyze_run.py` (`analysis/summary.json` and `summary.md`, regenerated for all three
runs). The extra breakdowns below (per-window envelopes and per-phase rates) are descriptive, computed from
the raw CSV/JSONL files.

## 1. Path executed: yes

In R1, `instr.calls.upsert_heartbeat` went from 1.14e3 to 5.04e4 and `instr.calls.list_services_for_token`
from 101 to 5.4e3. Both rose steadily, with Hamed-Rao p (HR p) of 2.2e-6 and 7.8e-12. The per-call cost of
the read path grew with the table:
- `instr.fetch_rows.max` / `services.serialized_rows.max` went from 661 to about 35 800 rows per call. The
  last value in the series is 0, but that is the shutdown snapshot resetting the window maxima, not a real drop.
- `instr.fetch_ms.max` (time holding the lock in the SELECT and `fetchall()`) went from about 5 ms to about
  88 ms. Medians per 10 minutes: 5.2, 16.6, 48.4, 56.0, 72.9, 82.4.
- `instr.services.handler_ms.max` went from 49 ms to 264–287 ms (medians per 10 minutes).

## 2. Resource growth: meets the card's criterion

| series | first → last | Sen /h | change % | MK-HR p | final/initial |
|---|---|---|---|---|---|
| `instr.rows.max_per_token` | 671 → 35 810 | +35 990 | 192 | 2.5e-6 | **53×** |
| `instr.rows.total` | 875 → 36 020 | +35 990 | 190 | 2.5e-6 | **41×** |

The ratios are measured after the 60 s warm-up. Both series have p < 0.05 and a ratio ≥ 10, as the card's
`resource_growth` rule requires.

**Achieved distinct-key growth versus the 10/s target:**
- The client sent 35 811 distinct growing serviceIds, all with status 200. That is 9.95/s over 3600 s.
- From the probe, the slope of `rows.max_per_token` was 9.98 rows/s overall. Per period:

| period (s) | rows/s |
|---|---|
| 60–1200 | 10.00 |
| 1200–2400 | 10.00 |
| 2400–3600 | 9.87 |

- The pacer dropped some slots only in the last ~10 minutes, when the grow stream fell to 9.75/s (−2.5%).
  This is the intended slow-server signal, and it is far below the 10% effect threshold.
- The db file ended at 5.4 MB, about 158 bytes per row. The workload document estimated 100–120 bytes per row.

## 3. Control contrast: yes

- **R0 (JMeter-faithful):** `instr.rows.total` stayed constant at 4 (the card asks for ≤ 4) and
  `watched_files_mb` stayed constant at 0.024 MB.
- **R0b (matched control):** `rows.total` stayed constant at 208 (4 + 4 + 200 by design), `rows.max_per_token`
  at 200, and `db.bytes` at 53 kB. R0b had the same request rates, tokens and probe schedule as R1. Only key
  diversity differed, so R0b isolates the effect of distinct serviceIds.

## 4. Symptoms

Each `expected_symptoms` series is checked against criteria (a) to (d). (a) is MK-HR p < 0.05; (b) is a trend
in the degrading direction; (c) is `sen_change_pct` ≥ 10%; (d) is no comparable trend in the controls.

| series (R1) | first → last | Sen /h | change % | MK-HR p | R0b (matched) | R0 (jmx) | (a) | (b) | (c) | (d) | result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `client.services_grow.latency_p50_ms` | 28.7 → 473 ms | +468 | +193 | 1.2e-8 | decreasing, −21.9% | n/a (no grow token) | ✔ | ✔ | ✔ | ✔ | **SYMPTOM** |
| `client.services_grow.latency_p95_ms` | 46.3 → 489 ms | +471 | +176 | 1.8e-9 | no trend (p = 0.09) | n/a | ✔ | ✔ | ✔ | ✔ | **SYMPTOM** |
| `client.services_grow.resp_bytes_mean` | 79 kB → 3.18 MB | +3.28 MB | +190 | 7.1e-6 | constant at 365 B | constant at 71 B | ✔ | ✔ | ✔ | ✔ | **SYMPTOM** |
| `server.watched_files_mb` (db file) | 0.157 → 5.4 MB | +5.37 | +191 | ≈0 | no trend, 0.051 MB | no trend | ✔ | ✔ | ✔ | ✔ | **SYMPTOM** |
| `client.heartbeat.latency_p95_ms` (secondary) | 35.7 → 205 ms | +203 | +142 | 2.7e-9 | no trend (−5.8%, p = 0.52) | increasing +13.7% (p = 0.0095) | ✔ | ✔ | ✔ | ✔ against R0b, see note | **SYMPTOM** |
| `client.heartbeat.latency_p50_ms` | 14.5 → 15.3 ms | +0.47 | +3.1 | 0.175 | no trend | no trend | ✘ | | | | none |
| `server.rss_mb` | 55.1 → 107 MB | +59.9 | +62.5 | 3.3e-6 | +0.5% (54.6 → 54.9) | 0% | ✔ | ✔ | ✔ | ✔ | **SYMPTOM, but see §5** |
| `server.uss_mb` | 44.2 → 95.7 MB | +59.3 | +70.3 | 4.2e-6 | +0.58% | +0.02% | ✔ | ✔ | ✔ | ✔ | same as RSS |
| `client.heartbeat.throughput_rps` | 14.0 → 13.8 | 0 | 0 | 0.043 | no trend | decreasing, −4.7% | ✔ | (✔) | ✘ | | none: paced open loop, drop below 2% |
| `client.*.error_rate` (all endpoints) | 0 → 0 | 0 | | 1 | 0 | 0 | ✘ | | | | none: 0 errors in 55 801 requests |

**Note on criterion (d) for heartbeat p95.** The proper contrast is R0b, the same open-loop workload without
key growth, and it shows no trend there. R0 does show a small increase (10.5 → 41.8 ms, +13.7%). R0 is a
different regime: a closed loop near the write ceiling at about 52 writes/s, and its throughput also declined
by 4.7%. Its effect is an order of magnitude smaller than R1's +142%. The mechanism in R1 is visible directly:
- `instr.lock_wait_ms.upsert.max` rose by 216 ms/h (HR p ≈ 0; 10-minute medians 55 → 253 ms).
- `instr.upsert_ms.max` rose from 31 ms to 247 ms (10-minute medians).

Heartbeats queue on `_db_lock` behind the growing `/services` fetch. They are also slowed while holding the
lock, which is consistent with GIL contention from the per-row pydantic and JSON work on the read path (this
part is inference). This is the F4+F5 coupling the card describes.

**`client.services_ref200.latency_*` (point 2).** These are the internal fixed-size reference for 200 rows.
- In R1: p50 6.23 → 6.65 ms, +3.7% (HR p = 0.0096); p95 6.95 → 7.47 ms, +4.6% (HR p = 0.045).
- In R0b: p50 +4.2% (HR p = 0.049).

Both runs fail criterion (c), since the effect is below 10%. Criterion (d) also fails for p50, because the
same drift appears in the matched control, where nothing grows. Verdict: *statistically significant but
negligible*, and not related to the finding. It is a small drift of about 0.3–0.4 ms over an hour that is
common to both runs, with plausible sources in the host, the client CPU governor or the network.

**`services_ref4`.** It shows no trend (p50 HR p = 0.20; p95 HR p = 0.34). Its p50 has a non-monotonic step
from about 4 to about 12 ms between about 0.15 h and 0.75 h, then returns to about 4 ms. A
phase-alignment effect is plausible: the ref4 probe slots land behind the `services_grow` fetch on the lock
during that stretch. This is a hypothesis and not tested. Either way, the step is not a trend.

## 5. Server RSS growth (point 1): evidence versus hypothesis

The card predicted that RSS would *not* grow much. RSS nonetheless went from 55 to 107 MB (median of the
last 20 minutes: about 107–109 MB; peak 131 MB).

**Evidence (from `monitor.csv`, 5 s samples, offset-corrected):**
- **Shape: a rising sawtooth band, not a monotonic staircase.** Of 717 consecutive differences, 382 are
  increases, 262 are decreases and 73 are flat. The largest single drops were −17.7, −12.7 and −12.5 MB.
  RSS sat at its running maximum in only 25% of samples.
- **Per-5-minute envelope (min / median / max RSS, MB):**

| window | min | median | max |
|---|---|---|---|
| w0 | 54.1 | 56.9 | 60.6 |
| w2 | 65.3 | 73.0 | 80.0 |
| w4 | 82.0 | 90.2 | 95.6 |
| w6 | 90.7 | 95.7 | 103.3 |
| w8 | 94.5 | 107.8 | 118.3 |
| w9 | 103.8 | 107.1 | 117.8 |
| w10 | 101.3 | 109.1 | 125.8 |
| w11 | 102.4 | 109.4 | 131.1 |

- **It is decelerating and close to a plateau in its floor and median.** The linear RSS slope per 15-minute
  window was:

| window | slope |
|---|---|
| 0–15 min | 94 MB/h |
| 15–30 min | 93 MB/h |
| 30–45 min | 66 MB/h |
| 45–60 min | 17 MB/h |

  Over the last 20 minutes the floor stayed at about 101–104 MB and the median at about 107–109 MB. The
  ceiling kept rising (118 → 131 MB), tracking the per-call payload.
- **It tracks the `/services` payload and rows fetched.** Correlation between RSS and the running maximum of
  the `services_grow` response size is r = 0.94. A linear fit gives USS ≈ 50 MB + 18.6 × (response size in
  MB), or about 1.4 kB of USS per row (about 50 MB over about 36 k rows). By the end, rows fetched per call
  reached about 35 800, and each call built that many ServiceOut objects.
- **RSS − USS is constant** (11.0 → 11.3 MB), so the growth is private anonymous memory, not shared or
  file-backed pages. VMS moved by about +100 MB in steps (522 → 610 MB at about 15 min, then about 616–630 MB).
- **Thread and FD counts are flat:** threads 10–12, FDs 19–23.
- **The matched control R0b, with the same rates and probe schedule but fixed rows, is flat**
  (54.1 → 54.9 MB, +0.5%), and R0 is flat as well. The growth is therefore tied to row count, not to
  request volume or uptime.

**Hypothesis (not directly measured, since there is no heap or allocator instrumentation):**
- Each `/services` call materialises N rows three or four times: the sqlite tuples, the ServiceOut pydantic
  models, FastAPI's `jsonable_encoder` dicts, and the JSON bytes (3.2 MB at the end). That makes the transient
  working set O(N), several tens of MB at 36 k rows.
- CPython's allocator (pymalloc arenas plus glibc malloc arenas, with fragmentation and no trim) keeps much of
  that high-water mark resident between calls. That would produce the rising floor. Samples that land during
  a call (the handler is busy about 15% of the time at the end: about 290 ms every 2 s) would produce the
  sawtooth peaks.
- This is per-call peak memory scaling with an unbounded per-token result set (F5). It is not an independent
  leak of retained objects: the decreases show memory being returned or reused, and the floor is flattening.

**Does it count as an aging symptom under the criteria? Yes, as a secondary, mechanism-dependent symptom.**
- It satisfies (a) to (d): HR p = 3.3e-6, the direction is up, +62.5%, and it is absent in both controls.
- It is caused by the finding's resource: per-call footprint grows with row count.
- Because row count is unbounded, the per-call peak (the ceiling) has no bound either, even if the floor
  plateaus for a while. The card's "per-call peaks only" expectation was right about the cause but
  underestimated the size.
- **Caveat:** its trajectory is flattening (17 MB/h in the last 15 minutes). A run of about 2 hours would
  show whether the floor is bounded (allocator reuse) or keeps rising with N. It should not be reported as a
  classic leak, and the repair assessment should treat the expected outcome as "RSS bounded" rather than "RSS
  flat".

## 6. Alternative explanations

| explanation | status | evidence |
|---|---|---|
| Client saturation | **ruled out** | `loadgen.cpu_percent` median 4.0%, p95 4.4%, max 14.6%, on 4 CPUs. The +13.5% trend is +0.8 points absolute, from reading 3 MB responses. `loadgen.rss_mb` 36 → 49 MB. Achieved rates matched targets: grow 9.95/s (target 10), refresh 2.00/s (2), ref 2.00/s (2), each probe stream 0.50/s (0.5). 0 non-200 responses in 55 801 requests. Max latency 522 ms, far below the 30 s timeout, so no client timeouts. |
| Client CPU governor `powersave` | **ruled out as a trend source**; it affects absolute latency only | The fixed-size probes `services_ref4` and `services_ref200`, issued from the same client at the same rate, show no trend or < 5% drift, and R0b shows the same < 5% drift. |
| Host effects on the server (disk, other processes) | **ruled out** | Disk has 75 GB free and the db is 5.4 MB. Server system memory used went from 1.49 to 1.57 GB (flat context). Server CPU 4.2 → 21.8% (p95 26.7%, max 30.7%), which is the app's own read cost and stays far from saturating one core. The server was not CPU-pinned, but the controls ran on the same host with flat series. |
| Warm-up transient | **ruled out** | The analysis discards the first 60 s. Growth is linear for the whole hour (rows, response size and `services_grow` latency are near-perfect lines). |
| Instrumentation overhead | **ruled out** | `instr.snapshot.query_ms` rose from 0.5 to 4.0 ms (median 2.2 ms; one outlier at 205 ms), once every 10 s, about 0.04% of lock time. `instr.snapshot.lock_wait_ms` had median 0 and max 60 ms. Compare a `services_grow` latency increase of about 440 ms every 2 s and heartbeat p95 +170 ms. The probe adds under 1% of the effect. The probe's COUNT queries do grow with rows, but they are 2 orders of magnitude below the symptom. The same probe in R0b produced no trends. |
| Network | **ruled out** | RTT 1.2–1.9 ms and stable. Wired LAN. The ref probes are flat. |
| RSS growth caused by something other than rows (e.g. a probe or uvicorn leak) | **largely ruled out** | R0b is flat with identical probes and request volume. The precise allocator mechanism is **not established**; see §5. |

## 7. Extrapolation (marked as such)

These figures extend the linear Sen slopes (rows +36 k/h; `services_grow` p50 +468 ms/h; heartbeat p95
+203 ms/h).
- **At 2 h:** about 72 k rows, `/services` about 0.95 s and 6.4 MB per call, heartbeat p95 about 400 ms.
- **Beyond that:** the read path would hold the lock and the GIL for a growing fraction of each 2 s probe
  interval. At that point the paced grow stream would start dropping slots, and throughput loss would become
  visible.

These are extrapolations, not observations.

## Next step

**Proceed to repair.** The finding was activated in loop iteration 1 of 3, so no workload revision is
needed.

For the later repair assessment, re-run R1 with identical parameters and the same two-machine setup. Fixing
the client CPU governor to `performance` is preferable, provided it is applied to both before and after. The
repair is expected to bound rows per token, `/services` latency and response size, and `upsert` lock wait.
Server RSS is expected to be bounded, not necessarily flat. Also check for a constant-overhead regression on
`heartbeat` p50.
