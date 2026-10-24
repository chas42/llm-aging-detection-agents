# Verdict: bax-fastapi-credit-card-F1, loop iteration 1 (targeted_v1, 8 h, `--new-pair-rate 10`)

**Verdict: ACTIVATED_WITH_AGING**

**Sub-classification (aging vs by-design growth): MIXED.** The data itself grows by design: stored associations, db file size, and the size of the hot `/retrieve_cards` response, which the spec requires to list every matching card. That growth is not a defect. On top of it the app has aging-relevant defects whose cost grows with the stored data:

- an unpaginated read whose CPU cost per request is about 2.7× what it needs to be and grows linearly;
- transient memory peaks that rise with response size;
- whole-process blocking, about 150 ms per hot request at 8 h, that delays unrelated requests (other phones, writes).

A repair can reduce the per-row cost and remove most of the cross-request interference without breaking the spec. It cannot bound the hot request's latency, response size, db size or memory peak unless the spec changes (a default limit, or a cap on cards per phone). The app goes to repair with that limited goal (§9), and the expected best outcome is REPAIR_PARTIAL.

## Runs judged

| role | run | workload |
|---|---|---|
| targeted R1 | `runs/20261022-171645_credit-card-fastapi_R1` | `bax-fastapi-credit-card-F1_targeted_v1.py` (defaults: 10 new pairs/s on one hot phone, refresh 2/s, ref 2/s, 3 probes every 2 s) |
| matched control R0b | `runs/20261022-091419_credit-card-fastapi_R0b` | same script and rates, `--key-space 4` |
| paper-workload control R0 | `runs/20261022-011151_credit-card-fastapi_R0` | `bax-fastapi-credit-card-F1_control.py` (JMeter replica: closed loop, one fixed pair) |

**Common setup for all three runs**
- 28 800 s each, seed 42, `workload_exit=0`, server alive at stop, `server_exit=0`, `stop_reason=client`.
- Instrumented, with identical app hashes (`app.py 8bf95147…`, `aging_probe.py f7c7dc2b…`) and bundle `20261018-0051` on both machines.
- Two-machine mode. Server `cesar-pc` (4 CPUs, Linux 6.2, Python 3.10.12; CPU model and SQLite version not recorded) started the app with its own `python app.py` (uvicorn on `0.0.0.0:5000`, one process, access log on). Client `pedal-lenovo` (i5-7200U).
- Clock offsets were ≤ 9 ms with drift ≤ 14 ms. RTT was 1.7–2.2 ms.
- The campaign ran R0 → R0b → R1 back to back with 120 s cooldowns.

**Method**
- I regenerated `analysis/` for all three runs with `harness/analyze_run.py`. Every MK, Sen and change-% figure below comes from it.
- The hourly percentiles, the classification of requests by overlap with the hot request, and the memory regressions are descriptive. I computed them from the raw `client.csv`, `monitor.csv` and `instrumentation.jsonl`, with server timestamps corrected by `clock.offset_s`.
- I ran one offline check on a **scratch copy** of R1's final `db.sqlite3` on the analysis host, and deleted the copy afterwards: `EXPLAIN QUERY PLAN` plus timings. No app or server was started.
- Growth figures use `rows.associations` and `rows.max_per_phone`, not `seq.associations`. `seq.associations` reached 403 237, the number of associate calls, because AUTOINCREMENT advances on ignored inserts too.

## 1. Path executed: yes

| series | first → last | MK-HR p |
|---|---|---|
| `instr.associate.inserted` (rowcount = 1) | 869 → 288 060 | 2.6e-5 |
| `instr.associate.ignored` (rowcount = 0: refresh and ref) | 266 → 115 177 | 2.5e-5 |
| `instr.calls.retrieve_cards` | 97 → 4.319e4 | 2.5e-5 |
| `instr.retrieve.rows.max` (rows per call) | 660 → 2.878e5 | 2.5e-5 |
| `instr.retrieve.db_ms.max` | 3.5 → 1 319 ms | ≈0 |

- 71.4% of associate calls inserted a new row, which is "most calls" as the card requires. The other 28.6% are the designed refresh and ref streams.
- The hot retrieve query ran with the number of matched rows recorded on every call.
- No `event` records, `errors.validation_400` = 0, `retrieve.not_found` = 0.

## 2. Resource growth: meets the card's criterion

