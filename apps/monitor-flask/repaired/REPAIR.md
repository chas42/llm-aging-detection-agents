# Repair: bax-flask-monitor-F1 (`ps` with no timeout and no concurrency bound)

Inputs: `findings/bax-flask-monitor-F1.yaml`, `runs/20261027-170453_monitor-flask_R1/verdict.md`
(ACTIVATED_WITH_AGING), source `apps/monitor-flask/instrumented`.
This directory is a copy of the source with only `app.py` edited. `aging_probe.py`, `app.yaml`,
`requirements.txt` and `generation-prompt.txt` are byte-identical to the source, and no `# AGING-PROBE` line was
added, removed or moved (`diff -ru instrumented repaired` contains no AGING-PROBE line).

## Diff summary (`app.py` only)

- `import threading`.
- New module constants: `_PS_TIMEOUT_S = 5.0`, `_PS_KILL_WAIT_S = 1.0`, `_PS_MAX_CONCURRENT = 8`,
  `_PS_SLOT_WAIT_S = 2.0`, and `_ps_slots = threading.BoundedSemaphore(8)`.
- New `_communicate_bounded(cmd)`. It does `Popen(cmd, stdout=PIPE, stderr=PIPE, text=True)` followed by
  `communicate(timeout=5)`, and returns a `CompletedProcess`, or `None` on timeout. It owns one semaphore
  permit, which is already acquired when it is called. If `Popen` fails, it releases the permit and re-raises.
- New `_reap_ps(proc)`, which runs in a `finally` on every path. It sends SIGKILL if the child is still
  running, closes both pipe read-ends, then calls `wait(timeout=1)`. If the child is reaped, it releases the
  permit. If not, it starts one daemon `ps-reaper` thread that calls `wait()` and releases the permit
  when the child is finally reaped.
  - If `Thread.start()` itself fails (e.g. `RuntimeError: can't start new thread` under NPROC or
    `pids.max` exhaustion), the exception is caught. The child is added to the module-level list
    `_ps_unreaped`, guarded by `_ps_unreaped_lock`, and keeps its permit. The call still returns the
    timeout result, so the client gets "`ps` timed out" and not "Failed to execute `ps`".
- New `_collect_unreaped_ps()`, called at the top of `_run_ps` before the acquire, and only when the list
  is non-empty.
  - It calls `poll()` on each listed child. For every child that has exited and been reaped, it removes the
    child from the list and releases its permit.
  - Both steps happen under the lock, and each child is removed exactly once, so a permit is never released
    twice and never released for a child that has not been reaped.
  - The list holds at most 8 entries, because each entry holds a permit.
- `_run_ps`:
  - Before the probe lines, it takes a permit with `_ps_slots.acquire(timeout=2)`. If none is free within
    2 s, it raises `ApiError(500, "Too many concurrent `ps` invocations")`.
  - `subprocess.run(...)` is replaced by `_communicate_bounded(cmd)` at the same place, inside the same
    `try / except FileNotFoundError / except Exception / finally: ps_exit` block.
  - After the probe `finally`, `None` raises `ApiError(500, "`ps` timed out")`. This raise is outside the
    `try` on purpose, so that `except Exception` does not turn it into "Failed to execute `ps`".
- The entrypoint and server are unchanged (`app.run(host, port)`, Werkzeug threaded).

## How each target is met

1. **Bounded wait for `ps`.**
   - `communicate(timeout=5)`. Healthy `ps` takes about 15–200 ms, and 5 s is below the workload's 10 s
     client timeout.
   - The kill and reap cannot hang. SIGKILL is followed by `wait(timeout=1)`; the code never calls
     `subprocess.run`'s unbounded post-kill `wait()`, and it never calls a second `communicate()` that
     could block on a descendant holding the write ends.
   - Both read-ends are closed on every path. On success, `communicate()` has already closed them at EOF,
     and closing them again does nothing.
   - Worst-case request time: 2 s (slot wait) + 5 s + 1 s = 8 s, below 10 s.
