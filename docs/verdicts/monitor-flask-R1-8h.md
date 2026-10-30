# Verdict: bax-flask-monitor-F1, loop iteration 1 (targeted_v1, 8 h, injected `ps` stall p = 0.01)

**Verdict: ACTIVATED_WITH_AGING** (conditional on the environment trigger, which here is injected)

The card's mechanism was reproduced as stated. Each POST /monitor/commands whose `ps` child does not return keeps one request thread, one child process and three FDs in the app. Nothing is released while the stall lasts, and the totals grow linearly with the requests sent: 589 stalls gave +589 threads, +589 children and +1 767 FDs. The client gave up on each of these requests after 10 s, but the server kept all of them for the rest of the run.

Two secondary symptoms meet criteria (a)–(d):
- The latency of **healthy** /monitor/commands requests rose about 5× (p50 19.9 → 101 ms) for every client, including those whose `ps` never stalled. The reason is that every later `ps` has to scan the stalled processes.
- App RSS and VSZ grew linearly.

GET / is unaffected at the median. No limit was reached: the run ends at about 2 % of RLIMIT_NPROC.

**What this result means.** It shows that the code does not bound or reclaim resources once a `ps` stall happens. It does not show that such stalls occur on real hosts, or that they occur at this rate or in this pattern (§7).

## Runs judged

| role | run | workload | app env |
|---|---|---|---|
| targeted R1 | `runs/20261027-170453_monitor-flask_R1` | `bax-flask-monitor-F1_targeted_v1.py` (defaults: open loop 2 req/s over 4 valid queries, GET / every 5 s, timeout 10 s, max-inflight 32) | `AGING_INJECT_PS_HANG_PROB=0.01` (N = 36 000 s, seed 42, cleanup `setpriv-pdeathsig`) |
| matched control R0b | `runs/20261027-090231_monitor-flask_R0b` | same script and defaults | none |
| paper-workload control R0 | `runs/20261027-010008_monitor-flask_R0` | `bax-flask-monitor-F1_control.py` (JMeter replica, closed loop, about 52 req/s) | none |

**Setup common to all three runs**
- Each run lasted 28 800 s with seed 42, `workload_exit=0`, server alive at stop, `server_exit=0` and `stop_reason=client`.
- All three were instrumented. App hashes were identical (`app.py 71353c71…`, `aging_probe.py d7920514…`), and both machines ran bundle `20261018-0051`.
- Two-machine mode. The server `cesar-pc` (4 CPUs, Linux 6.2.0-39, Python 3.10.12, glibc 2.35) started the app with its own `python app.py` (Werkzeug threaded dev server on 0.0.0.0:5000). RTT was 1.6–2.3 ms.
- R1 and R0b had the same workload hash (`2fe48ecd…`). The campaign ran R0 → R0b → R1 back to back.
- Limits recorded in `server_meta.limits`: `nofile` 1 048 576/1 048 576 and `nproc` 28 306/28 306. Not recorded: pid_max, threads-max, cgroup `pids.max`, MemTotal.

**Method**
- I regenerated `analysis/` for all three runs with `harness/analyze_run.py`. Every MK, Sen and change-% figure below comes from those runs.
- Hourly percentiles, per-stall regressions and overlap checks are descriptive. I computed them from the raw `client.csv`, `monitor.csv` and `instrumentation.jsonl`, with server timestamps corrected by `clock.offset_s`. The throwaway scripts are in `/tmp/judge_mon/`.
- Nothing was started.

## 1. Path executed: yes

| series (R1) | first → last | MK-HR p |
|---|---|---|
| `instr.calls.run_ps` | 140 → 57 600 | 2.5e-5 |
| `instr.ps.returned` | 138 → 57 011 | 8e-6 |
| `instr.inject.stalled` | 2 → 589 | ≈0 |
| `instr.ps.inflight` (= run_ps − returned) | 2 → 589 | ≈0 |

- Every one of the 57 600 scheduled requests reached `_run_ps`, with no 4xx or 5xx responses. 589 calls entered `subprocess.run` and never returned.
- `ps.inflight` equals `inject.stalled` in 91 % of snapshots and is never off by more than 1 (a healthy call in flight at snapshot time). It rose in every hour and never fell, which satisfies the card's `path_executed`.
- The stalled children are the injected `sh` wrappers: `proc.children.stall` = 589 at the end, `proc.children.ps` ≤ 1.
- Stalls per hour were 73, 71, 81, 72, 75, 75, 72 and 70, against an expected r·p·3600 = 72 ± 8.5.

