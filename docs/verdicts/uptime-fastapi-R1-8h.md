# Verdict: uptime-fastapi-F4, loop iteration 1 (targeted_v1, 8 h, `--new-key-rate 2`)

**Verdict: ACTIVATED_WITH_AGING**

## Runs judged

| role | run | workload |
|---|---|---|
| targeted R1 | `runs/20261020-164758_uptime-fastapi_R1` | `uptime-fastapi-F4_targeted_v1.py --new-key-rate 2` |
| matched control R0b | `runs/20261020-084533_uptime-fastapi_R0b` | same workload and rates, `--key-space 4` |
| paper-workload control R0 | `runs/20261020-004305_uptime-fastapi_R0` | `uptime-fastapi-F4_control.py` (JMeter replica: 4 fixed services, closed loop) |

**Common setup for all three runs**
- 28 800 s each, seed 42, `workload_exit=0`.
- Server alive at stop, `server_exit=0`, `stop_reason=client`.
- Instrumented, with identical app hashes (`app.py 6799e8e1…`, `aging_probe.py 8127cc9a…`) and the same bundle `20261018-0051` on both machines.
- Two-machine mode. Server `cesar-pc` (4 CPUs, Linux 6.2, Python 3.10.12) started the app with its own `python app.py` (uvicorn, `0.0.0.0:5000`, single process). Client `pedal-lenovo`, wired LAN, RTT about 2 ms.
- Client preflight passed with 0 failures and the CPU governor set to `performance`. The campaign ran the three runs back to back with 120 s cooldowns.

**Method.** I regenerated `analysis/` for all three runs with `harness/analyze_run.py`, and every MK, Sen and change-% figure below comes from it. The per-hour medians and percentiles, the phase and overlap classification of requests, and the memory fits are descriptive. I computed them from the raw `client.csv`, `monitor.csv` and `instrumentation.jsonl` (server timestamps corrected by `clock.offset_s`).

## 1. Path executed: yes

| series | first → last | Sen /h | MK-HR p |
|---|---|---|---|
| `instr.calls.upsert_heartbeat` | 609 → 1.73e5 | | 1.6e-5 |
| `instr.calls.list_services_for_token` | 101 → 4.32e4 | | 1.4e-5 |
| `instr.fetch_rows.max` = `services.serialized_rows.max` | 200 → 57 590 per call | | 1.7e-6 |
| `instr.fetch_ms.max` (lock held in SELECT + fetchall) | 1.5 → 98 ms | +12.4 ms | 2.9e-10 |
| `instr.services.handler_ms.max` | 31 → 417 ms | +44.9 ms | 2.7e-15 |

- No `event` records appeared, and `errors.*_500` = 0.
- Hourly medians of `fetch_ms.max` were 7, 22, 47, 62, 81, 71, 82 and 94 ms.

## 2. Resource growth: meets the card's criterion

| series | first → last | Sen /h | change % | MK-HR p | final/initial |
|---|---|---|---|---|---|
| `instr.rows.max_per_token` | 200 → 57 590 | +7 200 | 199 | 1.7e-5 | **288×** |
| `instr.rows.total` | 339 → 57 800 | +7 200 | 198 | 1.7e-5 | **170×** |

The first value of `max_per_token` is the ref200 token, because the grow token passes it about 100 s into the run.

**Achieved new-key rate versus the target of 2/s**
- Client: `tenant-grow-1` sent 57 592 serviceIds and all 57 592 returned 200. Per hour that is 1.999, 2.000, 2.000, 2.000, 2.000, 1.999, 2.000 and 2.000 keys/s.
- Probe: rows grew at 7 200/h, which is exactly 2.0/s. `rows.max_per_token` at shutdown was 57 592, matching the client count.
- The db file reached 9.1 MB (2 225 pages × 4 kB), about 158 bytes per row, the same as in the pilot.

## 3. Control contrast: yes

