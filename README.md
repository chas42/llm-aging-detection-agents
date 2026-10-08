# Static-analysis-guided aging validation and repair

Pilot of the agentic workflow proposed for the paper follow-up of
*"Software Aging in LLM-Generated Applications"* (Santos, Vitagliano, Natella, Andrade):
static aging findings are **validated at runtime** with targeted workloads and instrumentation
before being **repaired** by an LLM agent, and the repair is assessed by re-running the same
diagnostic workload.

```
finding card ──► instrumentation agent ──► workload generator ──► diagnostic run (harness)
                                                  ▲                       │
                                                  │ revise (≤3)           ▼
                                                  └──── NOT_ACTIVATED ◄─ activation judge
                                                                          │ ACTIVATED
                                                                          ▼
     before/after comparison ◄── re-run same workload ◄── functional validator ◄── repair agent ⇄ reviewer
```

Pilot target: **Uptime / FastAPI** (BaxBench, GPT-generated), Finding 4 + 5 of
`reference/static-analysis/uptime_fastAPI_report.md` (unbounded per-token service rows +
unpaginated read). The original 48 h JMeter workload used 4 fixed serviceIds, so this
finding was never activated in the original experiment.

## Layout

| path | content |
|---|---|
| `findings/` | finding cards (YAML): mechanism, trigger, activation criteria, expected symptoms |
| `apps/<app>/original/` | unmodified app (extracted from `baxbench_LLM_code` git HEAD) |
| `apps/<app>/instrumented/` | output of `aging-instrumentation-agent` |
| `apps/<app>/repaired/` | output of `aging-repair-agent` (applied on top of `instrumented/`) |
| `apps/<app>/repaired-reference/` | earlier manual-pipeline patch, for comparison only |
| `workloads/` | outputs of `aging-workload-generator` (targeted + control) |
| `harness/` | deterministic, non-LLM tooling: run, monitor, analyze, functional check |
| `runs/` | one directory per diagnostic run with all evidence |
| `reference/` | read-only copies: static-analysis report, BaxBench functional test, original JMeter plan |
| `.claude/agents/` | the six subagents of the pipeline |
| `deploy/` | bundle + scripts to run the campaign on an isolated machine ([tutorial](deploy/README.md)) |
| `docs/` | full documentation in Portuguese; start at [docs/README.md](docs/README.md); plan/status in `docs/PLANO.md` |

## Setup

```bash
python3 -m venv .venv          # if python3-venv is missing: python3 -m venv --without-pip .venv
                               # then: curl -sSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt -r apps/uptime-fastapi/original/requirements.txt
```

## Harness

```bash
# functional test on a temporary copy of an app (exit 0 = PASS)
.venv/bin/python harness/functional_check.py --app apps/uptime-fastapi/original --test reference/tests/test_uptime.py

# one diagnostic run (fresh DB each time); args after -- go to the workload
.venv/bin/python harness/run_diagnostic.py --app apps/uptime-fastapi/instrumented --instrument \
    --workload workloads/<script>.py --duration 3600 --label R1_targeted --app-cpus 0 --load-cpus 1-3 -- [workload args]

# trend analysis (Mann-Kendall + Hamed-Rao + Sen's slope) and before/after comparison
.venv/bin/python harness/analyze_run.py runs/<run>
.venv/bin/python harness/analyze_run.py runs/<before> --compare runs/<after>
```

Workload contract: `script --base-url URL --duration S --seed N --out client.csv [...]`,
writing `ts,endpoint,status,latency_ms,resp_bytes[,phase]`.
Instrumentation contract: JSONL at `$AGING_INSTRUMENT_LOG`, snapshot lines
`{"ts", "kind": "snapshot", "metrics": {...}}`, enabled only when `AGING_INSTRUMENT=1`.
