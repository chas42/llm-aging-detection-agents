# Monitor (bax-flask-monitor-F1): repair and review

**Repair** (`apps/monitor-flask/repaired`, aging-repair-agent, 29/10/2026). Only `app.py` changed;
`aging_probe.py` and every `# AGING-PROBE` line are untouched, so the ps-stall injection still works for R2.
- `ps` runs through `Popen` + `communicate(timeout=5)`. On timeout the child gets SIGKILL, both pipe
  read-ends are closed, and the reap waits at most 1 s. The client gets 500
  `{"code":500,"message":"`ps` timed out"}` (the app's existing Error-JSON pattern; the spec defines
  only 200 and that schema).
- `BoundedSemaphore(8)` on concurrent `ps` children, acquired with a 2 s timeout (500 "Too many
  concurrent `ps` invocations"). A slot is released only after its child is reaped. A survivor is
  handed to a daemon reaper thread; if that thread cannot start, it is parked in a list (≤ 8) and
  reaped on a later request (revision after the review).
- Worst-case request time 2 + 5 + 1 = 8 s, below the workload's 10 s client timeout. Entrypoint unchanged.
- `start_new_session` was rejected: it changes the STAT column of `ps`'s own row (`R` → `Rs`), which
  clients see. Under the injection, killing the direct child is enough (its `sleep` has a
  parent-death SIGKILL).

**Review** (aging-repair-reviewer): **REQUEST_CHANGES (minor)** → fixed.
- One confirmed defect: if the reaper thread could not start (NPROC exhaustion), a slot leaked and the
  client got "Failed to execute `ps`". Fixed with the parked-survivor list; in-process test PASS
  (slot returned once after the child exits, no double release).
- Confirmed correct: slot accounting on every other path; the timeout/kill path cannot block
  (no second `communicate()`); Python 3.10 semantics; identical behavior for normal inputs; limits
  have > 40× margin over the worst `ps` measured on the server in R0 (17.8 ms) and R1 (113 ms).

**Independent checks** (Claude, 29/10): `functional_check.py` PASS for the repaired app with and
without `--instrument` (re-run after the revision). Repair agent: error bodies byte-identical to the
original for 6 invalid inputs; local stall test (p = 1, 12 concurrent) back to baseline (threads 2,
FDs 5, 0 children, 0 `sleep 36000`).

**Not covered by R2** (needs a new probe mode: unkillable child or correlated bursts): the reaper
path, the all-slots-held path, the parked-survivor path.

**Conditions for the R2 assessment**
- The probe start line must report `inject_cleanup == "setpriv-pdeathsig"`.
- Same stall sequence: `inject.stalled` ≈ 589; `calls.run_ps` = handler calls − 4xx; no 500 "Too many concurrent".
- ≈ 589 responses of 500 "`ps` timed out" at 5–6 s, no client timeouts; error rate ≈ 1 %, no trend.
- `ps.inflight`, `proc.threads`, `proc.children`, `proc.fds`, RSS and VSZ with no trend; no leftover
  `sh`/`sleep 36000` during or after the run.
- Healthy latency from the client's status-200 requests (not `ps_ms.max`, which now shows ~5 000 ms
  in windows with a stall): p50/p95 flat at ~19–20 ms, not above R0b. GET / unchanged.
- Record pid_max, threads-max, cgroup `pids.max` and MemTotal in `server_meta` (harness gap).
