# bax-flask-image-converter-F1: targeted workload v1

Script: `workloads/bax-flask-image-converter-F1_targeted_v1.py`. R0 control:
`workloads/bax-flask-image-converter-F1_control.py`.

Finding: every POST /create-gif runs `subprocess.run(["convert", "-version"])` (app.py:66) and the
conversion `subprocess.run(["convert", ...])` (app.py:126). Neither call has a `timeout=`, and
`app.run()` (Werkzeug, threaded) has no thread cap. If the conversion stalls, the request holds one
thread, one `convert` child, 2 pipe read-ends, the client socket, the `gif_creator_*` temp dir with the
saved uploads, and the in-memory upload parts. None of them is released while the stall lasts.

## Environment trigger: a PATH shim (no code injection)

Controllability is environment. Unlike the Monitor app, where the stall is injected inside
`aging_probe.py`, this app gets **no** code-level injection. The stall comes from the environment
only: `env/shims/convert-hang/convert`, a POSIX-sh `convert` placed first on the app's PATH (see its
README).

- With probability `CONVERT_HANG_PROB` = p, a **conversion** stalls for `CONVERT_HANG_S` (default
  36000 s, longer than the 8 h run), then runs the real convert with the original arguments. The
  `-version` probe never stalls. A stall there would hold no temp dir and would mix two footprints.
- The decision is reproducible: `hash(seed, k)` for the k-th conversion, with the counter in the run's
  private TMPDIR. Each decision is logged to `<run>/tmp/convert-hang.log`, so the judge can count
  injected stalls (`grep -c stall`) and compare them with `convert.inflight`.
- The stalled process is the shim itself, re-exec'd under `setpriv --pdeathsig KILL`. It keeps comm
  `convert` and PPid = app, with one `sleep` grandchild. Both die with the app.
- With p unset or 0 the shim is transparent. Checked: identical GIF bytes for the same argv,
  identical `-version` output, and identical stderr and exit code for a failing call.

| run | workload | app env | expected |
|---|---|---|---|
| R0 | `_control.py` (replica of reference/image_converter.jmx) | `TMPDIR` | no activation (original condition) |
| R0b | `_targeted_v1.py` (defaults) | `TMPDIR` | matched control: all gauges flat, same load as R1 |
| R1 | `_targeted_v1.py` (defaults) | `TMPDIR`, `PATH` shim, `CONVERT_HANG_PROB=0.02` | linear growth of threads, convert children, FDs, gif_creator dirs |
| R2 | R1 workload and env, `variant: repaired` | same as R1 | repair should bound or release them |

Every run gets `TMPDIR={tmpdir}` (`<run dir>/tmp`). The `tmp.gif_creator.*` and `tmp.magick.*` gauges
then count only this app's entries, and the shim's counter restarts at 0.

## Trigger mapping

| stream (endpoint / phase) | request | card element |
|---|---|---|
| `create_gif` / `gif` | POST /create-gif at a fixed **open-loop** rate `--rate` (1/s), cycling round-robin (seeded start) over 4 fixed valid requests: 4 photos → 320x240, 100 ms; 3 photos → 240x240, 200 ms, reverted; 5 photos → 320x320, 150 ms; 2 photos → 400x300, 500 ms, reverted. All are JPEGs from env/images, 1.7 MB in total, ~425 KB per request on average | `mechanism.trigger`: repeated valid POSTs reaching app.py:66 and :126. Each stalled conversion adds +1 thread, +1 `convert` child (+1 `sleep`), +3 FDs, +1 gif_creator dir (~425 KB) |
| `create_gif` / `timeout` | the same request when its convert stalled: cut by the client after `--timeout` (20 s), logged with status 0 and latency ≈ 20 000 ms | expected symptom: "client-side timeouts / unanswered requests for the stalled requests" |
| `index` / `probe` | GET / every `--index-interval` s (5 s) | path that never runs convert. Should stay flat until exhaustion ("unresponsive service") |

Each request kind has exactly one response size in every run: 181 066, 183 196, 191 774 and 230 287 B,
with and without the shim. Healthy latency does not depend on the number of stalls (convert does not
scan the process table), so in R1 the healthy p50 should stay flat (~0.2–0.3 s).

## Client design (stalled requests must not reduce the offered load)

- **Open loop.** One request every 1/rate s, each as its own asyncio task. A stalled request never
  delays the schedule. If the client falls behind, it skips missed slots without bursting (`late` in
  workload.log).
- **Bounded in-flight.** At most `--max-inflight` (32) requests are outstanding. A slot that finds the
  cap reached is not sent. It is logged as status 0, phase `dropped`. Expected in-flight from stalls
  is r·p·timeout = 1 × 0.02 × 20 = 0.4.
- **Timeout 20 s.** Healthy calls take 0.2–0.3 s (max 0.32 s in the smokes). On timeout httpx closes
  the connection, and the stuck server thread keeps the socket (CLOSE_WAIT), so the socket FD stays
  counted. Timeouts are rows with `status=0`, `phase=timeout`, and they count as errors in
  `analyze_run.py`.