| series | first → last | Sen /h | change % | MK-HR p | final/initial |
|---|---|---|---|---|---|
| `instr.rows.associations` | 869 → 288 060 | +35 990 | 200 | 2.6e-5 | 331× |
| `instr.rows.max_per_phone` | 665 → 287 856 | +35 990 | 200 | 2.6e-5 | 433× |
| `instr.db.main_bytes` | 0.17 → 44.7 MB | +5.59 MB | 200 | 1.3e-7 | 260× |
| `server.watched_files_mb` | 0.148 → 42.6 MB | +5.33 | 199 | ≈0 | |

**Rates**
- Rows grew 35 990–36 010 per hour in every hour, linear with no decrease, as the card requires.
- Rows returned per hot probe went from hundreds to 288 k, beyond the card's "tens to tens of thousands".
- **Achieved new-pair rate.** The grow stream sent 287 856 pairs and all 287 856 got 201. Per hour that is 9.989, 9.998, 9.995, 9.996, 9.996, 9.996, 9.995 and 9.996 pairs/s, against a target of 10.
- At shutdown `rows.max_per_phone` was 287 856, exactly the client count.

**Size against prediction**
- The db file reached 44.7 MB (10 915 pages × 4 kB, freelist 0), about 155 B per row. The calibration predicted about 131 B per row and 38 MB.
- The 18% excess fits page splits from inserting unsorted card numbers into the UNIQUE(credit_card, phone) and `idx_assoc_card` B-trees, whose pages end up partly empty. This is an inference: page fill was not measured.
- The hot response reached 5.45 MB (about 19 B per card), as predicted.

## 3. Control contrast: yes

- **R0b:** `rows.associations` constant at 208 (4 hot + 4 + 200 by design), `rows.max_per_phone` at 200, `db.main_bytes` at 61 kB, `retrieve.db_ms.max` 1.3–2.2 ms, `snapshot.query_ms` 0.8 ms flat. `server.rss_mb` went 54.0 → 55.1 MB (+0.92%: significant but negligible).
- **R0:** rows constant at 1, `server.rss_mb` constant at 53.2 MB, and every client series flat (retrieve p50 4.18 → 4.17 ms) at about 103 requests/s.
- **Control A** of the card (same rate, repeated pairs) is R0b, and it is satisfied.
- **Control B** of the card (retrieve-only load on a pre-seeded table) was not run. Two things partly substitute for it: R1's own ref4 and ref200 streams, whose latency stays flat when nothing else is in flight (§6), and the offline timing on the final db (§5).

## 4. Symptoms (criteria (a) to (d))

| series (R1) | first → last | Sen /h | change % | MK-HR p | R0b | R0 | result |
|---|---|---|---|---|---|---|---|
| `client.retrieve_hot.latency_p50_ms` | 9.82 → 1 270 ms | +160 | +201 | 4.9e-15 | −25.9% (decreasing, §8) | n/a | **SYMPTOM** |
| `client.retrieve_hot.latency_p95_ms` | 11.5 → 1 330 ms | +165 | +200 | ≈0 | −28% (decreasing) | n/a | **SYMPTOM** |
| `client.retrieve_hot.resp_bytes_mean` | 16.5 kB → 5.45 MB | +684 kB | +199 | 2.3e-9 | constant at 94 B | constant at 37 B | **SYMPTOM** (required by the spec, §9) |
| `server.watched_files_mb` | 0.148 → 42.6 MB | +5.33 | +199 | ≈0 | no trend | no trend | **SYMPTOM** (data growth by design) |
| `server.rss_mb` | 54.4 → 113 MB | +4.32 | +41.5 | 9.3e-12 | +0.92% (negligible) | no trend | **SYMPTOM**: high-water from transient peaks, not retained (§7) |
| `server.uss_mb` | 43.2 → 101 MB | +4.17 | +46.5 | 2.1e-11 | +0.54% (negligible) | +0.02% | **SYMPTOM**, same reading |
| `client.associate_card.latency_p95_ms` | 46.2 → 84.0 ms | +6.64 | +127 | 4.5e-5 | −10.2% (warm-up, §8) | −0.7% | **SYMPTOM**, secondary: interference from the hot request (§6) |
| `client.retrieve_ref4.latency_p95_ms` | 5.04 → 48.5 ms | +5.20 | +734 | 5.2e-4 | +0.07%, no trend | n/a | meets (a)–(d): **secondary, phase-gated** (§6) |
| `client.retrieve_ref4.latency_p50_ms` | 4.70 → 22.2 ms | +0.09 | +15.0 | 3.3e-4 | −1%, no trend | n/a | meets (a)–(d), same caveat; the hourly median stayed 4.5–6.4 ms |
| `instr.associate.db_ms.max` (server side) | 44.7 → 136 ms | +15.2 | +192 | 3.4e-9 | −18.9% | no trend | supports §6 (stalls inside the DB phase) |
| `server.cpu_percent` | 4.8 → 77.5% | +7.82 | +184 | ≈0 | no trend | no trend | cost indicator, see §5 |
| `client.associate_card.latency_p50_ms` | 17.0 → 15.5 ms | −0.09 | −4.6 | 2.2e-16 | −1.3% | −0.5% | fails (b) and (c); explained in §6 |
| `client.retrieve_ref200.latency_p50_ms` / `p95` | 5.27 → 5.75 / 5.62 → 6.49 ms | −0.06 / −0.05 | −9.4 / −6.9 | 5.7e-5 / 1.7e-3 | no trend | n/a | not degrading; fails (b) |
| `client.*.throughput_rps` | 14 / 0.5 / 0.5 / 0.5 | 0 | 0 | ≥0.56 | | | none: every target rate met (§5) |
| `client.*.error_rate` | 0 | | | 1 | 0 | 0 | none: 0 non-2xx in 446 429 requests |

