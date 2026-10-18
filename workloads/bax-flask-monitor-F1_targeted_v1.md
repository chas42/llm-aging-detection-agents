# bax-flask-monitor-F1: targeted workload v1

Script: `workloads/bax-flask-monitor-F1_targeted_v1.py` (R0 control: `workloads/bax-flask-monitor-F1_control.py`).
Finding: `_run_ps` calls `subprocess.run(["ps", ...])` with no `timeout=` on the request thread, and
`app.run()` (Werkzeug, threaded) has no thread cap. If `ps` stalls, each POST /monitor/commands that
arrives during the stall holds one request thread, one child process, 2 pipe read-ends and the client
socket. None of them is released while the stall lasts.

## Environment trigger: how the runs differ

Controllability is environment. No request pattern makes `ps` block, so the trigger has two parts:

1. **Stall (environment):** the instrumentation's injection (`aging_probe.inject_ps`), enabled with
   `--app-env AGING_INJECT_PS_HANG_PROB=p`. Each ps call stalls with probability p (seeded RNG,
   `AGING_INJECT_SEED` default 42) for N = `AGING_INJECT_PS_HANG_S` (default 36000 s, longer than the
   8 h run, so every stall lasts until the run ends). The stalled child is a real process with real
   silent pipes: `setpriv --pdeathsig KILL` → `/bin/sh` (comm `sh`, PPid = app), which has a `sleep N`
   grandchild and then execs the original ps argv.
2. **Request stream (this script):** a steady, valid stream of POST /monitor/commands.

| run | workload | app env | expected |
|---|---|---|---|
| R0 | `_control.py` (replica of reference/monitor_test.jmx) | none | no activation (original condition) |
| R0b | `_targeted_v1.py` (defaults) | none | matched control: threads/children/FDs flat; same load as R1 |
| R1 | `_targeted_v1.py` (defaults) | `AGING_INJECT_PS_HANG_PROB=0.01` | linear growth of threads, children and FDs |
| R2 | R1 workload and env, `variant: repaired` | `AGING_INJECT_PS_HANG_PROB=0.01` | repair should bound or release them |

## Trigger mapping

| stream (endpoint / phase) | request | card element |
|---|---|---|
| `monitor_commands` / `cmd` | POST /monitor/commands at a fixed **open-loop** rate `--rate` (2/s), cycling round-robin (seeded start) over 4 fixed valid queries: `aux`+`python`, `-ef`+`app\.py`, `x`+`.*yth.*`, (default `aux`)+`grep` | `mechanism.trigger`: repeated POSTs reaching `_run_ps` → `subprocess.run` (app.py 160-170, 49-55). Each stalled call adds +1 thread, +1 child and +3 FDs |
| `monitor_commands` / `timeout` | the same request when its ps stalled: cut by the client after `--timeout` s, logged with status 0 and latency ≈ timeout | expected symptom: "requests issued during the stall never complete (client-side timeouts)" |
| `index` / `probe` | GET / every `--index-interval` s (5 s) | expected symptom: "GET / degrades only after thread/FD exhaustion". Path never runs ps. Should stay flat |

The 4 queries are realistic "is my service running" checks. None of the regexes matches the injected
stall lines (`/bin/sh -c setpriv ... sleep ... aging_ps_stall 36000 ps <flags>`, `sleep 36000`), so each
query's response size stays the same however many stalled processes exist. In the smokes each query
had exactly one response size per run: 168 / 895 / 3223 / 3610 B without injection and
168 / 2119 / 4483 / 4870 B with injection. The difference between the two runs comes from the
harness's own (longer) command line, which the `python` regexes also match, not from the stalls.
`-T` (one line per app thread) is deliberately not used.

## Client design (stalled requests must not reduce the offered load)

- **Open loop.** A scheduler fires one request every 1/rate s, each as its own asyncio task. A
  stalled request occupies a task and a connection but never delays the schedule. If the client
  itself falls behind, it skips missed slots without bursting (counted as `late` in workload.log).
- **Bounded in-flight.** At most `--max-inflight` (32) requests are outstanding. A slot that finds
  the cap reached is not sent and is logged as status 0, phase `dropped`, so it is visible and never
  hidden. Expected in-flight from stalls is r·p·timeout = 2 × 0.01 × 10 = 0.2, so drops should never
  happen.
- **Timeout 10 s.** Healthy calls take 9–12 ms at the start and ~100 ms by the end (see below), so
  10 s leaves wide margin. On timeout httpx closes the connection. On the server the stuck thread
  keeps the socket in CLOSE_WAIT, so the socket FD stays counted. Timeouts are rows with
  `status=0`, `phase=timeout`, `latency_ms` = elapsed time. They count as errors in
  `analyze_run.py` (`error_rate` of `monitor_commands`) and are not hidden.
