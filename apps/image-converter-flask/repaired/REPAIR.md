# Repair: bax-flask-image-converter-F1

Source: `apps/image-converter-flask/instrumented` (copied in full except `__pycache__`; only `app.py` edited).
Finding: `findings/bax-flask-image-converter-F1.yaml`. Evidence: `runs/20261025-113753_image-converter-flask_R1/verdict.md` (ACTIVATED_WITH_AGING).

## Changed

`app.py` only (`aging_probe.py` is byte-identical, and the diff has no `# AGING-PROBE` changes):
- Added `import signal`, `import time` and four constants after `DEFAULT_APPEND_REVERTED`:
  - `CONVERT_TIMEOUT_S`: env `CONVERT_TIMEOUT_S`, default 60.
  - `CONVERT_VERSION_TIMEOUT_S`: env `CONVERT_VERSION_TIMEOUT_S`, default 10.
  - `CONVERT_KILL_GRACE_S`: env `CONVERT_KILL_GRACE_S`, default 2.
  - `CONVERT_REAP_TIMEOUT_S`: 5.
- New helper `run_bounded(cmd, timeout, **kwargs)`, placed after `json_error`. It works like `subprocess.Popen(..., start_new_session=True)` followed by `communicate(timeout=)`. On `TimeoutExpired` or any other exception it does the following, then re-raises:
  1. `os.killpg(pid, SIGTERM)`. ImageMagick deletes its `magick-*` disk pixel-cache files only in its SIGTERM/SIGINT handler.
  2. It waits up to `CONVERT_KILL_GRACE_S` for the leader to exit, using `os.waitid(..., WEXITED|WNOHANG|WNOWAIT)`. This does not reap the leader, so as a zombie it keeps the pgid reserved.
  3. `os.killpg(pid, SIGKILL)` unconditionally. This catches group members that ignore SIGTERM, even if the leader already exited. If the group is empty, ESRCH is ignored.
  4. It closes the stdout/stderr pipes directly, without reading them to EOF.
  5. `wait(timeout=5)`.
  6. It leaves an unkillable (D-state) child to `subprocess`'s own `_active` reaping.

  The signal steps are skipped if the leader has already been reaped, so a recycled pgid is never signalled. Worst case per stall: T + grace + 5 s.

  It does not retry.
- `ensure_imagemagick_available`: `subprocess.run([... "-version"], check=True, DEVNULL, DEVNULL)` is replaced by `run_bounded(..., CONVERT_VERSION_TIMEOUT_S, same kwargs)` plus `check_returncode()`.
  - A timeout raises `RuntimeError("ImageMagick 'convert -version' timed out after 10 s.")`.
  - Every other exception is handled as before ("…not available.").
- `run_convert_make_gif`: `subprocess.run(cmd, PIPE, PIPE, text=True)` is replaced by `run_bounded(cmd, CONVERT_TIMEOUT_S, same kwargs)`.
  - A timeout raises `RuntimeError("ImageMagick convert timed out after 60 s.")`.
  - The return-code and stderr handling is unchanged.

## What it does

Neither `convert` call can block a request thread forever any more. After T seconds, the child and everything in its process group are SIGKILLed and reaped. The request then leaves the existing `with TemporaryDirectory` (which deletes the dir) and returns 500 JSON through the existing `except RuntimeError` branch.

## Why it closes the gap

The card's gap is "no `timeout=` at lines 66/126, no kill or cleanup path while waiting". Both calls now have a timeout and a kill path. Each stall used to hold 1 thread, 1 child (plus the shim's `sleep`), 3 fds and 1 temp dir permanently. Now they are held for at most T + ≤5 s, so the number in flight is bounded by Little's law (rate × p × T ≈ 1.2 in R2) instead of growing linearly.

**Process-group kill instead of `subprocess.run(timeout=)`.**
- `run(timeout=)` kills only the direct child. Since bpo-37424 (3.7.5/3.8), on POSIX it then calls `process.wait()` rather than `communicate()`, so it would not hang. It would, however, orphan a grandchild that holds a pipe, and real delegates (`gs`, `rsvg`) would survive the kill.
- The shim's stall tree is: app → `sh` shim (exec'd through `setpriv`, comm `convert`) → `setpriv`→`sleep 36000`. The `sleep` inherits the stdout/stderr pipes, as would real delegates (`gs`, `rsvg`).
- `start_new_session=True` puts the whole tree in one group, and `killpg` reaches all of it. The pipes are closed by the parent rather than drained, so a descendant that escaped the group cannot block the cleanup.

**Observable output.** `start_new_session` only changes the child's session and process group (setsid). `convert` neither prints nor depends on them, and argv, env, cwd, `close_fds` and `text` are unchanged.

**SIGTERM before SIGKILL.** This was added after review. A SIGKILL-only version left the disk pixel cache of a timed-out real conversion in TMPDIR forever: 4 files, 368 MB per timeout. These files are outside `gif_creator_*`, so `TemporaryDirectory` does not remove them, which made them a new unbounded accumulation. The grace period lets ImageMagick's handler remove them.

**The temp-dir cleanup itself is unchanged** (verdict §3: it was already correct).

## Semaphore: deliberately omitted