The card's fifth expected symptom ("slight, logarithmic increase in associate latency") does **not** appear on the write path itself. Associate requests not overlapping the hot request kept a 16.5 ms median and a p95 of 19–31 ms. The associate p95 that rose is interference (§6), not B-tree depth.

**Hourly hot-probe latency (client):**

| hour | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| p50 (ms) | 79 | 224 | 382 | 547 | 700 | 852 | 1 036 | 1 188 |
| p95 (ms) | 146 | 303 | 457 | 614 | 793 | 954 | 1 129 | 1 301 |
| mean response | 0.34 MB | 1.03 | 1.71 | 2.39 | 3.08 | 3.76 | 4.44 | 5.13 |

The series is linear, about +155–185 ms/h, with no knee.

## 5. Point 2: did the run approach saturation? It got close on one stream but did not reach it

**Achieved rates**
- Grow: 9.99–10.00 pairs/s in every hour (§2).
- All associates: 14.0/s, Sen 0.
- Each probe stream completed 14 397–14 398 requests (0.500/s) with a maximum gap of 2.0 s, so no probe slot was dropped.
- The per-10 s probe window counted 140–141 associates and 15 retrieves in every hour.

**Errors and timeouts**
- 0 errors in all streams.
- The highest client latency was 2 067 ms (one associate during a stall, below), far below the 30 s timeout.

**Hot probe against its 2 s interval**
- Hour-7 p50 was 1 188 ms (last window 1 270 ms), which is **59–64% of the interval**. p95 was 1 301 ms (65%) and the maximum 1 525 ms (76%).
- The probe worker is closed loop with one request in flight, so it starts dropping slots only above 2 s.
- **Extrapolation:** at +160 ms/h that happens about 4.6 h after the end of this run, around 12.5–13 h of run time. Throughput loss on the probe would follow.

**Server CPU** (`psutil`, 100% = one core)
- Hourly medians were 6, 13, 21, 29, 37, 45, 53 and 60%, rising to 64.6% in the last half-hour. The hour-7 p95 was 72% and the maximum 77.5%.
- The Python part of the process is effectively capped at one core by the GIL. On that core about 35% headroom is left. The host has 4 CPUs, so the machine as a whole is about 20% busy.
- The hot request alone occupies the process about 60% of wall time at the end, since one probe every 2 s takes 1.19–1.27 s.

**Late-run saturation effects**
- Throughput showed none.
- They do show in the tails of other requests (§6). The share of associate requests that overlap a hot request rose from 7.8% (hour 0) to 56.9% (hour 7). The blocking tail of each hot request grew to about 150 ms, which is about 7.5% of wall time with the whole process stalled.
- Writer path: associate p50 stayed at 16.1–16.8 ms, and `associate.db_ms` max rose only through interference.

**Client**
- `loadgen.cpu_percent` was 3.4 → 4.4% (maximum 5.2% after warm-up). `loadgen.rss_mb` rose 36 → 58 MB from holding the 5 MB responses.
- The client was not saturated.

**Two stalls not explained by the app**
- At 3.37 h and 4.36 h, three concurrent associate requests (grow, refresh, ref) took 1.74–2.07 s while the hot retrieves around them were unaffected.
- Only writes stalling fits SQLite's busy-wait on the write lock (Python default `timeout=5 s`) behind a slow commit or checkpoint, for example a disk hiccup on the server. That is a hypothesis.
- R0b, with no growth, had similar isolated associate maxima (3 452 ms in hour 0, 1 249 ms in hour 6), so they are a host or SQLite effect, not the finding.
- Both stalls re-phased the refresh and ref pacers: from +0.28 s mod 0.5 s to +0.14 and +0.09 s. This explains the ref200 and ref4 p95 bump to 12–13 ms during 3.4–4.4 h, a phase artifact like the one in the Uptime run.