## 2. Resource growth: meets the card's criterion exactly

**Per-stall cost.** These are least-squares fits of each `instr.proc.*` gauge against `inject.stalled` over 2 866 snapshots.

| gauge (app process only, `/proc/self`) | per stall | card prediction | r | first → last |
|---|---|---|---|---|
| `proc.threads` | **+1.000** | +1 | 1.0000 | 4 → 590 |
| `proc.children` (PPid = app) | **+1.000** | +1 | 1.0000 | 2 → 589 |
| `proc.fds` | **+3.001** | ~+3 (2 pipe read-ends + socket in CLOSE_WAIT) | 1.0000 | 12 → 1 773 |
| `proc.vm_rss_kb` | **+136 KB** | "RSS grows slightly" | 0.9999 | 30.4 → 112.7 MB |
| `proc.vm_size_kb` | **+8.4 MB** (one 8 MB thread stack + guard) | "VSZ grows" | 0.991 | 0.41 → 6.95 GB |

**Trend statistics**
- All five gauges are increasing with MK-HR p ≈ 0. Sen change ranges from +108 % (VSZ) to +199 % (threads, children, FDs).
- No snapshot showed a drop of threads (> 1) or of FDs (> 3). Under a stall that lasts longer than the run, the growth is monotonic and does not return toward baseline.