- **R0:** `rows.total` constant at 4 (the card asks for ≤ 4), `db.bytes` constant at 16 kB, and `server.rss_mb` constant at 53.2 MB.
- **R0b:** `rows.total` constant at 208 (4 + 4 + 200 by design), `rows.max_per_token` at 200, and `db.bytes` at 53 kB.
- R0b has the same request rates, tokens, probe schedule and pacer phases as R1, and differs only in key diversity.

## 4. Symptoms (criteria (a) to (d))

| series (R1) | first → last | Sen /h | change % | MK-HR p | R0b | R0 | result |
|---|---|---|---|---|---|---|---|
| `client.services_grow.latency_p50_ms` | 9.9 → 725 ms | +89.9 | +181 | 5.2e-7 | no trend (−0.04%) | n/a | **SYMPTOM** |
| `client.services_grow.latency_p95_ms` | 32.6 → 768 ms | +89.5 | +172 | 1.5e-11 | no trend (−0.08%) | n/a | **SYMPTOM** |
| `client.services_grow.resp_bytes_mean` | 15.9 kB → 5.22 MB | +655 kB | +199 | 5.9e-6 | constant at 365 B | constant at 71 B | **SYMPTOM** |
| `server.watched_files_mb` (db) | 0.070 → 8.69 MB | +1.08 | +198 | 8.9e-7 | no trend | no trend | **SYMPTOM** |
| `client.heartbeat.latency_p95_ms` (secondary) | 37.6 → 247 ms | +13.7 | +91.7 | 0.0024 | no trend (−0.4%) | no trend | **SYMPTOM**, phase-sensitive (§6) |
| `server.rss_mb` | 54.1 → 145 MB | +11.1 | +87.3 | ≈0 | no trend (0%) | constant | **SYMPTOM** (§7) |
| `server.uss_mb` | 43.3 → 134 MB | +11.0 | +97.7 | ≈0 | +0.39% (negligible) | no trend | **SYMPTOM** (§7) |
| `client.services_ref4.latency_p50_ms` (cross-token) | 4.5 → 49.9 ms | +0.24 | +43.1 | 0.0030 | +0.8% (negligible) | n/a | meets (a)–(d): **secondary, phase-gated, cross-tenant** (§5) |
| `client.services_ref4.latency_p95_ms` | 4.65 → 94.2 ms | +1.32 | +220 | 0.010 | +1.4% (negligible) | n/a | same as p50 |
| `client.services_ref200.latency_p50_ms` | 6.61 → 6.71 ms | +0.005 | +0.6 | 0.0094 | no trend | n/a | significant but negligible; fails (c) |
| `client.services_ref200.latency_p95_ms` | 7.17 → 7.29 ms | −0.012 | −1.3 | 5e-7 | no trend | n/a | not degrading; fails (b) and (c) |
| `client.heartbeat.latency_p50_ms` | 25.0 → 23.4 ms | −0.45 | −14.6 | 0.0053 | no trend | no trend | wrong sign, fails (b); workload-phase artifact (§6) |
| `client.heartbeat.throughput_rps` | 6 → 6 | 0 | 0 | 0.57 | constant | no trend | none: paced open loop, not saturated (§8) |
| `client.*.error_rate` | 0 | | | 1 | 0 | 0 | none: 0 non-200 responses in 216 172 requests |

**Comparison with the pilot.** Per-row costs agree with the pilot (1 h at 10 keys/s, 35.8 k rows):

| measure | pilot | R1 |
|---|---|---|
| `/services` p50 per row | 13.2 µs/row (473 ms) | 12.6 µs/row (725 ms at 57.6 k rows) |
| response bytes per row | 89 B | 91 B |

The `services_grow` latency is therefore a function of rows per token, not of elapsed time or insert rate. Hourly p50 values were 57, 156, 250, 343, 437, 527, 607 and 685 ms. The series is linear and shows no saturation knee.

**Other series.**
- `server.cpu_percent` went from 2.2 to 30.3% (+184%, HR p ≈ 0). This is the CPU cost per `/services` call growing with N, and matches the triage card's expected symptom. R0b shows no trend.
- `instr.upsert_ms.max` has no trend (HR p = 0.43).

## 5. Point 1: cross-token degradation (`services_ref4`) and the triage card's open question