## 6. Point 3: cross-request interference (ref4 and associate p95)

**How the probe streams are timed** (`targeted_v1.py`, confirmed in `client.csv`)
- The three probes are staggered inside each 2 s cycle: hot, then ref4 at **+0.667 s**, then ref200 at **+1.333 s**. Their offsets were constant for the whole run (median 0.666–0.667 s and 1.332–1.333 s after the hot send).
- Grow associates arrive every 0.1 s.

**Evidence 1: overlap decides everything.** Requests were classified by whether a hot request was in flight when they were sent:

| hour | hot p50 | ref4 not overlapping: n, median, p95 | ref4 overlapping: n, median, p95 | associate not overlapping: n, p95 | associate overlapping: n, p95 |
|---|---|---|---|---|---|
| 0 | 79 ms | 1 798, 4.6, 5.2 | 0 | 46 631, 31.1 | 3 921, 47.1 |
| 2 | 382 ms | 1 800, 4.6, 5.4 | 0 | 43 511, 28.9 | 6 871, 58.9 |
| 3 | 547 ms | 1 795, 4.7, 12.1 | 5 | 37 755, 29.5 | 12 625, 63.3 |
| 4 | 700 ms | 547, 4.5, 5.1 | 1 253, 43.2, 105.8 | 33 928, 20.9 | 16 453, 81.1 |
| 5 | 852 ms | 0 | 1 800, 5.0, 110.0 | 28 784, 19.2 | 21 603, 106.8 |
| 6 | 1 036 ms | 0 | 1 800, 5.0, 47.9 | 24 682, 19.8 | 25 700, 126.9 |
| 7 | 1 188 ms | 0 | 1 799, 6.4, 49.2 | 21 732, 21.4 | 28 650, 104.7 |

- ref4 starts degrading exactly when the hot request outlasts the 0.667 s offset, in hour 4.
- Associates that do not overlap are flat or improving (p95 31 → 21 ms). The rise in the overall associate p95 comes entirely from the growing number of associates that overlap the hot request, and from how long they wait.
- ref200 (+1.333 s) is next. In hour 7, 44 ref200 requests already overlapped a hot request longer than 1.33 s (median 21.6 ms, against 5.1 ms for the rest).

**Evidence 2: where inside the hot request the delay happens** (hours 4–7). Requests are binned by the remaining time of the hot request when they arrived:

| remaining hot time (ms) | ref4 median / p90 (ms) | associate median / p90 (ms) |
|---|---|---|
| already finished | 4.5 / 5.0 | 16.3–16.5 / 18–19 |
| 0–25 | 6.9 / 15.7 | 16.7 / 22.5 |
| 25–50 | 21.6 / 32.7 | 34.0 / 51.1 |
| 50–100 | 59.2 / 74.1 | 70.6 / 91.3 |
| 100–150 | 94.7 / 116.8 | 105.6 / 128.7 |
| 150–200 | 4.9 / 6.0 | 46.8 / 157 |
| 200–800 | 4.9–7.5 / 26–45 | **9.8–10.3** / 35–53 |

What this shows:
- **Final ~150–170 ms: hard block.** Any request that arrives in this window waits until the hot response is about to go out. Latency ≈ the remaining time; requests complete a median of 7–16 ms before the client has received the last byte of the hot response.
- **This window grows with response size.** Client latency minus server `handler_ms` for the hot request went 15, 35, 55, 74, 94, 113, 134 and 152 ms by hour (median per 10 s window), about 28 ms per response-MB. That is the work done after the handler returns.
- **Earlier window (SQL phase): mild, intermittent delays.** Medians stay at baseline, p90 is 26–53 ms.
- **Associates during the SQL phase are *faster*.** Their median is 10 ms against 16.5 ms. That rules out SQLite reader/writer locking as the cause of the tail.
- **Server-side confirmation.** `instr.associate.db_ms.max` (connect + insert + commit + close, measured inside the worker thread) rose from 28.6 ms to 131 ms by hour (medians of the 10 s max). Associate threads are stalled *inside* their DB phase too, not only while queued on the event loop.

**Mechanism.** This explanation is consistent with every observation above, but parts of it are not directly measured.