**Why `server.*` and `instr.proc.*` differ.** The two series measure different scopes, so they must not be averaged. `harness/monitor.py` samples the **process tree**: `_tree()` is `[proc] + proc.children(recursive=True)`, and it sums `rss`, `vms`, `uss`, `num_threads` and `num_fds` over every member.
- Under the injection, each stall puts **two** processes into that tree: `setpriv` execs into `sh` (the app's child), and `sh` forks `sleep` (the grandchild). The final sample has `num_children` = 1 178 = 2 × 589.
- `server.num_threads` 1 769 = 591 app threads + 1 178 single-threaded children.
- `server.num_fds` 5 306 ≈ 1 773 app FDs + 1 178 × 3 (each `sh` and `sleep` holds stdin plus the write ends of the stdout and stderr pipes). Only the 1 773 count against the app's RLIMIT_NOFILE, since that limit is per process.
- `server.rss_mb` 2 277 MB = 112.7 MB app + 1 178 × about 1.84 MB. Each child's RSS counts the shared pages of libc, `sh` and `sleep` again. This is not real memory.
- The tree's `uss` (230 MB) and the host's `sys_mem_used` give the real cost:
  - `sys_mem_used` went 1 214 → 1 657 MB in R1 (+443 MB, about **0.75 MB per stall** host-wide). This covers the app stack pages, the children's private pages and page tables, and kernel stacks for 3 tasks.
  - The same figure stayed flat in R0b (1 210 → 1 212 MB) and R0 (1 195 → 1 210 MB).
- **Use `instr.proc.*` for the app-process criterion**, which is the card's definition. Use `server.*` only as a host-impact view. Both views are inflated by the injection's extra `sleep` grandchild; a real stalled `ps` adds one process, not two.
- Side effect on measurement: sampling 1 179 processes, including `memory_full_info`/smaps, made the monitor's sample interval drift from 5.03 s to 5.40 s. That explains n = 5 513 for `server.*` in R1 against 5 740 in R0b. It does not bias the trends.

## 3. Control contrast: clean

**R0b (same workload, no injection)**
- `proc.threads` 1–3, `proc.children` 0–1, `proc.fds` 5–11, `ps.inflight` 0–1. All are "no trend" with HR p ≥ 0.92.
- `proc.vm_size_kb` constant at 262 428 KB.
- `proc.vm_rss_kb` 29.9 → 30.6 MB (+1.17 %, significant but negligible).
- `ps_ms.max` 14–28 ms with no trend. Healthy latency flat (p50 19.3 ms in every hour). Error rate 0.
- `server.*`: threads ≤ 4, FDs ≤ 15, children ≤ 1.

**R0 (paper replica, about 52 req/s closed loop, 1.496 M ps calls)**
- All gauges are flat: threads ≤ 4, FDs ≤ 15, children ≤ 1, RSS 33.0 → 33.3 MB with no trend.
- Latency p50 18.6 ms with a −0.17 % decreasing trend, which is negligible.
- `loadgen.rss_mb` +15 % is on the client side and not related to the app.
- This confirms that without a stall, a healthy `ps` releases everything per request even at 26× R1's request rate. The card's healthy-`ps` contrast holds.

**Not run:** the card's second contrast ("stall in place, no traffic → no growth"). It follows from the injection design, since stalls are created only by requests: `inject.stalled` counts only on `_run_ps` entry. It was not measured.

## 4. Symptoms (criteria (a)–(d))

| series (R1) | first → last | Sen /h | change % | MK-HR p | R0b | R0 | result |
|---|---|---|---|---|---|---|---|
| `client.monitor_commands.latency_p50_ms` (healthy, status 200) | 19.9 → 101 ms | +10.2 | +135 | ≈0 | −0.02 %, no trend | −0.17 % | **SYMPTOM** (secondary, see §5) |
| `client.monitor_commands.latency_p95_ms` | 21.4 → 111 ms | +11.5 | +141 | 6.8e-10 | +0.07 %, no trend | +0.05 % | **SYMPTOM** |
| `instr.proc.vm_rss_kb` | 30.4 → 112.7 MB | +10.1 MB | +111 | ≈0 | +1.17 % (negligible) | no trend | **SYMPTOM** (card symptom 3) |
| `instr.proc.vm_size_kb` | 0.41 → 6.95 GB | +614 MB | +108 | ≈0 | constant | no trend | **SYMPTOM** (card symptom 3; virtual only) |
| `instr.proc.threads` / `children` / `fds` | see §2 | +74 / +74 / +223 | +198 | ≈0 | flat | flat | **SYMPTOM** (card symptom 2) |
| `server.cpu_percent` (tree) | 1.0 → 2.7 % | +0.28 | +119 | ≈0 | no trend | no trend | meets (a)–(d), but only +1.7 points of one core. Negligible in absolute terms; it misses `ps` CPU (§5) |
| `client.monitor_commands.error_rate` | 0.0083 → 0 (≈1.02 % overall) | 0 | 0 | 0.735 | 0 | 0 | not a trend. A constant 1 % equal to the stalls (§6) |
| `client.monitor_commands.throughput_rps` | 2 → 2 | 0 | 0 | 1 | 2 | 52 | none |
| `client.index.latency_p50_ms` | 4.65 → 4.57 ms | +0.007 | +1.2 | 0.035 | no trend | n/a | significant but negligible; fails (c) |
| `client.index.latency_p95_ms` | 5.19 → 10.4 ms | +0.022 | +3.4 | 5.6e-6 | no trend | n/a | significant but negligible; fails (c). The last window is an outlier (§5) |
| `client.index.error_rate` | 0 | | | 1 | 0 | n/a | none |

**Card's expected symptoms, one by one**
1. *"Requests issued during the stall never complete (client-side timeouts)"*: **observed**. 589 timeouts at 10 002–10 011 ms, one per stall, and none of them appears in `server.log`, which logs only completed requests (57 011 + 5 760 lines with 200).
2. *Threads, children and FDs increase monotonically*: **observed** (§2).
3. *VSZ grows and RSS grows slightly*: **observed**, +8.4 MB and +136 KB per stall.
4. *500 "Failed to execute `ps`" or refused connections after a limit*: **not reached** (§8).
5. *GET / degrades only after exhaustion*: **consistent**. The p50 and p95 changes are negligible, and only a small p99 tail creep appears (§5).

The healthy-request slowdown was not among the card's expected symptoms. The workload rationale anticipated it as a by-product, and it qualifies under (a)–(d).

## 5. Point 2: why healthy /monitor/commands latency rose, and the /index tail

**Hourly figures, healthy requests only (status 200)**

| hour | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| stalled children (median) | 41 | 110 | 174 | 265 | 333 | 404 | 478 | 561 |
| client p50 (ms) | 25.0 | 35.0 | 44.1 | 56.1 | 66.3 | 76.5 | 87.8 | 97.6 |
| client p95 (ms) | 29.5 | 39.2 | 50.6 | 61.8 | 73.8 | 85.3 | 97.5 | 108.9 |
| server `ps_ms.max` (median of 10 s windows) | 20.7 | 30.4 | 39.1 | 51.2 | 61.0 | 70.6 | 81.7 | 92.3 |
| client p50 − `ps_ms.max` | 4.3 | 4.6 | 5.0 | 4.9 | 5.3 | 5.9 | 6.1 | 5.3 |
| R0b client p50 | 19.3 | 19.3 | 19.3 | 19.3 | 19.4 | 19.4 | 19.3 | 19.3 |

**Cause: `ps` itself scans the larger process table.** The evidence:
- **The increase is inside `subprocess.run`.** The probe's `ps_ms` (the wall time of the `subprocess.run` call) grows at **+0.1375 ms per stall** (r = 0.994). Client p50 grows at (97.6 − 25.0)/(561 − 41) = **+0.140 ms per stall**. The part outside `ps` (request parsing, Python parsing of the output, network) stays at about 4–6 ms. At most about 1 ms of the increase is in Python, which fits splitting and regex-scanning about 1 180 extra lines.
- **The per-process cost matches the calibration.** 0.1375 ms per stall over 2 extra processes is about **69 µs per process in the table**. The workload rationale measured about 80 µs per process for `ps` on the development host. `ps aux` and `ps -ef` open `/proc/<pid>/{stat,status,cmdline}` for every PID, so their cost is linear in the process count.
- **Response size is not the cause.** All four queries slow down by the same amount, including the one returning `[]` (3 B: 25.2 → 96.9 ms). Response sizes stayed constant (3/168/320/765 B; three late rows at 933 B came from a transient host process matching a regex). None of the regexes matches the stall lines.
- **Neither the GIL nor the thread count is the cause.** The 589 parked threads sit in `Popen.communicate()` → `poll()`, a blocking syscall that releases the GIL. GET /, which runs only Python on a request thread, kept p50 at 4.54–4.60 ms in every hour, against 4.53–4.59 in R0b. If 590 threads were contending for the GIL or the scheduler, GET / would show it.
- **The injection doubles the slope.** Each stall adds `sh` + `sleep`, two processes. A real stalled `ps` adds one, so the realistic slope is about +0.069 ms per stall. Over 589 stalls that would still have been 19 → about 60 ms (+200 %), so the symptom does not depend on the doubling for (c).

**Is this degradation of service for unaffected clients? Yes.**
- A client whose own request never stalls pays 5× latency at 8 h, and the latency keeps growing linearly with the cumulative number of stalls, with no plateau.
- The cost is also host-wide CPU. At hour 7 each healthy call spends about 92 ms in `ps`, mostly kernel time reading /proc. At 2 req/s that is about 0.18 core continuously, against about 0.03 at the start. psutil cannot see this, because the `ps` children exit between samples. The figure is an inference from `ps_ms`.
- Any other `ps`, `top` or `pgrep` on the host is slowed down the same way.

**/index (GET /)**
- p50 is flat and p95 hourly rose only 5.08 → 5.26 ms.
- p99 hourly rose 5.6, 6.2, 7.1, 7.5, 8.6, 8.0, 9.7, 10.3 ms, against a flat 5.3–5.4 ms in R0b. Maxima were 20–43 ms against ≤ 18 ms in R0b.
- The analyzer's final p95 of 10.4 ms is a single last window with only 12 samples. The Sen change is +3.4 %.
- Every index request is sent on the same scheduler tick as a cmd request (`targeted_v1.py`), so all of them overlap a cmd. Overlap therefore cannot separate a cause.
- Index requests that overlapped a probe snapshot (21 of them) were not slower, which rules out the snapshot thread.
- **Cause of the p99 creep: not identified.** Candidates, none of them measured:
  - fork/exec of the concurrent cmd request getting more expensive as the app gains threads, FDs and VMAs (`close_fds` over 1 770 FDs, a larger address space);
  - /proc readers (`ps` every 0.5 s, psutil smaps every 5 s) briefly taking the app's mmap lock, whose VMA count grows by about 2 per stall.
- It is a tail effect of about +5 ms at p99. It does not meet (c), so it is reported, not counted.

## 6. Point 3: stalled requests from the client's side

**Counts**
- 589 requests ended in `phase=timeout` (status 0): 1.02 % of 57 600. Latency was 10 002–10 011 ms (median 10 003), meaning the client cut them off; the server never answered.
- 589 timeouts = 589 `inject.stalled` = 589 final `ps.inflight`, a 1:1 match.
- Timeouts per hour: 73, 71, 81, 72, 75, 75, 72, 70. The error rate has no trend (HR p = 0.735).
- 0 dropped, 0 late slots, 0 HTTP errors; GET / had 5 760/5 760 with status 200.
- `workload.log` shows client in-flight 0–1 throughout. `loadgen.cpu_percent` was 0.6 % flat.

**Does the client-side cap hide server growth? No, but the client timeout decouples the two sides.**
- The `--max-inflight 32` cap was never reached. Each stalled request is abandoned after 10 s, so client in-flight is bounded by r·p·timeout ≈ 0.2.
- The server keeps all 589. After httpx closes the connection, the server socket sits in CLOSE_WAIT and stays counted in `proc.fds`.
- As a result, client-side series show a **flat** 1 % error rate and a flat 2 req/s, while the server accumulates without limit. A client-only view would see "1 % failures, constant" and miss the aging. The aging shows up only in the server gauges and in healthy latency (§5).
- Nothing was hidden. Stalls were logged, not dropped, and the open-loop schedule kept the offered load at 2/s.

## 7. The injection: what it proves, what it does not, and how realistic it is

**What it does** (`aging_probe.inject_ps`, `# AGING-PROBE` line 50)
- With probability 0.01 per call, decided by a seeded RNG independent of the request content, it replaces the argv with `setpriv --pdeathsig KILL -- /bin/sh -c 'setpriv --pdeathsig KILL -- sleep "$1" && shift && exec "$@"' aging_ps_stall 36000 ps <flags>`.
- The app then calls `subprocess.run` with the same arguments. It blocks in `communicate()` on a live child with open, silent pipes. After 36 000 s the child would exec the real `ps`, so for this 8 h run every stall is permanent.
- `pdeathsig` kills `sh` and `sleep` when the app dies, so no orphans are left behind.

**What it proves**
- If a `ps` child does not exit, the app code (unchanged; only the argv is substituted) holds exactly 1 thread, 1 child and 3 FDs per request.
- It has no timeout, no concurrency bound and no reclamation on client disconnect: the client closed each connection after 10 s and the thread stayed for 8 h.
- The growth is linear in the requests sent while stalls happen, with no plateau.
- Accumulated stalled processes slow every later `ps` call.
- These are properties of the code, and the app cannot tell this injected child from a stuck real `ps`.

**What it does not prove**
- That `ps` stalls happen on real hosts, how often, or for how long. The 1 % independent-per-call rate is a design parameter, not an observation.
- The exhaustion end state (§8), which was not reached.
- The behaviour of an **unkillable** stall. The injected child is an ordinary sleeping process: killable, reaped normally, and with `pdeathsig` cleanup.

**Realism of the stall model.** The model differs from a real D-state stall in four ways:
- **Shape (the biggest difference).** A real D-state cause, such as a hung NFS/FUSE mount or a process stuck holding its mmap lock, is persistent and correlated. While it lasts, *every* `ps aux` / `ps -ef` reads that process's `cmdline` and blocks. Real stalls therefore arrive as a burst (p ≈ 1 for the duration of the incident), not as an independent 1 %. At 2 req/s that is about 7 200 stalls per hour instead of 72. NPROC would be reached in hours (§8), and once the incident clears all stalled `ps` would complete together.
  - R1 is the "slow drip" version: rare, uncorrelated, permanent stalls. It is milder in growth rate but makes the stalls permanent.
  - Flags that avoid `cmdline` (e.g. `-o pid,comm`) might not stall at all. That was not tested.
- **Process count.** The injection adds 2 processes per stall where a real stall adds 1, which doubles the healthy-latency slope (§5). A real stall adds 2 host tasks per request (thread + `ps`) against 3 here.
- **Killability.** A real `ps` blocked in an uninterruptible wait may ignore SIGKILL until the wait ends. On recent kernels the `/proc/<pid>/cmdline` path (`access_remote_vm`) generally takes the mmap lock killably, but other D-state waits (hard NFS) do not. The injection cannot model that case. A repair that "kills on timeout" will look perfect under this injection even if it would block in `wait()` on a truly unkillable child (§10).
- **Memory.** `sh` + `sleep` (about 0.75 MB host-wide per stall including the app's thread) is not the footprint of a stalled `ps`. The app-side cost (thread stack, FDs, about 136 KB RSS) is representative.

## 8. Point 4: projection to limits (extrapolation)

Assumptions:
- Linear rates from R1: 73.6 stalls/h (589 / 8 h); +1 thread, +3 FDs and +2 child processes per stall (+1 child for a real `ps`); +0.75 MB host memory per stall.
- Limits from `server_meta.limits`.
- Linearity was verified only over 0–589 stalls.

| limit | value | consumed per stall | stalls to reach | time at 73.6/h (injection) | time, real-stall model at 73.6/h | time if stalls are correlated (2/s) |
|---|---|---|---|---|---|---|
| RLIMIT_NOFILE (per process: app FDs) | 1 048 576 | 3 | ≈ 349 500 | ≈ 4 750 h (≈ 198 days) | same | ≈ 49 h |
| RLIMIT_NPROC (tasks of UID `cesar`, threads included, whole host) | 28 306 | 3 tasks (thread + `sh` + `sleep`); 2 for a real `ps` | ≤ 9 435 (injection) / ≤ 14 150 (real) | **≤ 128 h (≈ 5.3 days)** | ≤ 192 h (8 days) | **≤ 2 h** |
| cgroup `pids.max` | **not recorded** | same as NPROC | (pids.max − baseline)/3 | e.g. a systemd `user-1000.slice` TasksMax of about 10–20 k would bind before NPROC: unverified | | |
| host memory (MemTotal not recorded) | ? | ≈ 0.75 MB (injection) | ≈ 9 400 stalls ≈ 7 GB | ≈ 128 h, a co-limit with NPROC on an 8 GB-class host | lower per stall | |

- The NPROC figures are upper bounds, because `cesar`'s baseline task count (desktop session, server agent) was not recorded and is subtracted from the same budget.
- VSZ at 9 435 stalls would be about 80 GB virtual. That is harmless under `overcommit_memory=0` (each stack mmap is checked individually) and fatal much earlier under mode 2.
- Healthy latency on the way: +0.1375 ms per stall gives about 1.3 s per healthy request at 9 435 stalls, with `ps` using about 2.6 of 4 cores at 2 req/s. Service quality degrades badly well before any hard limit, and the CPU would saturate at about 14 000 stalls, just after NPROC.

**Failure mode at NPROC or pids.max**
- `clone()` returns EAGAIN, and two paths fail:
  - Werkzeug's `ThreadingMixIn.process_request` → `Thread.start()` raises `RuntimeError: can't start new thread`. socketserver calls `handle_error` and closes the socket, so **new connections on every endpoint, GET / included,** get closed without a response.
  - On keep-alive connections that still have a live thread, `subprocess.run`'s spawn fails with `BlockingIOError`. The bare `except Exception` (app.py 63–64) turns this into **500 "Failed to execute `ps`"**, which is the card's symptom 4.
- The service does not recover, because the stalled children keep their tasks.
- At NOFILE, `accept()` returns EMFILE: socketserver swallows it, connections queue in the backlog and clients time out, and `pipe()` fails, giving 500. That limit is far away here.

## 9. Point 5: R0's clock offset

- R0 had `offset_s` 0.652 s at start and 0.0008 s at end (drift −0.652 s); the analyzer applied the mean, 0.327 s.
- The step is visible in R0's `client.csv`: at elapsed 1 112 s, consecutive rows are 0.659 s apart while their latencies are normal (18.4 ms). The closed loop otherwise has 17–20 ms gaps (next largest 0.28 s). This means the **client's wall clock stepped forward by about 0.65 s** (e.g. NTP).
- The server did not step. Its snapshot `ts` intervals stay at 10.0x s (the only exception is the final at-exit snapshot, 7.04 s), and a server step would show up as a 9.35 or 10.65 s gap.
- R0b and R1 offsets were 1–4 ms with drift ≤ 2.4 ms.

**Effect**
- Latencies use `perf_counter`, so they are unaffected.
- Server-side series in R0 are misaligned by at most 0.33 s against 60 s client windows and 5–10 s samples, which is negligible.
- One 60 s client window covered about 59.35 s of real time, a single throughput dip of about 1 %.
- R0 has no injection and no event-level client–server matching, so it **affects nothing** in this verdict.

## 10. Point 6: controls

- **R0b is clean and flat** (§3). The only significant drift, RSS +1.17 % (+0.7 MB), is negligible.
- **R0 is clean.** Its significant trends are negligible (latency p50 −0.17 %, `resp_bytes` +0.2 %, USS +0.08 %), apart from client-side `loadgen.rss_mb` +15 %, which belongs to the load generator.
- Neither control shows any trend in threads, children, FDs, VSZ, error rate or healthy latency.

## Alternative explanations

| explanation | status | evidence |
|---|---|---|
| client saturation | ruled out | loadgen CPU 0.6 % flat, in-flight 0–1, 0 dropped, 0 late, 57 600/57 600 slots sent on schedule |
| network | ruled out | GET / p50 flat at 4.5 ms; RTT 1.6–2.0 ms at both ends |
| host background or time-of-day (R0b ran 09:02–17:02, R1 17:04–01:04) | ruled out for the median latency symptom; not fully ruled out for the GET / p99 tail | latency is linear in the stall count (r = 0.994) at the calibrated cost per process; R0b and R0 are flat |
| warm-up | ruled out | growth is linear for 8 h, with no early transient |
| instrumentation overhead | ruled out as the cause; small and growing | `snapshot.gauge_ms` rose 7.8 → 54 ms per 10 s (it scans /proc too), a 0.5 % duty cycle; index requests overlapping a snapshot were not slower. Monitor sampling of the 1 179-process tree took about 0.4 s per 5 s at the end; it could contribute to tails but not to the `ps_ms` growth |
| injection artifacts | partly | they double the latency slope (2 processes per stall), give killable children, and impose an uncorrelated stall pattern (§7). The app-side resource accounting is unaffected |

## 11. Recommendations

**Repair** (it must keep the API contract of `apps/monitor-flask/original`: the same endpoint, request schema and `ps` flag semantics; 200 with `[{processId, processString}]`; errors as the `Error` JSON `{code, message}`; `python app.py` on port 5000. The `# AGING-PROBE` lines `inject_ps` / `ps_enter` / `finally: ps_exit` must stay around the call.)

1. **Add a timeout to the `ps` call**, e.g. `timeout=5` s. Healthy calls take about 15 ms, and about 190 ms with 2 450 processes on the host. On `TimeoutExpired`, raise `ApiError(500, "`ps` timed out")` (or 504), following the existing 500 "Failed to execute `ps`" pattern.
   - Keep T below the workload's 10 s client timeout, so that R2 shows a server-side error response rather than a client timeout.
2. **Kill and reap the child without blocking forever.** In CPython 3.10, `subprocess.run(timeout=…)` does `kill()` and then an **unbounded** `wait()`. That is fine for the injected (killable) child, but it hangs again on a truly unkillable D-state `ps`.
   - Prefer an explicit `Popen` + `communicate(timeout=T)` → `kill()` → `wait(timeout=short)`.
   - If the child is still alive, hand it to a background reaper and release the request thread. Close the pipes in all cases.
3. **Bound concurrency** with a module-level `BoundedSemaphore(N)` (e.g. N = 4–8) taken with `acquire(timeout=…)`. When it is exhausted, fail fast with an Error JSON (500/503) instead of spawning.
   - **Release the slot only when the child is actually reaped**, not when the request returns. Otherwise unreaped children still accumulate without bound.
   - This caps threads parked in `ps`, children and FDs at N even when kills do not take effect, which also protects against the correlated-burst case in §7.
4. Do not change the entrypoint or the server (a thread-pool WSGI server would change the failure shape, not remove it).

**R2 assessment** (R1 env and workload, `variant: repaired`; compare with `analyze_run.py R1 --compare R2`). Expected outcomes and checks:
- `inject.stalled` stays about 589, since the RNG decision is per call with the same seed and call count.
- `ps.inflight`, `proc.threads`, `proc.children(.stall)` and `proc.fds` stay at a plateau. By Little's law the plateau is r·p·T ≈ 0.1 for T = 5 s, i.e. baseline 2 threads, about 6 FDs and 0–1 children, bounded by N at worst. There must be no linear trend (HR p ≥ 0.05 or change < 10 %).
- Healthy `latency_p50_ms` stays flat at about 19–20 ms. This is the key check, since stalled processes no longer accumulate.
- `vm_rss_kb` and `vm_size_kb` stay flat. A one-time glibc arena or stack plateau is acceptable.
- The 589 stalled requests should now return 500 or 504 with the Error body after about T s, instead of status-0 client timeouts. A constant error rate of about 1 % is **expected, not a regression**. Verify the body matches the Error schema.
- No surviving `aging_ps_stall` or `sleep 36000` processes, either during the run (`server.num_children` ≤ 2) or after it.
- Regression checks: healthy p50 and p95 not higher than R0b (any extra cost from Popen, semaphore or reaper), GET / unchanged, and `functional_check.py` PASS.

**Limits of what R2 can show.** This injection cannot test the unkillable-child path (recommendation 2) or the correlated-burst path (recommendation 3). Two optional extra runs would test them:
- R2b with a higher p (e.g. 0.2), or a burst mode in which every call stalls for a window, to load the semaphore.
- A stall variant whose child ignores SIGTERM and holds the pipes from a grandchild without `pdeathsig`.

Either needs a new probe option, which is the instrumentation agent's job, not the repair's.

**Harness/meta.** Record `pid_max`, `kernel.threads-max`, the app's cgroup `pids.max` and `pids.current`, MemTotal, and the UID's baseline task count in `server_meta`, so that limit projections like §8 can be computed instead of bounded.

---

- **Verdict:** ACTIVATED_WITH_AGING (environment-conditional; trigger injected at p = 0.01, permanent stalls)
- **Evidence:**
  - Path executed: `instr.calls.run_ps` 140 → 57 600 and `ps.returned` 138 → 57 011 (HR p ≤ 2.5e-5). `ps.inflight` 2 → 589 = `inject.stalled` (HR p ≈ 0). This satisfies `path_executed`.
  - Resource growth per stall (r = 1.000): +1.000 thread, +1.000 child, +3.001 FDs. `proc.threads` 4 → 590, `proc.children` 2 → 589, `proc.fds` 12 → 1 773, each with Sen +74/+74/+223 per hour, +198 %, HR p ≈ 0, and no decreases. This meets `resource_growth` literally and matches the card's +1 / +1 / ~+3.
  - Control contrast: R0b threads 1–3, children 0–1, FDs 5–11, all no trend (HR p ≥ 0.92). R0 is flat at 52 req/s. This satisfies `control_contrast` (healthy `ps`).
  - `client.monitor_commands.latency_p50_ms`: 19.9 → 101 ms, Sen +10.2 ms/h, +135 %, HR p ≈ 0. R0b −0.02 % (no trend), R0 −0.17 %. Meets (a)–(d). Cause: `ps` scanning the stalled processes (`ps_ms` +0.1375 ms per stall ≈ 69 µs per process).
  - `client.monitor_commands.latency_p95_ms`: 21.4 → 111 ms, +11.5 ms/h, +141 %, HR p 6.8e-10. R0b flat. Meets (a)–(d).
  - `instr.proc.vm_rss_kb`: 30.4 → 112.7 MB (+136 KB per stall), +111 %, HR p ≈ 0. R0b +1.17 % (negligible). Meets (a)–(d).
  - `instr.proc.vm_size_kb`: 0.41 → 6.95 GB (+8.4 MB per stall), +108 %, HR p ≈ 0. R0b constant. Meets (a)–(d); the memory is virtual.
  - `client.monitor_commands.error_rate`: constant 1.02 % (589 timeouts at 10.0 s = 589 stalls), HR p 0.735, so it fails (a). Expected symptom 1 is observed descriptively. Throughput is constant at 2/s.
  - `client.index.latency_p50_ms` +1.2 % and `latency_p95_ms` +3.4 % (HR p 0.035 / 5.6e-6): significant but negligible, failing (c). The hourly p99 rose 5.6 → 10.3 ms against a flat R0b; the cause is not identified.
  - Limits not reached: about 1 770 / 28 306 NPROC tasks, 1 773 / 1 048 576 FDs. Extrapolated NPROC exhaustion is ≤ 128 h at this rate, or ≤ 2 h if stalls are correlated as a real D-state incident would be.
- **Alternative explanations:**
  - Ruled out: client saturation (loadgen 0.6 % CPU, 0 drops or late slots), network (GET / flat, RTT about 2 ms), warm-up, host drift for the median latency (linear in the stall count, r = 0.994, flat controls), instrumentation as the cause (snapshot duty 0.5 %; index requests during snapshots unaffected), and R0's clock step (client-side NTP step at 1 112 s; no effect).
  - Not ruled out: the cause of GET / p99 tail creep (fork or /proc mmap-lock contention, monitor sampling cost).
  - Injection artifacts: they double the latency slope, the children are killable, and the stall pattern is uncorrelated.
- **Next step:** proceed to repair. Add a timeout on `ps` (T < 10 s), kill and reap with a bounded wait (do not rely on `run()`'s unbounded post-kill `wait()`), and add a concurrency bound released on reap. Then run R2 with the same workload and injection, checking the plateau of `ps.inflight`/threads/children/FDs, flat healthy latency of about 20 ms, and Error-JSON 5xx responses in place of client timeouts.