- **It would not bound the main per-stall units.** The Werkzeug threaded server spawns a thread and holds the socket before the handler runs, so requests waiting on a semaphore still cost a thread, a socket and the spooled upload. It would bound only children and temp dirs, and the timeout already bounds those.
- **It adds a new 500 failure mode and a tuning knob** that could throttle legitimate CPU-heavy load. R0 already holds 10 concurrent conversions.
- **One finding, one patch.** The before/after comparison stays attributable to the timeout and kill alone.

## Verification done (local, short)

**Functional test** `tests/functional/test_convert_image.py`: the repaired app, both with and without `--instrument`, fails only at `Expected 4 frames, but got 3`, the same as the original and the instrumented copy.

**Original vs repaired** (`/tmp/icrepair/bytecheck.py`, port 5000): the responses match for 11 of 12 requests.
- **Identical:**
  - 4 GIF requests, with identical sha256 of the GIF bytes (including appendReverted with 2 and 5 frames, and the default delay);
  - all 400 messages (missing or invalid targetSize, bad delay, no images, bad extension, empty file);
  - the >50 MB upload (500 "Internal Server Error", the original's behaviour).
- **Different only in the random `gif_creator_*` name inside the message:** a garbage image (500 "convert failed: …").

**Stall test** (`/tmp/icrepair/stalltest.py`), three scenarios. All PASS, and port 5000 and the processes were cleaned up afterwards.

| scenario | setup | responses | after the stalls | probe |
|---|---|---|---|---|
| campaign shim | `env/shims/convert-hang`, `CONVERT_HANG_PROB=1`, `CONVERT_TIMEOUT_S=3`, 6 concurrent | all 6 → 500 `{"error":"ImageMagick convert timed out after 3 s."}` at ≈3.06 s | threads 2, fds 5, children 0, dirs 0 and stray `sleep 36000` 0, back to baseline from 8/23/6/6/6 during the stall | `convert.failed=6`, `create_gif.returned=6`, inflight 0, make_gif calls = requests (no retry) |
| `-version` hang | a test shim makes `-version` sleep, `CONVERT_VERSION_TIMEOUT_S=2`, 3 concurrent | 500 "…'convert -version' timed out after 2 s." | back to baseline | n/a |
| group-escaping descendant | a test shim spawns `setsid sleep 25` holding the pipes, then hangs | 500 after 3.03 s, with no blocking on the pipe | back to baseline | n/a |

Rev 2 re-ran the three scenarios above and they still pass. The SIGTERM path takes the shim tree down promptly: responses arrive at about 3.1 s, not after the grace period. Rev 2 also added two tests:
- **Group ignores SIGTERM** (`trap '' TERM`, inherited by `sleep`): 500 at 5.04 s (T + grace), then the SIGKILL fallback, and everything back to baseline.
- **Disk pixel cache** (`/tmp/icrepair/disktest.py`): 2 × 3000² PNGs resized to 4000×4000 with `MAGICK_MEMORY_LIMIT=1MiB`, `MAGICK_MAP_LIMIT=1MiB`, a fresh TMPDIR/MAGICK_TMPDIR and `CONVERT_TIMEOUT_S=1.5`.
  - Repaired: 500 "timed out after 1.5 s", 5 `magick-*` files during the run and **0 left**, with 0 children and 0 dirs.
  - The previous SIGKILL-only version, in the same setup: **4 files / 368 MB left**.

In the escape scenario the escaped `sleep 25` survived by construction. That was expected, and the test killed it afterwards.

## Residual risk / what to check manually

- **T = 60 s against a client timeout of 20 s.** In R2 the client still logs ≈580 timeouts. Judge on the server gauges, `convert.failed` ≈ 580 and the shim log pairing (k → stall identical to R1), not on the error rate. The handler's late 500 goes to a closed socket.
- **R0 regression.** Run R0 with the repaired variant and expect 0 × 500. Healthy conversions peaked at 13.8 s, but a heavier legitimate request (200 frames at 10000×10000) could exceed 60 s and would now return 500 instead of eventually succeeding. That is the inherent trade-off, and the env override is the knob.
- **Orphans outside the group.** A descendant that calls `setsid` itself, or a process stuck in D state, is not killed. The request is still released and the pipes closed. A D-state child stays a zombie candidate until `subprocess` reaps it. Check mid-run with `ps -eo pid,ppid,etimes,comm,args | grep -E 'convert|sleep 36000'`.
- **Not exercised by R2:** a stall of `-version` (covered only by the local test above) and CPU- or RAM-heavy hangs.
- **Terminal signals.** Because the children run in their own session, a terminal Ctrl-C or SIGHUP to the server's foreground group no longer reaches an in-flight `convert` directly. The shim has pdeathsig; real `convert` would finish its current conversion, which is at most T s.
- **Goodput collapse under overload.** There is no admission bound. At about 40 or more concurrent heavy requests on the 4-core server, conversions exceed 60 s and are killed after they have already done most of the work. Throughput of successful requests could then fall towards zero, where the original would have answered slowly. Re-running R0 at 10 clients does not show this.
- **`magick-*` cleanup now relies on SIGTERM.** A `convert` stuck in D state, or one that does not finish its handler within the grace period, is SIGKILLed and still leaves its pixel cache behind. A startup sweep of stale `magick-*`/`gif_creator_*` files would cover this and SIGKILL/OOM leftovers, but it is not included.
- **Invalid env values.** A non-numeric `CONVERT_TIMEOUT_S`/`CONVERT_KILL_GRACE_S` makes the app fail at import, which is intended to be loud.