**How the probe streams are timed** (from `targeted_v1.py` and confirmed in `client.csv`):
- All pacers start at the same `t0`.
- The three probe streams are staggered inside the 2 s interval: `services_grow` at +0.003 s, `services_ref4` at +0.667 s, `services_ref200` at +1.335 s.
- The three heartbeat streams (grow, refresh and ref, at 2/s each) all fired on the same 0.5 s grid at +0.002 s. They coincided with each other and with the grow probe until 5.34 h.

**ref4 latency by class.** Each ref4 request was classified by what it overlapped (per hour, n = 1 800):

| hour | grow p50 | clear: n, median | coincides with a heartbeat (±5 ms): n, median | sent while a grow request is in flight: n, median |
|---|---|---|---|---|
| 0–4 | 57–437 ms | 1 800, **4.3–4.4 ms** | – | 0–1 |
| 5 | 527 ms | 606, 4.4 ms | 1 191, 5.7 ms | 3 (the stall) |
| 6 | 607 ms | 0 | 1 798, 5.8 ms | 2 |
| 7 | 685 ms | 0 | 518, 5.6 ms | **1 281, 32.8 ms** |

**Mechanism, from the evidence**
1. **ref4's own query cost did not grow.** Whenever nothing else was in flight, ref4 stayed at 4.3–4.4 ms for the whole run, while `rows.total` grew 170× (to 57.8 k). ref200 (200 rows, larger fetch) was flat at 6.6 ms all run (p50 +0.6%).
2. **Step at 5.34 h: collision with a heartbeat.**
   - At 5.34 h a single 2.2–2.5 s stall slowed every stream at once (heartbeat max 2 238 ms, grow 2 517 ms, ref4 1 694 ms, ref200 1 606 ms).
   - The heartbeat pacers drop missed slots and re-phase to `now`. The ref heartbeat stream moved to +0.168 s mod 0.5 s, and grow and refresh moved to +0.489 s.
   - The ref heartbeat now lands at +0.668 s, on top of the ref4 probe. ref4 then waits behind one upsert commit on `_db_lock` (about 10 ms): median 4.4 → 5.7 ms, p95 4.8 → 15.9 ms.
3. **Step at hour 7: overlap with the grow request.**
   - The grow `/services` request crossed the 0.667 s probe offset (hour-7 p50 685 ms, last window 725 ms). From then on, most ref4 requests arrive while the grow request is still in flight, and their median rises to 32.8 ms. That is roughly the remainder of the grow request: 725 − 667 ≈ 58 ms at the end.
   - The grow fetch itself (`fetch_ms` ≈ 94–98 ms) finished about 0.57 s before ref4 arrived. The delay ref4 sees in hour 7 is therefore **not** `_db_lock` contention.
   - `lock_wait_ms.list.max` stays ≤ 40 ms (hour-7 median 22 ms, p95 38 ms), while ref4 p95 reaches 73–94 ms.
   - The rest of the grow request (`handler_ms` up to 417 ms, of which 98 ms is under the lock) is spent outside the lock: building 57 k `ServiceOut` objects in the threadpool (holding the GIL), then FastAPI's `field.validate` (threadpool) and `field.serialize` / `JSONResponse` rendering on the **event-loop thread**. Those last two steps add about 300 ms (725 − 417).
   - While the loop and GIL are busy with the 5.2 MB response, every other request is head-of-line blocked, whichever token it carries. That serialization runs on the event-loop thread is read from FastAPI's `routing.serialize_response`. It is an inference about where the time goes, not a probe measurement.
4. **ref200 is next.** ref200 sits at +1.335 s, so it would be hit only once grow latency exceeds about 1.33 s. Extrapolating +90 ms/h, that happens at about 15 h. This is an extrapolation.

**Does it qualify as a symptom?**
- By the letter of (a)–(d), yes: HR p = 0.003, latency up, +43% (p50) and +220% (p95), and R0b +0.8%.
- It is a real cross-tenant degradation caused by the finding's resource: a tenant with 4 services gets slower because another tenant's row set is unbounded. It belongs to the same F4+F5 coupling as the heartbeat tail.
- I count it as a **secondary, phase-gated symptom**, not as a trend measurement:
  - its onset time and size are set by the deterministic probe offsets and by one random re-phase event;
  - its Sen slope (+0.24 ms/h) describes two steps, not a gradual cost;
  - its slope must not be used to extrapolate.
