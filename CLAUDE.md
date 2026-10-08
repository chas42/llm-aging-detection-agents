# aging-validation

Research pilot: validate static software-aging findings at runtime, then repair them. See README.md
for the workflow, layout and contracts, and docs/PLANO.md for the current plan and status.

- Subagents live in `.claude/agents/`; run the pipeline in this order: aging-instrumentation-agent →
  aging-workload-generator → harness/run_diagnostic.py (control + targeted) → aging-activation-judge
  (loop back to the workload generator on NOT_ACTIVATED, max 3 revisions) → aging-repair-agent ⇄
  aging-repair-reviewer → aging-functional-validator → re-run → aging-activation-judge (repair assessment).
- Never edit `apps/*/original/` or anything under `reference/`; agents write new sibling directories.
- `# AGING-PROBE` lines and `aging_probe.py` are measurement code: repairs must not touch them.
- Statistics are computed only by `harness/analyze_run.py`; agents interpret its output.
- Long diagnostic runs happen on an isolated machine via `deploy/` (pack.sh → setup.sh → preflight.sh →
  campaign_uptime_F4.sh → collect_results.sh); results come back as tarballs extracted into `runs/`.
  After changing apps/workloads/harness, rebuild the bundle with `bash deploy/pack.sh`.
- Use `.venv/bin/python` for everything. Long runs (≥10 min) go in the background.
- The sibling projects `../baxbench_LLM_code` and `../generated-software-llm` are read-only sources.
