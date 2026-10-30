# Verdict: bax-flask-image-converter-F1, loop iteration 1 (targeted_v1, 8 h, PATH shim `CONVERT_HANG_PROB=0.02`)

**Verdict: ACTIVATED_WITH_AGING (environment-conditional)**

**Sub-classification: resource-exhaustion aging that depends on an external trigger.** The app has no way to release a request whose `convert` child never returns. It has no `timeout=`, no kill, no reaction to the client leaving and no concurrency bound. The cost of each stall is permanent and identical every time:

- +1 thread, +1 `convert` child, +3 fds, +1 `gif_creator_*` dir (≈406 kB);
- ≈0.52 MB of app RSS;
- ≈1.36 MB of host memory.

These add up linearly with the number of stalls (r = 1.0000 against the shim's own stall count), and nothing in the app limits them.

The stall itself was **injected from the environment**. The rate (2% of conversions) and the stall model (a sleeping, CPU-idle `convert`) are experimental choices, not observations of real ImageMagick behaviour (§7). The finding stays `conditional`. What this run establishes is that **if** `convert` hangs, the app ages exactly as the card predicts. It does not establish how often that happens in production.

The temp-dir growth is **not** a cleanup bug on the success path. It is a consequence of the stuck request holding the dir (§3).

## Runs judged

| role | run | workload / env |
|---|---|---|
| targeted R1 | `runs/20261025-113753_image-converter-flask_R1` | `bax-flask-image-converter-F1_targeted_v1.py` (defaults: 1 POST/s open loop, 4 fixed request kinds, GET / every 5 s, client timeout 20 s, max-inflight 32). App env: `TMPDIR={tmpdir}`, `PATH=<shim>:…`, `CONVERT_HANG_PROB=0.02` (`CONVERT_HANG_S` unset → 36 000 s). |
| matched control R0b | `runs/20261025-033524_image-converter-flask_R0b` | same script and arguments; `TMPDIR={tmpdir}` only (no shim on PATH) |
| paper-workload control R0 | `runs/20261024-193249_image-converter-flask_R0` | `bax-flask-image-converter-F1_control.py` (JMeter replica: 10 closed-loop workers, no think time, 5 photos, 400–800 px); `TMPDIR={tmpdir}` |

**Common setup for all three runs**
- 28 800 s each, seed 42, `workload_exit=0`, server alive at stop, `server_exit=0`, `stop_reason=client`.
- Instrumented, with identical app hashes (`app.py 158d0986…`, `aging_probe.py 93bba9d3…`) and bundle `20261018-0051` on both machines.
- R0b and R1 have the same workload hash (`b003248c…`).
- Two-machine mode. Server `cesar-pc`: 4 CPUs, Linux 6.2.0-39, Python 3.10.12, Werkzeug 3.1.3 / Flask 3.1.0. It started the app with its own `python app.py` (threaded Werkzeug dev server on `0.0.0.0:5000`).
- Clock offsets ≤ 16 ms, drift ≤ 20 ms, RTT 1.8–2.3 ms.
- Ran back to back: R0 → R0b → R1.
- Limits recorded in `server_meta.limits`: `nofile` 1 048 576 / 1 048 576, `nproc` 28 306 / 28 306. Not recorded: cgroup `pids.max`, host RAM, free disk under the run dir, `policy.xml`.

**Method**
- The `analysis/` directories were produced by `harness/analyze_run.py`, which has been unchanged since 2026-10-08, after each run ended. I used them as they are. Every MK, Sen and change-% figure below comes from them.
- Per-stall regressions, hourly percentiles, per-kind latencies and the reconciliation with the shim log are descriptive. I computed them from the raw `client.csv`, `monitor.csv`, `instrumentation.jsonl` and `tmp/convert-hang.log`, with server timestamps corrected by `clock.offset_s`. The scripts are under `/tmp/icjudge/`.
- I ran one offline check, with no app started: a 15-line Python script under `/tmp` that reproduces the atexit ordering of `TemporaryDirectory` cleanup (§2.3).

## 1. Path executed: yes, both call sites; only the conversion site stalled

| counter (R1, final) | value | meaning |
|---|---|---|
| `instr.calls.create_gif` / `create_gif.returned` | 28 800 / 28 220 | **580 handlers never returned** (neither the response nor any `except` branch) |
| `instr.calls.convert.make_gif` / `convert.returned.make_gif` | 28 800 / 28 220 | 580 entries to `subprocess.run` at app.py:126 (instrumented :131) never returned |
| `instr.calls.convert.version` / `convert.returned.version` | 28 800 / 28 800 | app.py:66 ran on every request and always returned |
| `instr.convert.failed`, `window.create_gif.error` | 0, 0 | no error path taken |
| `instr.proc.children.convert.max_age_s` | 0 → 28 724 s | the first stall (conversion #75, at 75 s) was still alive at shutdown |

This meets the card's `path_executed` criterion for the conversion site. The `-version` site (app.py:66) is exercised but **never stalled**: the shim exempts `-version` by design. Its stall footprint (thread + child, no pipes, no temp dir) is predicted by the code but not demonstrated.

**Reconciliation with the shim log** (`tmp/convert-hang.log`)
- 28 800 decisions (counter = 28 800 = conversions only): **580 `stall`** and 28 220 `pass`.
- The expectation r·p·t = 576 ± 24 (binomial sd), so 580 is +0.2 sd.
- The 580 stall lines map one-to-one onto the client's **580 `timeout` rows**: every stall has exactly one timeout, at a constant −0.20 s offset caused by `date +%s` truncation.
- At every one of the 2 866 regular snapshots, `proc.children.convert`, `convert.inflight.make_gif` and `tmp.gif_creator.dirs` equal **the cumulative count of stalls logged so far plus 0 or 1**. The +1 is a healthy conversion caught in flight.
- The resource count therefore never dropped below the number of injected stalls. The 257 decrements of `children.convert` are all healthy children finishing.
- Stalls per hour: 80, 77, 72, 76, 65, 68, 74 and 68, a steady injection.

## 2. Resource growth: meets the card's criterion literally

### 2.1 Per-stall cost (R1, least squares against `instr.convert.inflight`, 2 866 snapshots)

| resource | per stall (measured) | r | card prediction | workload-doc prediction | first → last (analyzer) | Sen /h | change % | MK-HR p |
|---|---|---|---|---|---|---|---|---|
| `instr.proc.threads` | **+1.000** | 1.0000 | +1 | +1 | 2 → 582 | +71.8 | 187 | 1.4e-8 |
| `instr.proc.children.convert` | **+1.000** | 1.0000 | +1 | +1 | 0 → 580 | +71.7 | 188 | 1.4e-8 |
| `instr.proc.descendants` | **+2.000** (shim + its `sleep`) | 1.0000 | (not stated) | +2 | 0 → 1 160 | +144 | 188 | 1.7e-8 |
| `instr.proc.fds` | **+3.000** | 1.0000 | **+2** (pipes only) | +3 (2 pipes + socket) | 6 → 1 745 | +215 | 187 | 1.4e-8 |
| `instr.tmp.gif_creator.dirs` | **+1.000** | 1.0000 | +1 | +1 | 0 → 581 (pre-exit) | +71.8 | 188 | 1.5e-8 |
| `instr.tmp.gif_creator.files` | +3.50 (mean frames per request = (4+3+5+2)/4) | 0.9999 | n/a | n/a | 0 → 2 045 (pre-exit) | +250 | 183 | 1.1e-6 |
| `instr.tmp.gif_creator.bytes` | **+406 kB** (saved uploads; no `output.gif` yet) | 0.9999 | "up to ~50 MB each" | ~425 kB | 0 → 237.6 MB (pre-exit) | +29.1 MB | 184 | 3.0e-7 |
| `instr.proc.vm_rss_kb` (app only) | **+520 kB** | 0.9985 | n/a | ~850 kB (from a 23-stall smoke) | 32.8 → 359.6 MB | +36.5 MB | 132 | 4.3e-7 |
| `instr.proc.vm_size_kb` (app only) | +8.66 MB (8 MB default thread stack + guard/arena) | 0.989 | n/a | n/a | 0.26 → 6.89 GB | +592 MB | 102 | 2.2e-8 |
| host `sys_mem_used` (monitor) | **+1.36 MB** | n/a | n/a | n/a | 1.32 → 2.32 GB | n/a | n/a | n/a |

**Fit to the card**
- The card's "+1 thread, +1 child, +1 tmpdir per stalled request" is matched exactly.
- The card's "+2 pipe fds" undercounts by one. The third fd is the accepted client socket, held by the blocked request thread after the client gave up at 20 s (presumably in CLOSE_WAIT; socket states were not sampled). The card's resource list does name "pipe and socket fds", so this is a counting slip in the criterion, not a different mechanism.
- Growth is linear and stepwise per stall, and never returned to baseline while the app ran.

**The card's "do not return to baseline after load stops"** could only be observed for the few seconds between the end of the workload (28 800 s + drain) and the stop (28 803 s). At the exit snapshot the children were still 580. The stronger evidence is that the oldest held unit lived 28 724 s with no release, and that releases never happened at any point in the run.

### 2.2 Why `server.*` (psutil) and `instr.proc.*` differ: different scopes, not noise

`harness/monitor.py` sums over `[proc] + proc.children(recursive=True)`: the app **plus every descendant**. In R1 that is the 580 stalled shims (comm `convert`, a `/bin/sh` script re-exec'd under `setpriv --pdeathsig KILL`) and their 580 `sleep 36000` grandchildren. `instr.proc.*` reads `/proc/self` only. The numbers reconcile exactly:

| at ~28 760 s | app (`instr.proc.*`) | per descendant | 1 160 descendants | sum | `server.*` (monitor) |
|---|---|---|---|---|---|
| threads | 583 | 1 | 1 160 | 1 743 | **1 743** (`num_threads`); +2.998 per stall |
| fds | 1 749 | 3.5 on average (`sh`: 0, 1, 2 + its script fd; `sleep`: 0, 1, 2) | 4 060 | 5 809 | **5 808** (`num_fds`); +9.998 per stall = 3 app + 7 per pair |
| RSS | 359.6 MB | ≈1.83 MB (libc and `sh` pages counted once per process) | ≈2 125 MB | ≈2 485 MB | **2 519 MB** (2 402 MiB in the analyzer); +4.20 MB per stall |
| USS | not sampled | ≈0.1 MB | n/a | n/a | **492 MB**; +0.72 MB per stall (≈0.52 app + ≈0.2 per shim/sleep pair) |
| children | n/a | n/a | 1 160 | n/a | **1 160** (`num_children`); +2.000 per stall |

How to read each source:
- **`server.rss_mb` (+287 MB/h, 2.4 GB) overstates the memory cost** because shared pages are counted 1 161 times.
- The right memory figures are:
  - **app RSS**: `instr.proc.vm_rss_kb`, +0.52 MB per stall;
  - **tree USS**: `server.uss_mb`, +0.72 MB per stall;
  - **host total**: `sys_mem_used`, +1.36 MB per stall. This adds kernel task structs, kernel stacks and page tables for 3 tasks per stall. It is an upper bound, but R0b shows the host otherwise flat (1.336–1.339 GB hourly).
- `server.num_threads` is the right figure for **task-count limits** (RLIMIT_NPROC, pids.max): 3 tasks per stall.
- `instr.proc.threads` is the right figure for the app's own thread count.
- `server.num_fds` is not a per-process count. RLIMIT_NOFILE applies per process, so the app's 1 749 (`instr.proc.fds`) is what matters for EMFILE.
- These series are not averaged anywhere in this verdict.

**Monitor caveat:** `server.cpu_percent` covers only the app process. `_tree()` builds new `psutil.Process` objects for the children at every sample, and their first `cpu_percent(None)` is always 0.0. Children's CPU is therefore never measured, in any run (this matters for R0, §5).

### 2.3 Why `instr.tmp.gif_creator.*` shows `last = 0`

- The analyzer's `last` is the **final atexit snapshot** (seq 2 867, taken at 28 803 s, 4.3 s after the previous one). Every earlier snapshot is non-zero from the first stall onward. Only that final one reads 0, and at the same instant it still shows `convert.inflight` = 580 and `children.convert` = 580.
- **Cause: interpreter-exit cleanup, not release by the app.**
  - On stop, Werkzeug catches the SIGINT (`KeyboardInterrupt`) and the module returns.
  - The 580 request threads are daemon threads (`ThreadingMixIn.daemon_threads = True`), so nobody joins them.
  - `atexit` handlers run in LIFO order. `TemporaryDirectory` registers a `weakref.finalize(..., atexit=True)` cleanup. The `finalize` module registers its own atexit hook at the first finalizer it ever creates, which here was the first request's temp dir, *after* `aging_probe.start()` registered the probe's hook at import.
  - So the finalizers run first and `rmtree` all 580 dirs. Then the probe's exit snapshot counts 0.
- I reproduced this ordering offline: a script registers an atexit hook, then 3 daemon threads each block inside `with TemporaryDirectory()`. The hook saw 0 dirs and none remained on disk.
- It is consistent with the collected `tmp/`, which holds only the shim's counter, lock and log.
- The shims and `sleep`s die right after the exit through `PR_SET_PDEATHSIG`.

**Consequences**
- Use the pre-exit values: 581 dirs, 2 045 files, 237.6 MB. The analyzer's Sen/MK figures for these series are unaffected, since one point out of 2 860 does not move them.
- This cleanup runs only on an orderly interpreter exit. After SIGKILL, an OOM kill or a power loss, the held dirs would stay on disk across restarts.
- For R2, compare tmp gauges on the series or the pre-exit snapshot, never on `last`.

## 3. Is the temp-dir growth a leak in the app? No: it is held by the stuck request

```python
# apps/image-converter-flask/original/app.py (instrumented: lines 216-244)
        with tempfile.TemporaryDirectory(prefix="gif_creator_") as tmpdir:
            ...
                f.save(path)
                if os.path.getsize(path) == 0:
                    raise ValueError("One of the uploaded images is empty.")
            ...
            run_convert_make_gif(frame_paths, out_path, target_size, delay_ms)
            return send_file(out_path, ...)
    except ValueError as e:
        return json_error(str(e), 400)
    except RuntimeError as e:
        return json_error(str(e), 500)
```

**Cleanup runs on every exit path.** `TemporaryDirectory.__exit__` runs on every exit from the `with`, including the error paths:
- `return send_file(...)` (success);
- the `ValueError` raised inside for a bad extension or an empty file;
- the `RuntimeError` from a non-zero convert exit;
- any `OSError` (for example ENOSPC from `f.save`).

On Linux, `send_file` already holds the output open, so deleting the dir under it is safe.

**The dir stays because the thread never leaves the `with`.** It is parked inside `subprocess.run(cmd, stdout=PIPE, stderr=PIPE, text=True)` (app.py:126/:131), which waits forever because there is no `timeout=`.

**Evidence that only stalls hold dirs**
- R1: dirs = cumulative stalls + {0, 1} at every snapshot, and all 28 220 non-stalled conversions released theirs.
- R0b: dirs ≤ 1 for 28 800 requests, back to 0 after each request.
- R0: dirs plateau at the concurrency of 10 (≈6.4 MB) and are 0 at the end.

The defect is the unbounded wait (no timeout or kill), not the cleanup. A repair does not need to change the cleanup. It needs to make the wait end.

## 4. Control contrast: both controls are clean

**R0b (matched: same script, rates and seed; no shim)**

| series | value | trend | effect |
|---|---|---|---|
| `instr.proc.threads` | 1–4 | no trend, HR p 0.52 | n/a |
| `children.convert` | ≤ 1 | no trend | n/a |
| `proc.fds` | 5–9 | no trend | n/a |
| `tmp.gif_creator.dirs` | ≤ 1 (≤ 770 kB) | no trend | n/a |
| `server.num_threads` | 2 | no trend, p 0.91 | n/a |
| `server.num_fds` | 5 | no trend, p 0.75 | n/a |
| `instr.proc.vm_rss_kb` | 31.6 → 35.1 MB | significant | +1.1%: negligible, a warm-up step in the first hour, then flat at 34.96–35.08 MB |
| `server.rss_mb` | n/a | n/a | +1.3%, same reading |
| `sys_mem_used` | 1.336–1.339 GB hourly | flat | n/a |
| `client.create_gif.error_rate` | 0 | n/a | n/a |
| `client.create_gif` p50, p95 | n/a | n/a | −0.23%, −0.08% (flat) |
| `snapshot.gauge_ms` | 8.4–8.7 ms | flat | n/a |
| `convert_ms.max.version` | 8.5 ms | flat | n/a |

**R0b is flat**, which satisfies the card's control contrast.

**Response bytes are identical in R0b and R1** for each of the 4 request kinds: 191 915, 214 186, 214 170 and 259 966 B. So the shim is output-transparent on cesar-pc.
- These differ from the 181–230 kB quoted in the workload rationale, which were measured on the smoke host with another ImageMagick build. That is not a problem.
- The GIF bytes, and therefore the conversion, are the same with and without the shim.

## 5. R0 (paper workload): not activated, and why its latency and RSS look so different

**Not activated.** No resource gauge grows:

| series | value | trend |
|---|---|---|
| `convert.inflight`, `children.convert`, `tmp.gif_creator.dirs` | steady at 9–10 (≈6.4 MB of dirs) | no trend (HR p ≥ 0.29) |
| `proc.threads` | 12 | no trend |
| `proc.fds` | 34–36 | no trend |
| `server.num_threads` | 39–43 | no trend |
| `server.rss_mb` | ≈1.41–1.46 GB | no trend, −3.3% |

- Every one of the 35 246 requests got a 200, with no timeouts (120 s client timeout).
- The only significant series is `instr.proc.vm_rss_kb` (app only): 42.7 → 51.8 MB, +6.5%, HR p 4.8e-5.
  - It is below the 10% bar and decelerating: 49.1, 49.9, 51.0, 51.6 and 51.8 MB at 1, 2, 4, 6 and 8 h.
  - That is significant but negligible. It is plausibly allocator high-water from ≈1 MB multipart bodies and 0.9 MB GIF responses handled 10 at a time. It is not the finding.

**Why p50 ≈ 8 s**
- R0 is a **closed loop with 10 workers and no think time against a CPU-bound conversion**.
- Each request converts 5 photos to 400–800 px, with `-coalesce` and `-layers Optimize`, into a ≈0.9 MB GIF.
- Throughput settled at 1.22 requests/s. By Little's law, latency = N / X = 10 / 1.224 ≈ 8.2 s, which matches the measured p50 of 8.0–8.1 s. Each request waits in processor sharing with 9 others.
- If the 4 cores are saturated, that is ≈3.3 core-seconds per conversion against ≈0.25 s for R0b's small requests, and `convert` itself runs ≈10.9 s (max per window).
- CPU saturation cannot be confirmed from the data because `monitor.py` never measures the children's CPU (§2.2). The app process itself shows 2.1%.

**Why tree RSS ≈ 1.4 GB:** it is the sum over the app (≈50 MB) and 10 live `convert` children of ≈135 MB each, which hold decoded Q16 frames and the coalesced and resized sequence. Their ≈3 OpenMP threads each explain `num_threads` ≈ 42 = 12 app threads + ≈30.

**What R0 does show, as a counterpoint:**
- With the paper's concurrency, the app has **no admission bound**. 10 concurrent children and 1.4 GB are simply whatever the clients ask for.
- A real hung conversion that had already decoded its frames would cost ≈100+ MB per stall, not the shim's ≈1.4 MB (§7).
- R0 also gives the repair a constraint: healthy conversions take **up to 13.8 s** (`convert_ms.max` peak) under the paper load. A conversion timeout must sit well above that.

## 6. Symptoms (criteria (a) to (d)) and the effect on healthy requests

| series (R1) | first → last | Sen /h | change % | MK-HR p | R0b | result |
|---|---|---|---|---|---|---|
| `instr.proc.threads` | 2 → 582 | +71.8 | +187 | 1.4e-8 | no trend | **SYMPTOM** (card symptom 1) |
| `instr.proc.children.convert` | 0 → 580 | +71.7 | +188 | 1.4e-8 | no trend | **SYMPTOM** (card symptom 1) |
| `server.num_threads` (tree: tasks) | 2 → 1 743 | +215 | +191 | 1.8e-12 | no trend | **SYMPTOM** (task-limit view) |
| `instr.tmp.gif_creator.dirs` / `.bytes` | 0 → 581 / 237.6 MB (pre-exit) | +71.8 / +29.1 MB | +188 / +184 | 1.5e-8 / 3.0e-7 | no trend | **SYMPTOM** (card symptom 2) |
| `instr.proc.fds` | 6 → 1 745 | +215 | +187 | 1.4e-8 | no trend | **SYMPTOM** (card symptom 3) |
| `server.num_fds` (tree) | 5 → 5 808 | +718 | +191 | 3.6e-12 | no trend | same, tree scope |
| `instr.proc.vm_rss_kb` | 32.8 → 359.6 MB | +36.5 MB | +132 | 4.3e-7 | +1.1% (negligible) | **SYMPTOM** (memory) |
| `server.uss_mb` (tree) | 21.8 → 469 MiB | +50.9 | +134 | ≈0 | +1.1% (negligible) | **SYMPTOM** (memory) |
| `server.rss_mb` (tree) | 31.8 → 2 403 MiB | +287 | +174 | ≈0 | +1.3% | meets (a)–(d), but overstated by shared-page counting (§2.2) |
| `server.cpu_percent` (app process only) | 1.4 → 3.1% | +0.16 | +85 | ≈0 | no trend | meets (a)–(d) and is tiny: hourly median 1.2 → 2.8% of one core. About half is the probe's own snapshot cost (`snapshot.gauge_ms` 14 → 85 ms every 10 s ≈ 0.7%). A cost indicator, not a service symptom |
| `client.create_gif.error_rate` | 0.033 → 0.017 | 0 | 0 | 0.071 | constant 0 | **fails (a)**: a constant 2.01% (580/28 800) equal to the injected p. The timeouts are present but **not rising** |
| `client.create_gif.latency_p50_ms` | 472 → 508 | +1.36 | +2.15 | 1.2e-7 | −0.23% | **fails (c)**: significant but negligible, and partly an aggregation artifact (below) |
| `client.create_gif.latency_p95_ms` | 768 → 788 | +2.54 | +2.59 | ≈0 | −0.08% | **fails (c)** |
| `client.index.latency_p50_ms` / `p95` | 4.21 → 4.10 / 4.83 → 4.80 | −0.016 / −0.050 | −3.1 / −8.6 | 1.7e-7 / 1.7e-7 | +1.7% / no trend | not degrading, fails (b) |
| `client.*.throughput_rps` | 1.0 / 0.2 | 0 | 0 | 1 | constant | none: 0 dropped and 0 late slots |

**Card symptom 4 ("client-side timeouts…; latency of healthy POST rises if stalled children consume CPU or RAM")**
- **Stalled requests:** all 580 ended at the client's 20 s timeout (20 016–20 028 ms, status 0). The server logged nothing for them, since Werkzeug logs only on response.
- **After the client gives up, the server keeps waiting** for up to 28 724 s, still holding the thread, the child, the socket, the dir and the upload parts.
- With this shim, each stall would end at 36 000 s. The shim would then run the real conversion for a client that left 10 h earlier, which costs CPU. Writing the response would then fail on the dead socket. Only after that would the thread exit and the dir be cleaned.
- **Healthy requests:**

| kind (response B) | R0b p50 | R1 hour 0 p50 | R1 hour 7 p50 | R1 − R0b in hour 0 | drift over 8 h |
|---|---|---|---|---|---|
| 191 915 | 376.2 ms | 385.0 ms | 391.8 ms | +8.8 ms | +6.8 ms (+1.8%) |
| 214 186 | 432.9 ms | 442.1 ms | 449.1 ms | +9.2 ms | +7.0 ms (+1.6%) |
| 214 170 | 493.8 ms | 503.8 ms | 511.0 ms | +10.0 ms | +7.2 ms (+1.4%) |
| 259 966 | 759.2 ms | 766.8 ms | 774.4 ms | +7.6 ms | +7.6 ms (+1.0%) |

  - **A constant +8–10 ms against R0b from the first hour on.** This is the shim's overhead on every conversion: `sh` start, a PATH scan, `awk`, `flock`, `date` and `cat` before the `exec`. The `-version` call also goes through the shim, with `convert_ms.max.version` at 8.5 ms in R0b against 10.0 ms in R1 hour 0. This is a confound in the R1-vs-R0b level, not a trend.
  - **A drift of +7 ms over 8 h (+1.0–1.8%)**, below the 10% bar. The trivially cheap `convert -version` shows the same rise: hourly median of its window max 10.0 → 15.8 ms, against flat 8.5 ms in R0b.
    - So the per-spawn cost grows with the number of held stalls. Likely candidates: the child closing ≈1 750 inherited-fd slots (`close_fds=True`), and the larger task and address space (VmSize 6.9 GB, 1 160 extra host processes). This is a hypothesis; the candidates were not separated.
    - The stalled children use **no CPU** and almost no RAM (`sleep`). The card's "if stalled children consume CPU or RAM" condition is therefore not met by this stall model, and healthy latency correctly stays nearly flat.
  - **The aggregate p50 of 472 → 508 ms is mostly a mixture artifact.** Each 60 s window holds ≈15 requests of each of 4 kinds with discrete latency levels (≈385/442/504/767 ms). The window median falls between the 2nd and 3rd kind, and a stall removing one request from a window moves it from one level to the other. R0b shows the same instability (hourly p50 488 → 466 in hour 7, with no change per kind). Only the per-kind values above should be used.
  - **Tail:** the hourly max of healthy R1 requests rose 842 → 1 130 ms, while R0b stayed 772–1 123 ms with no pattern. This is consistent with the probe's snapshot walk of 580 dirs plus a `/proc` scan of 1 160 extra processes. `snapshot.gauge_ms` reached up to 114 ms, holding the GIL in Python code. It is an instrumentation effect that grows with the held resource. It is a hypothesis and affects only the max.

**Card symptom 5 (late-stage failures):** none occurred. There were 0 non-2xx responses in 33 980 answered requests, and `GET /` stayed at about 4 ms. The run was far from any limit (§8).

**Bottom line:** the symptoms that qualify are the resource and memory series (threads, children, fds, temp dirs and bytes, app RSS, tree USS). No user-visible degradation of healthy traffic qualified in 8 h. The timeouts affect exactly the stalled 2% and do not spread.

## 7. What the injection does and does not prove; realism of the stall model

**What the shim does** (`env/shims/convert-hang/convert`, POSIX sh, first on the app's PATH)
- For every `convert` call except a lone `-version`/`--version`, it takes a lock (`flock`) on `$TMPDIR/convert-hang.lock` and increments a counter (k = the k-th conversion). It then hashes (seed 42, k) with a 32-bit xorshift-multiply and compares the result with ⌊p·2³²⌋. It appends `ts k stall|pass pid 36000` to `convert-hang.log`.
- On `pass` it `exec`s the real `convert` by name with the arguments unchanged and `PATH` stripped of the shim directory. argv[0], output, stderr and exit status stay identical.
- On `stall` it re-execs itself under `setpriv --pdeathsig KILL`, so the stalled process keeps comm `convert` and PPid = app. That process runs `setpriv --pdeathsig KILL -- sleep ${CONVERT_HANG_S:-36000}`, and only afterwards execs the real convert.
- With p = 0 it is a pure pass-through. R0b and R1 give identical GIF bytes.
- In R1, `CONVERT_HANG_S` was not set, so each stall is **36 000 s = 10 h**, longer than the run.
- The decisions are reproducible: R2 with the same seed and the same conversion count will stall the **same 580 conversions** (k = 75, 101, 130, …).

**What it proves**
- Given a `convert` that does not return, the app holds one thread, one child, three fds, the temp dir with all saved frames, and about 0.5 MB of heap per occurrence.
- It holds them for as long as the child lives, with no timeout, kill, client-disconnect reaction or bound.
- The held set scales exactly linearly with the number of occurrences.
- No app code was changed. The decorators in `aging_probe.py` only count, and the shim does not depend on them.

**What it does not prove**
1. **The rate.** p = 0.02 is an experimental knob. Nothing here shows that real conversions hang at all, let alone at 2%.
2. **The footprint of a real hang.** The shim hangs *before* ImageMagick runs: no decoding, no pixel cache, no `magick-*` temp files (`tmp.magick.*` = 0 throughout), no CPU. A real hang after decoding would hold ≈100+ MB of memory per stall (R0 shows ≈135 MB per live convert) and possibly disk-backed pixel cache, so memory exhaustion would come after tens of stalls, not thousands. A hang that spins CPU (a pathological input with no `time` policy) would also degrade healthy latency, which this run cannot show. **The measured per-stall cost is a lower bound.**
3. **The behaviour at exhaustion.** No limit was approached. The failure modes in §8 are inferred from code and kernel semantics, not observed.
4. **The `-version` call site** (app.py:66), which never stalled.
5. **Real-life duration.** The shim's stall ends after 10 h. Under this exact shim and a run longer than 10 h, the held count would plateau at about r·p·36 000 ≈ 720 (Little's law), not grow forever. The card's model (and §8) assumes hangs that never end.

**Is the stall model realistic?** For the mechanism, yes. For the rate, unknown.
- The stalls that look like this one, where `convert` blocks in a syscall, costs no CPU and never exits, are:
  - a read from a hung NFS/SMB/FUSE mount, if TMPDIR or the input path is network-backed (D state, unkillable until the mount recovers);
  - a delegate (Ghostscript, rsvg, ffmpeg) waiting on a pipe or a lock;
  - a child stopped by SIGSTOP or a debugger;
  - a `convert` waiting on a resource lock held by a crashed sibling.
- A "pathological input" hang is less likely on cesar-pc's Ubuntu 22.04 ImageMagick-6: the stock `policy.xml` limits memory, map, disk, width, height and area, so bombs usually *fail* rather than hang. It has no `time` limit (checked on the dev host per the workload rationale, not recorded for cesar-pc), so slow but finite conversions are possible. Those are long, not hung.
- The model is a plausible, conservative stand-in for the I/O-hang class, and an optimistic one for the CPU/RAM-heavy class.

## 8. Projection to limits (extrapolation, assuming permanent hangs at R1's rate)

Rates are 72.5 stalls/h (580 in 8 h), that is 1 stall per 49.7 requests. The per-stall costs come from §2.1. All lines are extrapolations from the measured slopes.

| limit (source) | per stall | headroom | stalls to reach | time at 72.5/h | requests (≈49.7 per stall) | failure mode that follows |
|---|---|---|---|---|---|---|
| **RLIMIT_NOFILE** 1 048 576 (per process, `server_meta.limits`) | +3 app fds | 1 048 570 | ≈349 500 | ≈4 820 h (≈200 days) | ≈17.4 M | EMFILE (inferred from code and kernel semantics; never observed). A failed `accept()` makes socketserver return silently, so the listener stays readable and the serve loop is expected to spin, with new connections queuing in the backlog. `subprocess.run` failing on `pipe()` or `/dev/null` becomes a 500 ("ImageMagick 'convert' command is not available." from the `-version` wrapper). *With a typical soft limit of 1 024 (for example systemd's default `LimitNOFILE=1024:524288`), this is ≈340 stalls ≈ 4.7 h, the first limit hit.* |
| **RLIMIT_NPROC** 28 306 (per real UID, counts threads, all of the user's processes) | +3 tasks (app thread + shim + `sleep`; `server.num_threads` +2.998 per stall) | 28 306 − ≈4 − B (B = the user's other tasks, not recorded) | ≤ 9 430 | ≤ 130 h (≈5.4 days) | ≤ 469 k | `clone()` returns EAGAIN. The Werkzeug accept loop's `Thread.start()` raises `RuntimeError: can't start new thread`, which socketserver logs before closing the socket. **Every endpoint, including `GET /`, gets a closed connection with no response.** Requests whose thread still starts fail to fork `convert`, which becomes a 500 with a misleading "not available" message. Other processes of the same user (ssh logins, the harness) cannot fork either. |
| **pids.max** (cgroup; not recorded) | +3 tasks | if the app runs in a systemd user slice with the default `TasksMax=33%`: ≈0.33 × threads-max. threads-max is inferred as 2 × 28 306 = 56 612, because the kernel's default RLIMIT_NPROC is threads-max/2, giving ≈18 680 (inference) | ≈6 200 | ≈86 h | ≈310 k | Same as NPROC, but cgroup-wide. Under a systemd service the limit depends on that unit's `TasksMax`. |
| **host RAM** (total not recorded) | +1.36 MB host-used; +0.52 MB app RSS | per GB free | ≈735 per GB | ≈10 h per GB | ≈36.5 k per GB | Swap, then the OOM killer, which most likely kills the app, the largest RSS. The held dirs then stay on disk (no atexit). *With a real hang that has decoded its frames (≈135 MB), this would be ≈7–8 stalls per GB and the first limit.* |
| **disk under TMPDIR** (free space not recorded) | +406 kB | per GB free | ≈2 460 per GB | ≈34 h per GB | ≈122 k per GB | `f.save` raises ENOSPC, caught by the generic `except Exception` and returned as **500 `{"error": "Internal Server Error"}`** for every valid POST. The `with` cleans each failing request's partial dir, so no new dirs pile up. `GET /` keeps working. On a RAM-backed `/tmp` (tmpfs) this merges with the RAM line. |
| virtual memory | +8.66 MB VmSize | no RLIMIT_AS recorded; overcommit heuristic | n/a | n/a | n/a | Not a practical limit: stacks are touched lazily. |

**Which limit comes first**
- With the recorded limits, the likely order is: **host RAM** (on an 8 GB-class host with ≈6.5 GB free: ≈4 800 stalls, ≈66 h), then **pids.max/NPROC** (≈86–130 h), then disk (depends on free space), with NOFILE last.
- With a default soft `ulimit -n 1024`, EMFILE comes first at ≈4.7 h.
- Under this exact shim (10 h stalls) none of these limits is reached, because the count plateaus at ≈720.

## 9. Alternative explanations

| explanation | status | evidence |
|---|---|---|
| Client saturation | **ruled out** | `loadgen.cpu_percent` median 0.8%, p99 1.2%, max 5.8%. 0 dropped, 0 late slots, client in-flight ≤ 2 against a cap of 32. 1.000 POST/s and 0.200 GET/s held for 8 h. |
| Network | **ruled out** | RTT ≈2 ms. `GET /` flat at ≈4 ms. Timeouts occur exactly at the stalled requests (one-to-one with the shim log). |
| Host effects | **ruled out for the growth** | Counts are exact multiples of logged stalls (r = 1.0000). R0b on the same host 2.5 min earlier is flat, including `sys_mem_used`. |
| Warm-up | **ruled out** | The growth starts at the first stall (75 s) and stays linear. R0b's small RSS step is in the first hour only. |
| Instrumentation overhead | **ruled out as the cause; not negligible for tails and CPU** | The growth is independently confirmed by `monitor.py` (psutil), the shim log and the client. `snapshot.gauge_ms` grows 8 → 85 ms (hourly median) per 10 s snapshot because it walks the held dirs and scans `/proc`. It plausibly explains part of the app-CPU rise and the rising max latency, but not any resource count. |
| Shim overhead | **identified; level offset only** | +8–10 ms per healthy request against R0b from hour 0. Response bytes identical. The `-version` path also pays `sh` start (8.5 → 10 ms). |
| Harness stop artifact on tmp gauges | **identified** (§2.3) | The final atexit snapshot is taken after `weakref.finalize` cleanup. The pre-exit values are used instead. |
| Card's fd prediction | **minor miscount** | +3 fds per stall (2 pipes + socket) against the card's +2 pipes. |

## Next step

**Proceed to repair.** The finding is activated with aging in loop iteration 1 (environment trigger), and no workload revision is needed. The card states no `loop.max_workload_revisions`; the project default is 3.

### Repair guidance (aging-repair-agent)

**Must preserve**

The API contract in `original/generation-prompt.txt` is responses 200 `image/gif`, 400 JSON `{"error"}` and 500 JSON `{"error"}` only. There is no 503 or 504, so a timeout or overload must surface as **500 `{"error": …}`**. The original's observable behaviour must also stay intact:
- **`build_frame_list` unchanged.** This includes the known appendReverted deviation (2 images → [A, B, A]; N ≥ 3 → forward + reversed middle). The functional test must still fail exactly at `Expected 4 frames, but got 3` and nowhere else.
- The identical `convert` argv, and therefore identical GIF bytes, for the same input.
- The `-version` check on every request, *before* validation, with its message `ImageMagick 'convert' command is not available.`.
- The existing 400 messages, `MAX_CONTENT_LENGTH` → 413, the `except` structure, and start via `python app.py` (threaded Werkzeug on 0.0.0.0:5000).
- Every `# AGING-PROBE` line and `aging_probe.py` untouched. The decorated functions must still be the ones that wrap the `subprocess` calls, so the counters keep their meaning.

**Recommended changes**
1. **Timeouts on both calls.**
   - Conversion: default ≈60 s, overridable by an env var, and well above R0's 13.8 s worst case so the paper workload is unaffected.
   - `-version`: ≈10 s.
   - On timeout, raise `RuntimeError("ImageMagick convert timed out after N s.")`, which the existing handler turns into a 500 JSON.
2. **Kill the whole process group, not only the child.**
   - Start the child with `subprocess.Popen(..., start_new_session=True)`. On `TimeoutExpired`, call `os.killpg(proc.pid, SIGKILL)`, then `proc.communicate()`/`wait()` to reap it and close both pipes.
   - `subprocess.run(timeout=)` alone kills only the direct child. Real delegates (`gs`, `rsvg`) and the shim's `sleep` would survive as orphans. In R2 the shim's `sleep` dies anyway (pdeathsig), so R2 cannot tell the two apart: get this right by construction.
   - Do not retry after a timeout. A retry would change the shim's call counter and the R1/R2 pairing.
3. **Temp-dir cleanup:** keep the `with tempfile.TemporaryDirectory(...)`. It already cleans on every path (§3). Once the wait is bounded, the dir is released at most T s after a stall. Optionally sweep stale `gif_creator_*` dirs at startup, as SIGKILL/OOM leftovers. That is not needed for R2.
4. **Concurrency bound** as defence in depth: a module-level `threading.BoundedSemaphore`, configurable, default ≥ 10 so R0's 10 concurrent requests are not throttled.
   - Acquire it *with a timeout* (for example the conversion timeout) and return 500 JSON on failure.
   - Place it after the `-version` check and the `parse_*` validation, so 400 and "not available" responses keep their order. Release it in `finally`.
   - Without the timeout in (1), a blocking semaphore would only turn hangs into pool saturation, the variant the card warns about.

### R2 assessment (aging-activation-judge)

**Run conditions**
- Use the campaign `R2` entry exactly: same workload defaults, seed 42, 28 800 s, shim `CONVERT_HANG_PROB=0.02` with `CONVERT_HANG_S` unset (36 000 s), the same two hosts and `python app.py`.
- Record `pids.max`, host RAM, free disk under the run dir, and `policy.xml` in the run notes.

**Pairing check**
- `tmp/convert-hang.log` should show 28 800 decisions with the same `k → stall` set as R1 (580 stalls at k = 75, 101, 130, …).
- Diff the two logs on (k, verdict). A mismatch means the repair changed the number of conversions.

**Success criteria (REPAIR_EFFECTIVE)**
- These series show no trend (MK-HR p ≥ 0.05, or |change| < 10%), with maxima of a few units: `instr.convert.inflight`, `proc.threads`, `proc.children.convert`, `proc.descendants`, `proc.fds`, `tmp.gif_creator.dirs/bytes` (judge on the series, not `last`, §2.3), `proc.vm_rss_kb`, `server.num_threads/num_fds/uss_mb`, and `sys_mem_used`.
- Expected mean in flight is r·p·T (1.2 for T = 60 s).
- At stop: `convert.failed` ≈ 580, `calls.create_gif` = `create_gif.returned` (± in flight), and `children.convert.max_age_s` ≤ T plus a few seconds.

**Orphans**
- `monitor.py` and the probe see only the app's tree, so reparented orphans would be invisible.
- Have the operator capture `ps -eo pid,ppid,etimes,comm,args | grep -E 'convert|sleep 36000'` mid-run and at stop. Expect zero shims or `sleep`s older than T.

**Client view (not a criterion)**
- With T > 20 s, the client still logs ≈580 timeouts, so the error rate stays ≈2% because the client gives up before the server's 500. Do not judge the repair on the error rate.
- The server side should show ≈580 handler returns through the `RuntimeError` path, about T s after each stall. In `server.log` they appear as 500 lines or broken-pipe messages for gone clients.

**Regression check**
- Healthy per-kind p50 should equal R1's hour-0 values (≈385, 442, 504, 767 ms, which include the shim's overhead), with no +7 ms drift.
- `snapshot.gauge_ms` should be flat at ≈8–15 ms.
- `GET /` should stay flat.
- Re-run **R0 with the repaired variant** (paper workload): 0 errors, ≈1.22 requests/s, p50 ≈8 s. Any 500 there means T or the semaphore is too tight.
- The functional test should fail only at the known appendReverted assertion.

**Not covered by R2:** a stall of the `-version` call (the shim never stalls it) and CPU- or RAM-heavy hangs. Note these as residual risk, covered by code review of the timeout on both calls.

- **Verdict:** ACTIVATED_WITH_AGING (environment-conditional; stall injected by a PATH shim at p = 0.02, 36 000 s stalls)
- **Evidence:**
  - Path executed. `calls.convert.make_gif` 28 800 against `returned` 28 220: 580 calls to app.py:126 never returned, and 580 `create_gif` handlers never reached any branch (`max_age_s` 0 → 28 724 s). This matches the shim log's 580 `stall` lines one-to-one and the client's 580 timeouts. Expected r·p·t = 576 ± 24. Meets `path_executed`.
  - `instr.proc.threads` 2 → 582, Sen +71.8/h, +187%, HR p 1.4e-8, +1.000 per stall (r = 1.0000). R0b no trend (p 0.52). Meets growth, control contrast and (a)–(d).
  - `instr.proc.children.convert` 0 → 580, +71.7/h, +188%, p 1.4e-8, +1.000 per stall. R0b ≤ 1, no trend. Meets them all.
  - `instr.proc.fds` 6 → 1 745, +215/h, +187%, p 1.4e-8, +3.000 per stall (the card said +2: the extra fd is the socket). R0b 5–9, no trend. Meets them all.
  - `instr.tmp.gif_creator.dirs` / `.bytes` 0 → 581 / 237.6 MB before the exit snapshot (the analyzer's `last=0` is atexit `weakref.finalize` cleanup), +71.8/h / +29.1 MB/h, +188% / +184%, p 1.5e-8 / 3.0e-7, +1 dir and +406 kB per stall. R0b ≤ 1 dir. Meets them all.
  - `instr.proc.vm_rss_kb` 32.8 → 359.6 MB, +36.5 MB/h, +132%, p 4.3e-7 (+0.52 MB per stall). `server.uss_mb` +134%. R0b +1.1% (negligible). Memory symptom.
  - `server.num_threads` 2 → 1 743 (+3 tasks per stall), `server.num_fds` 5 → 5 808 and `server.rss_mb` 32 → 2 403 MiB are tree sums over the app plus 1 160 shim/`sleep` descendants. They reconcile exactly with `instr.*` and are not averaged with it.
  - `client.create_gif.error_rate` constant 2.01%, HR p 0.071: fails (a), timeouts present but not rising. `latency_p50_ms` 472 → 508, +2.15%, p 1.2e-7: fails (c), negligible and mostly a mixture artifact. Per kind: +8–10 ms constant shim offset plus +7 ms drift (+1.0–1.8%). `latency_p95_ms` +2.6%: fails (c). `GET /` flat. 0 non-2xx.
  - Controls: R0b flat on every gauge. R0 (paper workload, 10 closed-loop workers, CPU-bound, p50 8 s by Little's law, 1.4 GB tree RSS from 10 live converts) not activated; only app RSS +6.5% (negligible).
  - Projection (extrapolation): NOFILE ≈200 days (≈4.7 h with soft 1024), NPROC ≤ 130 h, pids.max ≈86 h if the user-slice default applies, RAM ≈10 h per free GB, disk ≈34 h per free GB. Under this shim the count plateaus at ≈720 because stalls end at 10 h.
- **Alternative explanations:**
  - Ruled out: client saturation (loadgen ≤ 5.8% CPU, 0 drops or late slots), network, host effects, warm-up.
  - Ruled out as the cause: instrumentation overhead (the growth is confirmed independently by psutil, the shim log and the client).
  - Identified and accounted for: the shim's constant +8–10 ms per request, the exit-time tmp cleanup artifact, the growing probe snapshot cost (contributes to app CPU and max latency only) and the card's fd miscount.
  - Not ruled out / not shown: the real-world stall rate, and the footprint of a real (CPU/RAM-heavy) hang.
- **Next step:** proceed to repair: timeouts on both `subprocess` calls, process-group kill, keep the `with` cleanup, and an optional bounded semaphore (≥ 10, acquire timeout), all with 500 JSON. Then R2 with the identical shim and seed, judged on bounded gauges and orphan checks, plus a repaired R0 for regression.
