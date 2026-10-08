# uptime-fastapi-F4 — targeted workload v1

Script: `workloads/uptime-fastapi-F4_targeted_v1.py` (control: `workloads/uptime-fastapi-F4_control.py`).
Finding: every POST /heartbeat with an unseen (serviceId, token) inserts a row into `heartbeats`
that is never deleted (F4); POST /services reads all of a token's rows with `fetchall()` under
the global `_db_lock` and builds one pydantic object per row (F5).

## Trigger mapping

| stream (endpoint / phase) | request | card element |
|---|---|---|
| `heartbeat` / `grow` | POST /heartbeat, serviceId `tenant-grow-1-svc-0000001, ...0000002, ...` (counter, never reused) at `--new-key-rate` | `mechanism.trigger` (distinct serviceIds) → `upsert_heartbeat` INSERT path; resource = rows per token, db file size |
| `heartbeat` / `refresh` | POST /heartbeat re-sending an already-registered growing serviceId (seeded RNG) | `upsert_heartbeat` ON CONFLICT path; realism (services keep beating); adds no rows |
| `heartbeat` / `prime`, `ref` | registration + cyclic heartbeats of the reference tokens' fixed services | keeps reference tokens at constant row count |
| `services_grow` / `probe` | POST /services `{"token":"tenant-grow-1"}` every `--probe-interval` s | symptom: `list_services_for_token` + `services` cost, latency and response size growing with rows |
| `services_ref4`, `services_ref200` / `refread` | POST /services for tokens with a constant 4 and 200 services, same interval | internal control: same read path, constant size → should stay flat; separates per-row growth from global drift (lock contention, B-tree depth, host noise) |

The endpoint names split the /services series by token because `harness/analyze_run.py` groups
by `endpoint`. All heartbeats share endpoint `heartbeat` (phase tells them apart).

## Pacing (why the server is not saturated from second 0)

Writes are serialized by `_db_lock` and cost ~17 ms each (fsync), so the write ceiling is
~55–60/s. Defaults put the write lock at ~25 % utilization: 10 (grow) + 2 (refresh) + 2 (ref)
= 14 heartbeats/s, plus 3 reads every 2 s. Every stream uses a shared slot pacer that drops
missed slots instead of bursting, with a fixed number of workers (2 for grow, 1 per other
stream), so a slowing server shows up as lower achieved rate (fewer new keys), never as a
client backlog. Per-request timeout 30 s.

## Defaults and sizing

| flag | default |
|---|---|
| `--new-key-rate` | 10 /s (distinct serviceIds) |
| `--grow-tokens` / `--grow-workers` | 1 / 2 |
| `--refresh-rate` | 2 /s |
| `--ref-sizes` / `--ref-hb-rate` | `4,200` / 2 /s |
| `--probe-interval` | 2 s (per probed token) |
| `--timeout` | 30 s |

Observed in the smoke run: ~90 bytes per row in the /services response.

| duration | rows for `tenant-grow-1` (≈ rate × t) | total rows | /services response | final/initial ratio (after 60 s warm-up, ~600 rows) |
|---|---|---|---|---|
| 3600 s | ~36 000 | ~36 200 | ~3.2 MB | ~60× |
| 7200 s | ~72 000 | ~72 200 | ~6.5 MB | ~120× |

db.sqlite3: roughly 100–120 bytes/row incl. PK and token index → ~4 MB (1 h) / ~8 MB (2 h);
no disk concern. For 7200 s keep `--new-key-rate 10` (the per-read cost at 72 k rows is
expected around ~1 s of server CPU, i.e. ~50 % of CPU 0 at a 2 s probe interval — strong
symptom without total saturation). If the 1 h run degrades too little, the first knob is
`--new-key-rate 20` (≈72 k rows in 1 h, still ≤ ~45 % write-lock utilization).

Client cost: one 3–7 MB response every 2 s is read but not parsed; negligible on CPUs 1-3.

## Smoke runs (60 s, port 8000, original app, uninstrumented)

- control `runs/20261004-191110_smoke_F4_control`: heartbeat 3144, services 3140, 0 errors,
  heartbeat median 16.9 ms, services 1.7 ms; table = 4 rows (pass1..pass4, 1 each).
- targeted `runs/20261004-191214_smoke_F4_targeted_v1`: heartbeat 994 (grow 564, prime 204,
  ref 113, refresh 113), services_grow 29, services_ref4 28, services_ref200 28; 0 errors.
  `tenant-grow-1` reached 564 rows (≈10/s after the ~3.5 s prime); services_grow response size
  grew 5.5 kB → 51 kB over the minute; ref tokens stayed at 4 and 200 rows.

## Commands

```bash
# targeted diagnostic (1 h)
.venv/bin/python harness/run_diagnostic.py --app apps/uptime-fastapi/instrumented --instrument \
    --workload workloads/uptime-fastapi-F4_targeted_v1.py --duration 3600 --label R1_F4_targeted_v1 \
    --app-cpus 0 --load-cpus 1-3 -- --new-key-rate 10 --probe-interval 2

# control (same duration; reproduces reference/uptime_test.jmx: 1 thread, no timers)
.venv/bin/python harness/run_diagnostic.py --app apps/uptime-fastapi/instrumented --instrument \
    --workload workloads/uptime-fastapi-F4_control.py --duration 3600 --label R1_F4_control \
    --app-cpus 0 --load-cpus 1-3
```

For 2 h replace `--duration 3600` with `7200` (same workload args). Note the control is a
closed loop without think time (as in the .jmx), so it drives ~52 heartbeats/s — near the
write ceiling — but over 4 fixed keys; that is the original condition, not a bug.

## Matched control (`--key-space N`, added manually on 04/10)

`--key-space N` (default 0 = off) makes the `grow` stream cycle over N fixed serviceIds per token
at the same rate (phase label `cycle`), and `refresh` picks among those N. Load, request shape,
tokens and probes are identical to the targeted run; only key diversity changes. Campaign run R0b
uses `--key-space 4` (table constant at 4 + 4 + 200 = 208 rows).