2. **Concurrency bound.** At most 8 `ps` children exist at once. Each permit is released only after
   `wait()` has reaped the child, never just because the request returned. Because of this, children, pipe
   FDs and request threads parked in `ps` are each capped at 8, even when kills do not take effect.
   - **Survivors.** A child that survives SIGKILL for more than 1 s (D state) keeps its permit, and one
     daemon thread waits for it. Its pipes are already closed, so it holds no app FDs. The request thread
     itself returns 500 after about 6 s.
   - **Why this stays bounded.** A reaper thread exists only while its child holds a permit, so there are
     at most 8 such threads and 8 such PIDs, never more.
   - **If all 8 permits are held by survivors**, every POST /monitor/commands gets the fast 500 "Too many
     concurrent `ps` invocations" after 2 s until one is reaped. The service degrades to a bounded error
     instead of growing without limit. GET / is unaffected.
3. **Entrypoint and server unchanged.**

**Status code choice: 500 for both new errors.**
- The spec defines only 200 plus the `Error {code, message}` schema, with no other status codes.
- For server-side failures to run `ps`, the existing code already returns 500 with this exact shape
  ("Failed to execute `ps`", "`ps` command not found on this server"). Timeouts and overload are the same
  class of failure, so 500 adds no status code that clients of the original could not already receive.
- 503 or 504 would be more specific, but they would be new behaviour.

## Behaviour where `ps` completes normally: unchanged

- When `ps` finishes within 5 s, the repaired code makes the same Popen call as `subprocess.run` did:
  same argv, PIPE/PIPE, text mode, `close_fds` default, same session and process group. It sees the same
  returncode, stdout and stderr, and goes through the same parsing and the same 200/400/500 mapping.
- A decode error inside `communicate()` still propagates to `except Exception`, giving 500 "Failed to
  execute `ps`", as before.
- **No new session or process group.** `start_new_session=True` + `killpg` was considered and rejected. It
  visibly changes the output: the `ps` row of `ps` itself changes STAT from `R` to `Rs` (verified locally).
  `process_group=` needs Python ≥ 3.11, and the server runs 3.10.12. `preexec_fn` is not thread-safe.
- **Killing the direct child is enough for the injection.** `inject_ps` runs
  `setpriv --pdeathsig KILL -- /bin/sh -c 'setpriv --pdeathsig KILL -- sleep N && … exec ps …'`. The app's
  child is `sh`. The grandchild `sleep` has PDEATHSIG=KILL bound to `sh`, so SIGKILL on `sh` kills
  `sleep` immediately. A real stuck `ps` has no descendants.