- Under random (Poisson) arrivals, the expected extra delay for an unrelated request is about P(overlap) × E[residual] ≈ (L/2 s) × (L/2), with L the grow-request time. That grows as N², but this is a model, not a measurement.

**Answer to the triage card's open question** (do OTHER tokens' rows degrade a token's `/services`?):
- **Direct per-query effect: no, at this scale and with this plan.**
  - `EXPLAIN QUERY PLAN` on a scratch copy of R1's final `db.sqlite3` gives `SEARCH heartbeats USING INDEX idx_heartbeats_token (token=?)` + `USE TEMP B-TREE FOR ORDER BY`.
  - Offline timings on that copy (connect + SELECT + fetchall): ref4 0.14 ms, ref200 0.28 ms, grow 58 ms. Per-token cost is O(n_token · log n_token), independent of other tokens' rows.
  - The runtime data agrees: the clear-path ref4/ref200 latency was flat while the table grew 170×.
- **Indirect effect: yes.**
  - Other tokens' *requests* are delayed by the large token's read through the shared process. Before 5.34 h this went through `_db_lock` (fetch); afterwards, and dominantly, through the GIL and event loop (pydantic and JSON).
  - The growing *row set* of other tokens has no effect on its own. The growing *response* of another token being served concurrently does.
- **What cannot be concluded**
  - The EXPLAIN ran with the analysis machine's SQLite 3.45.1. The server's SQLite version (Ubuntu 22.04 system library) was not recorded. The plan is heuristic: there is no `sqlite_stat1`, and `ANALYZE` was never run.
  - The plan could differ with other SQLite versions, after `ANALYZE`, or with very low token selectivity. Example: workload option (b) of the triage card, many tokens with few rows each, which this run did not exercise (3 tokens only).
  - B-tree depth growth of `idx_heartbeats_token` (log N) is below the resolution of these probes. The +0.6% drift on ref200 matches the drift seen in the controls and the pilot.

## 6. Point 2: series moving the "wrong" way

### `client.heartbeat.latency_p50_ms` decreasing (25.0 → 23.4 ms, HR p = 0.005) while p95 rises (37.6 → 247 ms)

**The heartbeat distribution is bimodal by construction.**
- The three heartbeat streams share one 0.5 s grid, so each slot sends three upserts at once. They serialize on `_db_lock`, with about 10 ms of commit each (R0, single stream, has p50 12.8 ms). The median request is the second in line, about 24 ms. R0b shows exactly this: p50 24.0 ms flat for 8 h.
- In each 2 s cycle, a quarter of the heartbeats (one slot of four, about 25–31%) are sent within 10 ms before, or during, a grow `/services` request. They queue behind it.

Heartbeat latency by group (descriptive):

| hour | other heartbeats: median / p95 | sent with or during a grow request: median / p95 |
|---|---|---|
| 0 | 24.4 / 36.0 | 45.1 / 102 |
| 2 | 25.0 / 39.4 | 24.6 / 161 |
| 4 | 25.3 / 43.4 | 24.4 / 197 |
| 5 | 16.0 / 34.6 | 44.4 / 192 |
| 6 | 14.9 / 25.4 | 117.8 / 148 |
| 7 | 15.2 / 25.5 | 153.4 / 245 |