- **End of run.** At `--duration` the stream stops. Outstanding requests finish or time out (≤ 10 s
  drain, within the harness's +120 s grace) and are logged.

## Sizing for 8 h (28800 s), defaults `--rate 2`, p = 0.01

Measured on this host: ps costs ~80 µs per process in the table (6 ms at 53 processes, ~190 ms at
2450). Smoke with p = 0.2 showed, per stall: +1 thread, +1 `sh` child (+1 `sleep` grandchild),
+3 FDs (2 pipes + 1 socket), app RSS +~125 KB, sh+sleep PSS ~275 KB, and VSZ +8 MB (stack; plus
glibc arenas up to 8×cores). VSZ is virtual only; `overcommit_memory` = 0.

| elapsed | ps calls | stalls (≈ r·p·t, ±√) | proc.threads | proc.children | proc.fds | host tasks added | healthy ps latency |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | ~2 | 0 | ~6 | 0 | ~9–12 ms |
| 1 h | 7 200 | ~72 ± 8 | ~74 | ~72 | ~220 | ~216 (72 threads + 144 procs) | ~20 ms |
| 8 h | 57 600 | ~576 ± 24 | ~580 | ~576 | ~1 735 | ~1 730 | ~100 ms |

Extra memory at 8 h: ~70 MB app RSS + ~160 MB for the sh/sleep pairs, plus ~5–8 GB VSZ. These are
far from the limits of this host: `ulimit -u` 31 613 (~6 % used), `ulimit -n` 1 048 576,
pid_max 4 194 304, threads-max 63 227, 7.9 GB RAM. Exhaustion is **not** the intended end point.
The run should show clean linear growth for the whole 8 h. To push toward exhaustion, raise p.

**Check before the campaign:** if the isolated machine's soft `ulimit -n` is the common default of
1024, the app reaches EMFILE at ~340 stalls (~4.7 h at these defaults). From then on accept or
pipe creation fails and POSTs return 500 "Failed to execute `ps`" or fail to connect. That is a
real exhaustion symptom, but it changes the run's shape. Either raise `ulimit -n` (e.g. 65536) in
the shell that launches the campaign, or record that the 1024 limit was reached on purpose.

**What the client should see**
- R1: `monitor_commands` `error_rate` stays at a steady ~p = 1 % (each stalled request times out on
  its own). Throughput holds at 2/s. The `timeout` rows prove the calls never completed. The error
  rate does **not** grow, because the client never shares a stuck thread. The aging lives in the
  server-side gauges: `ps.inflight`, `proc.threads`, `proc.children`, `proc.children.stall` and
  `proc.fds` grow linearly, and `ps.inflight` = `inject.stalled`.
- Secondary effect: latency of *healthy* requests grows from ~10 ms to ~100 ms, because every ps
  call has to scan a process table that grows with the stalls. In a real D-state stall only the
  `ps` processes would accumulate (1 per stall). Here each stall adds 2 (sh + sleep), so this
  latency growth is about twice what a real incident would show. Treat it as a by-product, not
  the primary evidence.
- `index` (GET /) p50 stays flat (~3 ms). It should rise only on exhaustion.
- R0b: error rate 0, all gauges flat (threads ~2, children 0–1, fds ~6), healthy latency flat.

client.csv volume: ~2.2 rows/s → ~63 k rows (~3 MB) over 8 h. The load generator is idle most of
the time.

## Smoke runs (90 s, instrumented, port 5000; dirs deleted afterwards)

| run | requests | errors | gauges at 90 s |
|---|---|---|---|
| control (`_control.py`) | 9 250 monitor_commands (~103/s closed loop), all 200, p50 8.8 ms | 0 | run_ps 9 250, threads 2–3, children 0–1, fds 5–13 (plateau) |
| targeted, no injection | 180 monitor_commands (2.01/s) + 18 index (0.2/s), all 200, p50 9.0 / 2.8 ms | 0 | stalled 0, inflight 0, threads 2, children 0, fds 6, flat |
| targeted, `AGING_INJECT_PS_HANG_PROB=0.2` | 180 monitor_commands (2.01/s, no drops, no late slots): 145 × 200 (p50 12 ms) + 35 × timeout (status 0, ~10 002 ms); 18 index all 200 (p50 3.0 ms) | 19.4 % (= the 35 stalled) | inject.stalled = ps.inflight = 35; threads 1 → 37, children 0 → 35 (all `sh`), fds 5 → 110 (+3 per stall), RSS 31.8 → 36.7 MB, all monotonic |

After the injected smoke, no `aging_ps_stall` or `sleep 36000` process was left (pdeathsig cleanup
`setpriv-pdeathsig` confirmed in the start line), and port 5000 was free.

## Commands (8 h)

```bash
# R0 control (reference/monitor_test.jmx replica: 1 thread, no timers, commands.csv cycled)
.venv/bin/python harness/run_diagnostic.py --app apps/monitor-flask/instrumented --instrument \
    --workload workloads/bax-flask-monitor-F1_control.py --duration 28800 --label R0_mon_F1_control \
    --app-cpus 0 --load-cpus 1-3

# R0b matched control (same workload, no injection)
.venv/bin/python harness/run_diagnostic.py --app apps/monitor-flask/instrumented --instrument \
    --workload workloads/bax-flask-monitor-F1_targeted_v1.py --duration 28800 --label R0b_mon_F1_matched \
    --app-cpus 0 --load-cpus 1-3

# R1 targeted (same workload + ps stall injection)
.venv/bin/python harness/run_diagnostic.py --app apps/monitor-flask/instrumented --instrument \
    --workload workloads/bax-flask-monitor-F1_targeted_v1.py --duration 28800 --label R1_mon_F1_targeted_v1 \
    --app-cpus 0 --load-cpus 1-3 --app-env AGING_INJECT_PS_HANG_PROB=0.01
```

R2 is R1 with `--app apps/monitor-flask/repaired`. Campaign definition:
`apps/monitor-flask/campaign.yaml`. Note that `--instrument` is required for R1 and R2, because
the injection only works when `AGING_INSTRUMENT=1`. The control drives ~100 req/s closed loop
(each `ps aux` takes ~9 ms), which is the original condition. Without a stall, the finding cannot
activate under it.