- **If a descendant does survive** (for example the injection's no-setpriv fallback), it cannot block the
  app, because the app has already closed its read-ends. Such a descendant would be an orphan outside the
  app's process; the app never waits for it.

## Local verification (not a substitute for R2)

- `py_compile` OK.
- `harness/functional_check.py`: PASS for the repaired app with `--instrument`, PASS for it without, and
  PASS for the original.
- Error-path bodies are byte-identical between original and repaired. Inputs checked: bad ps flag (400
  with ps stderr), missing regex, invalid regex, non-string flags, non-JSON body, and a no-match `[]`.
- Stall test with `AGING_INJECT_PS_HANG_PROB=1` on port 5000, about 1 min, script
  `/tmp/claude-1000/-home-cesar-projects-llm-research/6587e912-e67b-4d52-a74b-c799acd1258b/scratchpad/stall_test.py`:
  - Baseline: threads 2, FDs 5, children 0.
  - 12 concurrent stalled POSTs: 8 returned 500 "`ps` timed out" at 5.01 s, and 4 returned 500 "Too many
    concurrent `ps` invocations" at 2.0 s.
  - During the burst: children 8, threads 10, FDs 29 (5 + 8 × 3), and 8 `sleep 36000` processes.
  - 5 sequential stalled POSTs: each returned 500 timed out at 5.01 s.
  - After the burst: threads 2, FDs 5, children 0, and no `sleep 36000` process left.
  - Probe: `calls.run_ps` = `ps.returned` = `inject.stalled` = 13, `ps.inflight` 0, max `ps.inflight.max` 8.
  - No process was left on port 5000.

- In-process test of the case where the reaper thread cannot be started. No server and no port are used.
  Script:
  `/tmp/claude-1000/-home-cesar-projects-llm-research/6587e912-e67b-4d52-a74b-c799acd1258b/scratchpad/reaper_fail_test.py`.
  - Setup: the script imports the repaired `app.py` and uses Flask's `test_client`. It sets T = 0.5 s and
    the kill wait to 0.3 s. `Popen` is patched to run `sleep 2` with `kill()` as a no-op, and
    `Thread.start` is patched to raise RuntimeError.
  - The POST returned 500 `{"code":500,"message":"`ps` timed out"}` after 0.80 s.
  - Afterwards: 7 free slots, 1 entry in `_ps_unreaped`, child still alive. A collect while the child is
    alive kept 7 free slots (no early release).
  - After the child exited, a healthy POST returned 200 `[]`, and the collect at the top of `_run_ps`
    brought the slots back to 8 with an empty list.
  - A second collect left 8, so there was no double release (a `BoundedSemaphore` would raise on one).
  - Result: PASS.

## Not covered / residual risk

- **The unkillable-child path is untested end to end.** The injection's children are always killable, so the
  `ps-reaper` thread and the case where all permits are held were not exercised under a real stall.
  Testing them needs a new probe option, which is the instrumentation agent's job. Only the branch where
  `Thread.start` fails was tested, in-process, with patches.
- **When the reaper thread cannot be started,** a survivor's permit comes back only on a later POST, after
  the child has exited. If all 8 permits are held this way, each POST still runs the collect before its 2 s
  acquire, so the permits recover as soon as the children exit.
- **D-state limits.** A truly unkillable `ps` still costs one PID and one small daemon thread for as long
  as it is stuck. This is capped at 8.
- **Correlated bursts** (every `ps` stalls while an incident lasts): POSTs fail with 500 after about 5 s or
  2 s, and resources stay ≤ 8 children. Not run at scale (an R2b with high p would test this).
- **The constants are judgement calls.** T = 5 s fits healthy `ps` on hosts with ≤ ~10⁴ processes, and N = 8
  is far above the R1 and R0 in-flight levels (≤ 1). A host where a healthy `ps` takes more than 5 s, or a
  sustained concurrency above 8 for longer than 2 s, would now get 500s where the original was only slow.
- **The semaphore is not FIFO.** Under contention, waiters can be served out of order. This is bounded by
  the 2 s wait.
- **Probe semantics.** All counters keep their meaning. `ps_exit` still runs when the call returns or raises.
  The one difference: stalled calls now *return* after about 5 s, so `ps_ms.max` will show about 5000 ms in
  any window that contains a stall. In R1, stalled calls never returned, so they never appeared in
  `ps_ms.max`. Compare healthy latency with client status-200 latency, not with `ps_ms.max`.
- **RNG alignment.** A call rejected by the semaphore does not reach `inject_ps`, so it consumes no RNG
  draw. With no rejections, as expected in R2, the decision sequence matches R1.

## Expected R2 (R1 env + workload, `variant: repaired`)

- `inject.stalled` ≈ 589, the same as R1 (same seed and call count).
- `ps.inflight`, `proc.threads`, `proc.children(.stall)` and `proc.fds` plateau near baseline, with
  r·p·T ≈ 0.1 stalled calls in flight on average, ≤ 1–2 in practice, and no trend.
- Healthy `latency_p50_ms` stays flat at about 19–20 ms, because stalled processes no longer accumulate.
  RSS and VSZ stay flat.
- About 589 requests (≈ 1 %) get 500 `{"code":500,"message":"`ps` timed out"}` after about 5 s instead
  of status-0 client timeouts. This is a constant error rate and is expected.
- There should be no "Too many concurrent" errors, and no `aging_ps_stall` or `sleep 36000` survivors during
  or after the run.