**Reading the p50 and p95**
- The p50 is always inside the unaffected group, and the p95 is always inside the affected group, because more than 5% of requests are affected.
- **p50 decrease.** The 5.34 h stall re-phased the pacers. The ref stream (+0.168 s) separated from grow and refresh (+0.489 s), so most slots now carry one or two upserts instead of three. The ref heartbeat p50 fell from 24.9 to 14.4 ms, refresh from 25 to 16 ms, and grow (still paired with refresh) stayed at 23.8 ms. Overall p50 went from 25 to 20.3 ms in the half-hour after the stall. It was 23.4 ms in the final window, because the affected share rose to 31% as grow latency crossed the +0.489 s slot.
- This is a self-collision artifact of the workload's phase alignment, not an improvement of the app. R0b has no stall, keeps its phases, and is flat at 24 ms.
- **Decision:** not a symptom. It fails (b), and is explained.
- **p95 increase.** The affected group's latency tracks the grow request's cost. Before 5.34 h it went through `_db_lock`: the co-sent heartbeats either win the race to the lock (median about 24 ms in hours 2–4) or lose it and wait for the fetch (p95 161–197 ms). After 5.34 h the affected slot at +0.489 s falls inside the pydantic and serialization phase of the grow request (GIL and event loop), and its median rises 44 → 118 → 153 ms.
- **Decision:** a symptom, satisfying (a)–(d), and the card's secondary heartbeat symptom. Its hourly path is non-monotonic (p95 78, 134, 119, 45, 166, 97, 133, 208 ms), because which slot overlaps the grow request depends on phase. Its exact magnitude is not a stable measurement.

### `instr.lock_wait_ms.max` "decreasing" (first 29.9, last 39.4, Sen −8.6 ms/h, HR p = 0.046) and `lock_wait_ms.upsert.max` (−8.9 ms/h, HR p = 0.046)

**Hourly medians**

| series | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| `lock_wait_ms.max` (ms) | 69 | 127 | 71 | 84 | 105 | 12.5 | 11.8 | 24 |
| `lock_wait_ms.list.max` (ms) | 24 | 29 | 29 | 29 | 29 | 11 | 10 | 22 |

R0b stays flat at 23–27 ms. **The negative slope is the regime change at 5.34 h.**
- Before, one heartbeat triple per 2 s was sent at the same instant as the grow probe, so the max window captured an upsert waiting behind the whole fetch plus the other upserts (up to 175 ms at p95).
- After the re-phase, no heartbeat coincides with the grow fetch (it ends about 0.1 s after the probe). Max waits drop to about 12 ms (one commit), then rise again in hour 7 (24–39 ms). By then the ref4 and heartbeat lock acquisitions are slowed by GIL contention with the grow request's pydantic phase (inference).

**Endpoints**
- `first` is the first snapshot after warm-up.
- `last` (39.4 ms) is the shutdown snapshot. In this run it is a partial window of about 4.9 s (`window.calls.upsert_heartbeat` = 20 instead of 60), not a zero as in the pilot.
- Neither endpoint is representative, and the "decreasing" label is not a property of the app.

**Decision:** not a symptom (it fails (b)), and it is a workload-phase artifact. Window-max lock wait is not a valid monotonic indicator under a deterministic, phase-locked schedule. The lock *hold* cost (`fetch_ms.max`, +143%, monotonic) is the valid server-side measure, and it grows as expected.

## 7. Point 3: memory

**Numbers**
- RSS: 54.1 → 145 MB (+87.3%). USS: 43.3 → 134 MB (+97.7%). Both HR p ≈ 0.
- R0b: RSS 54.3 → 54.4 MB, USS +0.39%. R0: RSS constant at 53.2 MB.
- RSS − USS stays at 10.9–11.3 MB the whole run, so the growth is private anonymous memory.
- Threads and FDs are flat (11 and 20–22), apart from the stall below.

**Hourly RSS envelope (MB)**

| hour | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| min | 53.6 | 70.2 | 85.4 | 92.8 | 100.7 | 108.0 | 127.6 | 134.9 |
| median | 62.9 | 75.2 | 91.1 | 97.9 | 105.1 | 121.4 | 132.7 | 141.9 |
| max | 70.9 | 86.2 | 98.1 | 105.0 | 115.2 | 136.2 | 146.3 | 155.8 |

**It is not levelling off.** The floor (15-minute minima) rose at:

| span | floor slope |
|---|---|
| 0–2 h | 13.5 MB/h |
| 2–4 h | 7.1 MB/h |
| 4–6 h | 13.9 MB/h (includes a +12 MB step at 5.34 h) |
| 6–8 h | 9.3 MB/h |