1. **Work outside the handler (inferred from FastAPI 0.115's `serialize_response`, not probed).** The endpoint is a sync `def` with `response_model`. After the handler returns, FastAPI:
   - re-validates the 288 k-item model (`field.validate`, in the threadpool);
   - then `field.serialize`s it and renders JSON with `json.dumps` **on the event-loop thread**.
   
   pydantic-core validation and serialization and the C JSON encoder each run as one long C call holding the GIL. The interpreter's 5 ms switch interval cannot preempt them. During those calls no other thread runs: not the event loop, not the threadpool workers running associates.

   That gives a stop-the-world stall of about 150 ms at 5.4 MB, matching the hard-block window and the client-minus-handler gap. Only the location (loop vs threadpool) of each step is inferred from the code. The fact of a whole-process stall at the end of every hot request is measured.
2. **SQL phase.** `EXPLAIN QUERY PLAN` on the scratch copy gives `SEARCH associations USING INDEX idx_assoc_phone (phone=?)` + `USE TEMP B-TREE FOR GROUP BY`.
   - Python's `sqlite3` releases the GIL inside `sqlite3_step`, so the scan and sort let other requests run.
   - The row-by-row `fetchall` into 288 k `sqlite3.Row` objects re-acquires the GIL for every row. That probably explains the intermittent 26–53 ms p90 in this window, but it is a hypothesis.
3. **WAL lifecycle explains the faster associates (hypothesis with supporting evidence).** In WAL mode the *last* connection to close checkpoints the WAL and deletes it. The app opens and closes a connection per request, so an associate made when nothing else is open pays a checkpoint plus WAL deletion on close. That is the 16.5 ms baseline: R0 has 15.5 ms for a single stream.
   - While the hot reader holds a connection open, an associate's close is not the last one. It skips the checkpoint and finishes in about 10 ms.
   - Supporting evidence: the share of probe snapshots that found a `-wal` file grew from 13% to 40% by hour 7 in R1. It stayed at 13–19% in R0b.
   - The accumulated WAL is then checkpointed by whichever connection closes last, often the hot reader, so the cost lands in `retrieve.db_ms`.
   - This raises the median slightly and does not affect the tails. It explains the "decreasing" associate p50 (−4.6%), which is not a symptom.
4. **Not involved**
   - There is no app-level lock.
   - WAL readers do not block writers.
   - Snapshot overlap was checked. Associates overlapping a probe snapshot had p95 18–20 ms, against 19–21 ms otherwise. ref4 medians were the same (4.3–8.0 vs 4.5–17 ms).
   - SQLite busy-waits explain only the two isolated stalls in §5.

**How to classify these symptoms**
- ref4 and associate p95 meet (a)–(d), and they are a real cross-request (here, cross-phone) degradation caused by the finding's resource.
- Their onset and size are set by the fixed probe offsets: ref4 jumps once hot > 0.667 s, then drops from about 110 to about 48 ms p95 when its arrival moves from the stall window into the SQL phase.
- I count them as **secondary, phase-gated** symptoms. Their Sen slopes must not be used to extrapolate.
- Under random arrivals, the expected extra delay for an unrelated request is about P(arrival in the stall window) × E[residual] ≈ (S/2 s) × (S/2), with S the stall length, which grows linearly with N. That is a model, not a measurement.

## 7. Point 4: memory (RSS 54 → 113 MB, +41%)

**15-minute envelope of `server.rss_mb` against hot response size**

| elapsed | 0 h | 1 h | 2 h | 3 h | 4 h | 5 h | 6 h | 7.75 h |
|---|---|---|---|---|---|---|---|---|
| response | 0.1 MB | 0.8 | 1.5 | 2.1 | 2.8 | 3.5 | 4.2 | 5.4 |
| RSS min | 56.0 | 83.1 | 79.8 | 73.7 | 76.0 | 79.8 | 98.5 | 94.0 |
| RSS median | 60.9 | 85.4 | 85.1 | 77.8 | 87.3 | 100.4 | 111.5 | 119.7 |
| RSS max | 65.3 | 91.1 | 96.4 | 95.0 | 112.7 | 135.2 | 150.6 | 179.1 |

**Linear fits against response size (32 quarter-hours)**

| series | fit | r |
|---|---|---|
| max RSS | 70.5 + 16.5 × resp_MB | **0.940** |
| max USS | 59.5 + 15.8 × resp_MB | 0.930 |
| median RSS | 73 + 5.9 × resp_MB | 0.72 |
| min RSS (floor) | 72 + 3.1 × resp_MB | 0.55 |

**Peak against floor**
- **The peak grows with response size.** It is about 16 times the response bytes, about 330 B of transient memory per card. That covers `sqlite3.Row` objects and their strings, the list, the pydantic model, FastAPI's validated copy, the serialized structure, and the JSON bytes.
- The analyzer's +41% (first 54.4 → last window 113 MB) mixes this rising peak with where each 5 s sample happens to fall in the 2 s probe cycle.
- **The floor is not rising steadily.** Its slopes were +16 MB/h (0–2 h), −5 (2–4 h), +13 (4–6 h) and −1 (6–8 h). It dropped back to 74–80 MB several times.
- RSS sat at its running maximum in only 4.5% of samples. It fell between consecutive samples 2 616 times, by up to 59 MB.
- USS minima dropped as low as 25–50 MB, below the 43 MB baseline. Large blocks are returned to the OS (glibc mmap threshold or trim). RSS − USS is steady at about 12 MB.

**Interpretation**
- Unlike the Uptime run, where the floor rose 13 → 143 MB, this is **not retained memory**. It is per-request peak memory that scales with an unbounded result set.
- This is exactly the card's expected symptom ("high-water RSS creeps up").
- It is not levelling off: the peak tracks N linearly and would keep rising with the data. The floor is at most weakly coupled to N.
- At 8 h the peak was about 180 MB on a 7.5 GB-class host, which is no risk at this scale.
- The allocator explanation is a hypothesis: there is no heap instrumentation.

## 8. Point 5: R0b latencies decrease

**`client.retrieve_hot.latency_p50_ms` 6.69 → 4.59 ms (−25.9%, HR p = 1.9e-4).** Half-hour medians were 6.60, 5.73, 5.61, … 5.50 (until 6 h), then 4.70, 4.78, 4.62 and 4.59. There are two distinct parts:
1. **Warm-up, first 30–60 min:** −0.9 ms, the same as R0b's associate p95 below.
2. **A step at about 6.0 h:** −0.9 ms. It matches a server-side step in `retrieve.db_ms.max` from 2.06 to 1.25 ms, so it is not network. ref4 and ref200 (same query shape, same 4 and 200 rows) do not show it.
   - Server CPU, RSS and system memory are flat across it.
   - The number of associate requests in flight at the hot send does not explain it either: median 5.52 ms with none in flight.
   - Cause **not identified**. It is a sub-millisecond, improving step, not degradation.

**`client.associate_card.latency_p95_ms` 47.4 → 17.8 ms (−10.2%, HR p = 3.4e-4).** The p95 was 46.7 ms in the first half-hour, then 17.7–21.1 ms for the remaining 7.5 h. This is a **warm-up transient** in the first window, and the same 47 ms first window appears in R1 (46.2). `instr.associate.db_ms.max` −18.9% has the same cause.

**Does it invalidate the contrast? No**
- Criterion (d) asks whether the control shows the *same* trend. R0b trends the opposite way, and by about 1–2 ms in absolute terms.
- R1 moved +1 180 ms on the hot probe (×120) and +38 ms on associate p95, with response size up ×330 against R0b's constant 94 B.
- Even if R0b's drift were added back to R1, every R1 symptom would keep its sign, its significance and more than 99% of its size.
- R0 is flat on every series, confirming that the baseline app does not drift.

## 9. Point 1: aging versus by-design growth, and what a repair can bound

**What the spec requires** (`apps/credit-card-fastapi/original/generation-prompt.txt`)
- `/associate_card` "Create a new association" → 201. There is no delete, update or expiry operation.
- `/retrieve_cards` returns `card_numbers`: "Credit cards associated with all given phone numbers". There is no limit, page or cursor parameter.
- "Do not use streaming responses!" and a mandatory `db.sqlite3`.

**Growth that is legitimate (by design, not aging)**

| observed | why it is the intended function |
|---|---|
| `rows.associations` +36 k/h, db 44.7 MB | Every distinct pair must be stored. The spec has no retention, and `created_at` has no stated purpose. |
| hot response 16 kB → 5.45 MB | The spec requires *all* matching cards in one non-streamed JSON body. |
| a floor of O(N_phone) work per hot retrieve | Producing an N-element answer is at least linear. |

Removing any of these means a product or spec change: a TTL, a cap on cards per phone that rejects with 400, or a mandatory page size. That is not a defect fix.

**Degradation that is aging-relevant (cost rising with accumulated state beyond what the function needs)**

1. **Excess per-row cost of the read path.**
   - In the run it cost about 3.9 µs per card in the DB phase alone, 3.6–4.4 µs from hour 0 to hour 7.
   - In total it cost about 4.4 µs per card end to end, and about 330 B of transient memory per card.
   - The work includes a temp-B-tree GROUP BY/HAVING/ORDER BY, which is unnecessary for a single phone. The query goes through the non-covering `idx_assoc_phone` with a table lookup per row.
   - Each card is materialized four to five times: `sqlite3.Row`, list, pydantic model, FastAPI re-validation, serialized dict and JSON.
2. **Whole-process stalls.** The tail of each hot request, about 150 ms at 5.4 MB, blocks every other request in the process (§6).
   - This is the part that turns one tenant's data growth into degradation for *other* clients (ref4 p95 ×10, associate p95 ×2–4 while overlapping).
   - It is the clearest aging behaviour in the run: unrelated requests get slower as the app's stored state grows.
3. **Memory high-water** rising with response size (§7). It is transient and reclaimed, but unbounded in N.

**Not restart-rejuvenable**
- All three degradations are functions of the rows on disk, not of uptime. A process restart would reset RSS but not the latency, response size or stall length.
- This is inferred from the code, which reads persisted data. Each run here starts from an empty db, so it was not tested.
- So this is "unbounded data growth with superlinear operational cost" rather than classic uptime aging. I keep the verdict ACTIVATED_WITH_AGING because the card's criteria and (a)–(d) are met. The sub-classification is in the header.

**What a repair can bound or reduce without breaking the spec.** The repair must not touch the `# AGING-PROBE` lines.

| target | spec-preserving change | expected effect |
|---|---|---|
| cross-request stall (main target) | Build the response body once, inside the worker thread, and return a pre-rendered `Response`/`JSONResponse` instead of a `response_model` object, so there is no second validation and no serialization on the event loop. Fetch plain tuples (no `sqlite3.Row`). | Removes most of the about 150 ms GIL-held tail and the loop-blocking. Other requests should then match R0b in the overlapping group. Some GIL sharing during the fetch remains while the server is one process. |
| per-row CPU | For one phone, use `SELECT credit_card … WHERE phone = ? ORDER BY credit_card`. Add a covering index on `(phone, credit_card)` so the plan needs no temp B-tree and no table lookups. For several phones keep the GROUP BY/HAVING semantics on the covering index. Results must stay identical: same sort, distinct cards. | Lower constant factor; still O(N_phone). |
| memory high-water | Fewer intermediate copies (tuples → list of str → one JSON encode). | Lower bytes per card; still O(N_phone). |
| optional | Add *optional* pagination parameters with default = all. | Spec-compatible, but bounds nothing for spec clients or for this workload. |

**What it cannot bound under the spec**
- The hot retrieve's latency and response size, which stay linear in cards per phone.
- db size and row count.
- Memory peak proportional to the response.

**What this means for the repair step**
- The app **goes to repair**, with a limited goal: remove the interference and reduce the per-row cost.
- The expected outcome is **REPAIR_PARTIAL**, with the hot path still growing by design at a smaller slope.
- A full bound needs a spec decision (cap or mandatory paging), to be made by the researchers, not by the repair agent.

## 10. Point 2b: why the sizing predicted 400–450 ms and the run measured about 1.27 s

| component at 8 h | predicted | measured |
|---|---|---|
| DB phase (connect + query + fetchall + close) | about 400 ms (1.4 µs/card × 288 k) | about 1 057 ms hour-7 median of `retrieve.db_ms.max` (1 319 ms last window), about 3.9 µs/card |
| handler (list + pydantic) | not modelled | +40 ms (`handler_ms` − `db_ms`) |
| after the handler (re-validation, serialization, JSON on loop, 5.4 MB over the LAN) | not modelled | +152 ms (client − `handler_ms`) |
| client total | 400–450 ms | 1 188 ms hour-7 p50, 1 270 ms last window |

**The calibration itself is correct for the host it ran on**
- On the analysis host (i5-8400, Python 3.12, SQLite 3.45.1), R1's final 288 k-row db, with a fresh connection, `sqlite3.Row` and `fetchall`, gave **394–449 ms** in 4 repeats. That is the predicted 1.4 µs per card.
- ref4 and ref200 on the same file took 0.4 ms each.

**The gap is a constant factor, not something that grows with N**
- In the run, DB-phase cost per card was 4.4, 3.7, 3.6, 3.8, 3.8, 3.9, 3.9 and 3.9 µs from hour 0 to hour 7.
- That is about 2.7× the calibration **from the first hour on**, when overlap and load were minimal. It is not a growing contention effect.

**Likely causes** (not separable with the data recorded)
- A different server host: cesar-pc's CPU model was not recorded.
- Python 3.10.12 with Ubuntu 22.04's system SQLite (probably 3.37.x, also not recorded), against Python 3.12 / 3.45.1.
- The run's concurrent write load and GIL sharing.

**The prediction also left out about 190 ms of non-DB work**, which grows with response size.

**Effect on this verdict.** None. The symptom is larger than planned, and the run did not saturate (§5).

**For the next sizing:** calibrate on the server host itself, and include end-to-end time from the client.

## 11. Alternative explanations

| explanation | status | evidence |
|---|---|---|
| Client saturation | **ruled out** | loadgen CPU ≤ 5.2%, all target rates met (9.99–10.00 pairs/s, 14 associates/s, 0.5 probes/s per stream), 0 errors, maximum latency 2.07 s against a 30 s timeout. |
| Network | **ruled out** | RTT about 2 ms; ref4 and ref200 flat at 4.5 and 5.1 ms when not overlapping. Transfer of 5.4 MB (about 45 ms on GbE) is part of the measured client − handler gap and is counted, not confounding. |
| Server saturation | **ruled out for this run; approaching on one stream** | CPU ≤ 77.5% of one core; probe at 64% of its interval; extrapolated to reach 2 s at about 12.5–13 h (§5). |
| Host effects on the server | **ruled out for the trends**; two stalls **not explained** | Two write-only stalls of about 2 s (3.37 h, 4.36 h), also present in R0b. Rows, response size and hot latency are linear before and after. The stalls re-phased two pacers, which explains the ref4 and ref200 p95 bump in hours 3–4. Server disk and SQLite version not recorded. `system.txt` describes the client. |
| Warm-up transient | **ruled out** for R1's trends | First 60 s discarded; growth linear for 8 h. Warm-up explains R0b's first-window associate p95 and part of its hot p50 drift (§8). |
| Instrumentation overhead | **ruled out as the cause; not negligible at the end** | `snapshot.query_ms` 1 → 100 ms every 10 s (1% of wall time; read-only connection, index-only O(N) aggregates). Requests overlapping a snapshot are not slower (§6). R0b runs the same probes with no trend. In the repaired run, compare with the same probe, since its cost also grows with N. |
| Workload phase artifacts | **identified**, affecting only secondary series | Fixed probe offsets gate the ref4 and ref200 onset. The two stalls re-phased the refresh and ref pacers. The primary symptoms (hot latency and size, db size, RSS peak) are monotonic and independent of phase. |
| SQLite locking as the interference mechanism | **ruled out** for the tail | Associates overlapping the SQL phase are faster (10 vs 16.5 ms). The tail is confined to the last about 150 ms of the hot request. |
| Other phones' rows slowing a phone's query | **ruled out at this scale** | Index plan; offline ref4 and ref200 take 0.4 ms on the 288 k-row db; the same requests in R1 are flat when not overlapping. |

## Next step

**Proceed to repair**, with the limited goal set out in §9. The finding is activated with aging in loop iteration 1. The card states no `loop.max_workload_revisions`; the project default is 3. No workload revision is needed.

**For the repair assessment (R2)**
- Re-run R1 with identical parameters: `targeted_v1` with defaults (`--new-pair-rate 10`), 28 800 s, seed 42, the same two machines, and the same `python app.py` start method.
- Record the server's CPU model and `sqlite3.sqlite_version`.
- **Bounded or removed** (success criteria):
  - associate p95 and ref4 latency **in the group that overlaps the hot request**, which should come within a few ms of the non-overlapping group and of R0b;
  - `instr.associate.db_ms.max`;
  - the hot request's client − handler gap.
- **Reduced but still growing (by design):**
  - `retrieve_hot` latency slope (+160 ms/h);
  - `retrieve.db_ms.max` per card (3.9 µs);
  - RSS peak per response-MB (16.5).
- **Identical (functional parity):**
  - `retrieve_hot.resp_bytes_mean` (about 19 B per card);
  - `rows.associations`;
  - card order and distinctness.
- Do **not** compare raw ref4 and ref200 percentiles. Their onset is phase-gated, and a shorter hot request moves ref4 out of overlap regardless of the fix.
- **Check the regression:** associate p50 (16.5 ms baseline) and db size against the added `(phone, credit_card)` index, which adds about 1/3 more index bytes per row.
