# Image Converter (bax-flask-image-converter-F1): repair and review

**Repair** (`apps/image-converter-flask/repaired`, aging-repair-agent, 29/10/2026, rev 2). Only
`app.py` changed; `aging_probe.py` and every `# AGING-PROBE` line are untouched.
- New `run_bounded(cmd, timeout)`: `Popen(start_new_session=True)` + `communicate(timeout)`. On timeout
  (or any other exception): SIGTERM to the process group, up to `CONVERT_KILL_GRACE_S` (2 s) waiting for
  the leader with `waitid(WNOWAIT)` (the zombie keeps the pgid reserved), SIGKILL to the group, close
  the pipes, reap with a 5 s bound, re-raise. No retry.
- Both calls use it: conversion `CONVERT_TIMEOUT_S` (60 s), `convert -version`
  `CONVERT_VERSION_TIMEOUT_S` (10 s). A timeout raises into the existing 500 JSON `{"error"}` path
  (the contract allows only 200 GIF or 400/500 JSON). Check order, convert argv, 400 messages, the
  `with TemporaryDirectory` cleanup (already correct) and the appendReverted deviation are unchanged.
- No semaphore, on purpose: Werkzeug takes the thread and socket before the handler, the timeout
  already bounds what is held (≈ λ·p·T ≈ 1.2 in R2), and a semaphore would add new 500s.

**Review** (aging-repair-reviewer): rev 1 **REQUEST_CHANGES** → rev 2 **APPROVE**.
- Rev 1 defect (CONFIRMED, found only by the review): SIGKILL-only kill left ImageMagick's disk
  pixel-cache files (`magick-*` in TMPDIR) forever, because ImageMagick removes them only from its
  SIGTERM/SIGINT handler: 4 files / 368 MB per timed-out heavy conversion (up to the 2 GiB disk policy).
  A new unbounded accumulation that R2 could not observe (the shim stalls before the real `convert`).
  Rev 2 (SIGTERM → grace → SIGKILL): 0 files left.
- Also corrected in REPAIR.md: the rationale against `run(timeout=)` (since bpo-37424 it calls
  `wait()` after `kill()`; it would orphan pipe-holding descendants, not hang).
- Disclosed residual risk: goodput collapse under overload (≈ 40+ concurrent heavy requests on the
  4-core server exceed 60 s and are killed after doing the work; the original answered slowly).
- Confirmed: every path bounded (worst case T + grace + 5 s ≈ 67 s); `start_new_session`
  unobservable (identical GIF sha256); Python 3.10 semantics of `waitid`/`communicate`; R0 margins
  (make_gif peak 13.8 s vs 60 s; `-version` 156 ms vs 10 s); R2 shim pairing (no extra convert calls).

**Independent checks** (Claude, 29/10): functional test with and without `--instrument` fails only
with the known "Expected 4 frames, but got 3"; 0 AGING-PROBE lines in the diff; `aging_probe.py`
identical; no leftover processes. Repair agent: 11/12 responses byte-identical to the original
(the 12th differs only in the random temp-dir name inside a 500 message); stall tests (shim p = 1,
`-version` hang, setsid escape, SIGTERM-ignoring group) all back to baseline.

**Not covered by R2:** a `-version` stall, CPU/RAM-heavy hangs, the `magick-*` cleanup
(`tmp.magick.*` must simply stay 0). These rest on review and local tests.

**Conditions for the R2 assessment**
- R2 entry unchanged (seed 42, `CONVERT_HANG_PROB=0.02`, `CONVERT_HANG_S` unset, timeout defaults).
- `convert-hang.log` vs R1 on (k, verdict): 28 800 decisions, the same 580 stalls.
- `convert.failed` ≈ 580; `children.convert.max_age_s` ≤ ≈ 61 s.
- No trend in inflight, threads, children, descendants, fds, `gif_creator` dirs/bytes (judge the
  series, not `last`), app RSS, tree threads/fds/USS.
- Do not judge on the client error rate: it stays ≈ 2 % (client timeout 20 s < T).
- Operator: `ps -eo pid,ppid,etimes,comm,args | grep -E 'convert|sleep 36000'` mid-run and at stop;
  nothing older than ≈ 67 s.
- Recommended: an R0 with the repaired variant (0 × 500, ≈ 1.22 req/s, p50 ≈ 8 s).