The last 2 h still rose 127.6 → 143.3 MB, so the floor, the median and the ceiling all grow for the whole 8 h. The pilot's slowdown to 17 MB/h in its last 15 minutes was not a plateau.

**It is a function of rows per token, not of time.**
- Correlation between RSS and the running maximum of the `services_grow` response size is r = 0.992 (USS r = 0.986).
- The linear fit is USS ≈ 47 + 18.3 × (response MB) over 0–4 h and 41 + 18.5 × (response MB) over 4–8 h. The slope is the same as the pilot's (18.6).
- At 36 k rows R1 had RSS median about 105 MB (hour 4), against the pilot's 107 MB at 36 k rows after 1 h. Same N gives the same memory, at a fifth of the insert rate.
- At the end, USS growth is about 91 MB for a 5.22 MB response: about 17× the response size, or about 1.6 kB per row (pilot: 1.4 kB per row).

**The sawtooth is still there.**
- Of 5 748 consecutive differences, 3 534 are up, 1 618 down and 596 flat. The largest drops are about −11 MB.
- RSS sits at its running maximum in only 15.5% of samples.
- Memory is returned or reused between calls, but the high-water floor rises with each larger response.

**Stall at 5.34 h.** The threadpool grew from 11 to 14 threads, VMS rose 560 → 776 MB (+216 MB, consistent with three new glibc malloc arenas plus stacks), and RSS took a lasting step of about +12 MB.

**Hypothesis (not measured; there is no heap instrumentation).** Same as the pilot:
- The O(N) transient working set per `/services` call (sqlite tuples, `ServiceOut` models, FastAPI-encoded structures, 5.2 MB JSON) is retained as allocator high-water memory: pymalloc arenas and glibc arenas that are not trimmed.
- It is per-call peak memory scaling with an unbounded result set (F5), not an independent leak of retained objects. Because N is unbounded, the floor is unbounded too.

**Decision:** a symptom, satisfying (a)–(d). The card's "RSS not expected to grow much" was wrong about size but right about cause. For the repair assessment, the expectation is that RSS is bounded once rows per token are bounded.

## 8. Point 4: did the targeted run approach saturation? No

- **New-key rate.** It held at 2.000/s in every hour (§2). The grow pacer lost slots only during the 2.5 s stall at 5.34 h.
- **Probe streams.** Each probe stream completed 14 399 or 14 398 requests (0.500/s), with no inter-request gap above the slot interval.
- **Errors and timeouts.** 0 errors. Maximum client latency was 2 517 ms (the stall), far below the 30 s timeout.
- **`/services` latency versus the 2 s probe interval.** p50 was 725 ms and p95 768 ms in the last window, about 36% of the interval. The probe worker is closed loop with one request in flight, so it would start dropping slots only above 2 s. Extrapolating +90 ms/h, that is about 14 h beyond the end of this run, around 22 h of total run time. This is an extrapolation.
- **Lock occupancy.** This is a rough estimate from probe maxima and R0 per-upsert cost, not a direct measurement:
  - upserts: 6/s × about 10 ms ≈ 60 ms/s;
  - grow fetch: 0.5/s × about 94 ms ≈ 47 ms/s;
  - ref fetches: about 2 ms/s;
  - snapshot: 7 ms every 10 s.
  - The total is about 11% at the end, against about 6–7% in R0b.
  - The process is busier than the lock: the grow request occupies the main thread and GIL for about 36% of wall time. Server CPU was 34.5% (median, hour 7), with p95 42%.
  - There is headroom on the server, which was not CPU-pinned and has 4 CPUs.
- **Client.** `loadgen.cpu_percent` 1.8 → 3.0% (hour-7 median 2.8%, max 3.6%), and `loadgen.rss_mb` 36 → 65 MB from holding 5 MB responses. Client saturation is not a factor.
- **Conclusion.** Choosing 2 keys/s for 8 h worked: the run stayed in the linear regime, and throughput loss is not yet visible. It would show up only near the 2 s probe ceiling, as the pilot also predicted.

## 9. Point 5: clock drift in R0