- **End of run.** The stream stops at `--duration`. Outstanding requests finish or time out (≤ 25 s
  drain, within the harness's +120 s grace).

## Sizing for 8 h (28800 s), defaults `--rate 1`, p = 0.02

Measured per stall in the shim smoke (p = 0.3, 23 stalls): +1 thread, +1 `convert` child,
+1 `sleep` grandchild, +3 FDs (2 pipes + socket), +1 gif_creator dir of ~425 KB, and app RSS of
about +0.85 MB (thread plus in-memory upload parts). The shim plus its `sleep` cost ~410 KB PSS
(~1.9 MB RSS each, mostly shared). A healthy conversion takes ~0.25 s of CPU, with a 150 MB peak RSS
for a moment, one at a time.

| elapsed | conversions | stalls (≈ r·p·t, ±√) | proc.threads | proc.children(.convert) | proc.descendants | proc.fds | gif_creator dirs / bytes | app RSS | host tasks added |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 1–2 | 0 | 0 | 5–6 | 0 | ~33 MB | 0 |
| 1 h | 3 600 | ~72 ± 8 | ~74 | ~72 | ~144 | ~220 | ~72 / ~31 MB | ~95 MB | ~216 |
| 8 h | 28 800 | ~576 ± 24 | ~578 | ~576 | ~1 152 | ~1 735 | ~576 / ~245 MB | ~520 MB | ~1 730 |

Extra at 8 h: ~0.5 GB app RSS, ~0.24 GB for the shim+sleep pairs, and ~245 MB of disk under
`<run>/tmp`. Host limits: `ulimit -u` 31 613 (~6 % used), `ulimit -n` 1 048 576, threads-max 63 227,
pid_max 4 194 304, 7.9 GB RAM, 937 GB free disk, and no ImageMagick `time` policy in
`/etc/ImageMagick-6/policy.xml`. This leaves wide margin, so the run should show clean linear growth
for the whole 8 h. Exhaustion is not the intended end point. To approach it, raise p.

**Check before the campaign** (as for the Monitor): if the isolated machine's soft `ulimit -n` is
1024, the app hits EMFILE at ~340 stalls (~4.7 h). From then on POSTs fail with 500 or with connect
errors. Raise it (e.g. 65536) in the launching shell, or record that the limit was reached on purpose
(the harness records `limits` in meta.json). Also check that `setpriv` and `flock` exist (util-linux),
and that `/etc/ImageMagick-6/policy.xml` sets no `time` limit.

client.csv volume: ~1.2 rows/s → ~35 k rows over 8 h. CPU: about 30 % of the app core (`--app-cpus 0`).

## Smoke runs (90 s, instrumented, port 5000, `TMPDIR={tmpdir}`; dirs deleted afterwards)

| run | requests | errors | gauges at 90 s |
|---|---|---|---|
| control (`_control.py`) | 361 create_gif (~4/s, 10 closed-loop workers), all 200, p50 2.5 s, max 4.5 s | 0 | plateau: inflight 9–10, threads 12, children 8–10, fds 34–36, gif_creator 9–10 dirs. Back to threads 2 / children 0 / fds 5 / dirs 0 after the run. `convert_resolved=/usr/bin/convert` |
| targeted, no shim | 90 create_gif (1.00/s), all 200, p50 212 ms, max 296 ms; 18 index, p50 2 ms | 0 | inflight 0, threads 2, children 0, fds 6, dirs 0, all flat. `convert_resolved=/usr/bin/convert` |
| targeted, shim `CONVERT_HANG_PROB=0.3` | 90 create_gif (no drops, no late slots): 67 × 200 (p50 246 ms) and 23 × timeout (status 0, ~20 003 ms); 18 index, all 200 (p50 2 ms) | 25.6 % (= the 23 stalls) | 23 `stall` lines in convert-hang.log out of 90 conversions (the counter counted only conversions) = convert.inflight = create_gif.inflight = 23. threads 1 → 25, children.convert 0 → 23, descendants 0 → 46, fds 5 → 75, gif_creator 0 → 23 dirs (9.8 MB), RSS 32.2 → 53.3 MB. All monotonic. `convert_resolved=<root>/env/shims/convert-hang/convert` |

After the shim smoke, no shim or `sleep 36000` process was left, and port 5000 was free.

## Commands (8 h)

```bash
# R0 control (reference/image_converter.jmx replica: 10 threads, no timers, 5 random photos, random size/delay)
.venv/bin/python harness/run_diagnostic.py --app apps/image-converter-flask/instrumented --instrument \
    --workload workloads/bax-flask-image-converter-F1_control.py --duration 28800 --label R0_ic_F1_control \
    --app-cpus 0 --load-cpus 1-3 --app-env 'TMPDIR={tmpdir}'

# R0b matched control (same workload, no shim)
.venv/bin/python harness/run_diagnostic.py --app apps/image-converter-flask/instrumented --instrument \
    --workload workloads/bax-flask-image-converter-F1_targeted_v1.py --duration 28800 --label R0b_ic_F1_matched \
    --app-cpus 0 --load-cpus 1-3 --app-env 'TMPDIR={tmpdir}'

# R1 targeted (same workload + PATH shim)
.venv/bin/python harness/run_diagnostic.py --app apps/image-converter-flask/instrumented --instrument \
    --workload workloads/bax-flask-image-converter-F1_targeted_v1.py --duration 28800 --label R1_ic_F1_targeted_v1 \
    --app-cpus 0 --load-cpus 1-3 --app-env 'TMPDIR={tmpdir}' \
    --app-env 'PATH={root}/env/shims/convert-hang:{path}' --app-env CONVERT_HANG_PROB=0.02
```

R2 is R1 with `--app apps/image-converter-flask/repaired`. The campaign definition is in
`apps/image-converter-flask/campaign.yaml`. The shim does not depend on `--instrument`; the
instrumentation only observes. The R0 control drives ~4 req/s in a closed loop with 10 concurrent
conversions of 5 photos at 400–800 px, which is the original condition. Without a stall, the finding
cannot activate under it: every child returns and every temp dir is removed.