- R0's `clock` block: offset 0.401 s at start and 0.003 s at end (drift −0.399 s). `analyze_run.py` subtracts the single mean offset of 0.202 s.
- The preflight had measured a 400.5 ms offset just before R0, so the server clock was being slewed back during R0. R0b and R1 have offsets of 7.6 and 10.1 ms, with drift ≤ 10 ms.
- The residual misalignment of R0 server series is at most ±0.2 s, at the two ends of an 8 h run. That is small compared with the 5 s monitor interval, the 10 s snapshots and the 60 s client windows, so no sample moves to another window.
- Every R0 server-side series is constant or has no trend (rows 4, RSS 53.2 MB, `lock_wait`, `fetch_ms`), so alignment cannot create or hide a trend.
- Durations inside the probe use `perf_counter`. A 0.4 s slew over 8 h is 14 ppm, which is negligible for durations.
- None of the phase analyses above use R0.
- **Conclusion:** no effect on the verdict. The R1 and R0b offsets are also negligible against the overlap windows (tens to hundreds of ms) used in §5–§6.

## 10. Alternative explanations

| explanation | status | evidence |
|---|---|---|
| Client saturation | **ruled out** | §8: loadgen CPU ≤ 3.6%, all target rates met, 0 errors, no timeouts. Client governor `performance`. |
| Network | **ruled out** | RTT 1.8–2.3 ms in all three runs. ref4 and ref200 clear-path latency flat at 4.4 and 6.6 ms. |
| Host effects on the server | **ruled out for the trends**; one stall **not explained** | Server `sys_mem_used` flat at 1.2–1.3 GB. db only 9.1 MB. The server disk itself is not in `system.txt`, which describes the client, but 9 MB cannot matter. The 2.5 s stall at 5.34 h hit all streams at once. Its cause (host hiccup, GC, I/O) is not identifiable from the data. It did not create the trends: rows, response size, `services_grow` latency and RSS are linear before and after it. It did re-phase the pacers, which explains the heartbeat p50 and lock-wait "decreases" and part of the ref4 step. |
| Warm-up transient | **ruled out** | First 60 s discarded. Growth is linear over 8 h. |
| Instrumentation overhead | **ruled out** | `snapshot.query_ms` 0.4 → 7.3 ms every 10 s (0.07% of wall time). `snapshot.lock_wait_ms` median 0. The same probes in R0b produce no trends. |
| Workload phase artifacts | **identified**, and they affect only secondary series | They affect heartbeat p50, `lock_wait_ms.max`, the shape of heartbeat p95 and the shape of ref4. The primary symptoms (`services_grow` latency and size, db size, RSS) are monotonic and phase-independent. |
| Query-plan dependence on other tokens' rows | **ruled out at this scale** | §5: index plan, clear-path ref latency flat. |
| RSS growth not caused by rows | **largely ruled out** | R0b is flat with identical request volume and probes, and USS is linear in response size. The allocator mechanism is a hypothesis. |

## Next step

**Proceed to repair.** The finding is activated with aging in loop iteration 1 of 3, and confirmed over 8 h. No workload revision is needed.

**For the repair assessment**
- Re-run R1 with identical parameters: `--new-key-rate 2`, 28 800 s, seed 42, same two machines and same app start method.
- **Primary comparison series:** `rows.max_per_token`, `services_grow` latency and response size, `watched_files_mb`, `fetch_ms.max`, RSS and USS. The expectation is "bounded", not "flat".
- **Compare these by group, not by raw percentile:** heartbeat p95 and ref4. Use requests overlapping the grow request versus clear ones, as in §5–§6. A single random re-phase event changes their raw percentiles independently of the app.
- **Ignore for this purpose:** heartbeat p50 and `lock_wait_ms.max`, which are phase-dominated.
- **Check the regression:** a constant overhead on heartbeat p50, against R0b's 24 ms baseline.
- **Optional, for a later study of the cross-tenant effect:** jitter the probe and heartbeat send times, for example Poisson arrivals. The interference would then show up as a smooth function of the grow-request time rather than as phase-gated steps. Do not change the workload between before and after.
