"""AI channel of the identification stage: the previous paper's two-phase review, one isolated run.

    .venv/bin/python identification/run_ai_review.py --app bax-fastapi-uptime --rep 1
    .venv/bin/python identification/run_ai_review.py --app bax-fastapi-uptime --rep 1 --dry-run

Isolation (the reviewer must not see the paper's reports, finding cards or tool results):
  - each run happens in a fresh temporary directory OUTSIDE the project (no parent CLAUDE.md),
    containing only a copy of corpus/<app>/src (as ./app) and .claude/settings.json enabling
    the VoltAgent plugins;
  - Bash/Write/Edit and web tools are denied by the CLI (the paper only asked for it in the
    prompt), and the directory hash is checked before/after to prove nothing was modified;
  - no session persistence; one fresh session per repetition.

Output, in identification/<app>/:
  ai_review_r<N>.md    the final answer (performance-engineer report + both Phase-1 handoffs)
  ai_review_r<N>.json  metadata: prompt template, model usage, cost, duration, isolation checks
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "identification" / "prompts"
DENY = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "KillShell"]
ALLOW = ["Read", "Grep", "Glob", "Task", "Agent"]


def tree_hash(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def plugin_versions() -> dict:
    out = {}
    cache = Path.home() / ".claude" / "plugins" / "cache" / "voltagent-subagents"
    for plugin in ("voltagent-qa-sec", "voltagent-lang"):
        versions = sorted(p.name for p in (cache / plugin).iterdir()) if (cache / plugin).is_dir() else []
        out[plugin] = versions[-1] if versions else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--rep", type=int, required=True, help="repetition number (1..3)")
    ap.add_argument("--model", default=None, help="orchestrator model (default: CLI default; recorded)")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--dry-run", action="store_true", help="build the sandbox and list enabled plugins, no model call")
    args = ap.parse_args()

    manifest = {a["id"]: a for a in yaml.safe_load((ROOT / "corpus" / "manifest.yaml").read_text())["apps"]}
    app = manifest.get(args.app)
    if not app:
        print(f"unknown app {args.app}", file=sys.stderr)
        return 2
    template = PROMPTS / f"{app['framework']}.txt"
    prompt = template.read_text().replace("{target}", "app")
    out_dir = ROOT / "identification" / args.app
    out_dir.mkdir(parents=True, exist_ok=True)
    md, meta_path = out_dir / f"ai_review_r{args.rep}.md", out_dir / f"ai_review_r{args.rep}.json"
    if md.exists() and not args.dry_run:
        print(f"{md.relative_to(ROOT)} already exists; delete it to rerun", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="aging-review-") as tmp:
        box = Path(tmp)
        shutil.copytree(ROOT / "corpus" / args.app / "src", box / "app")
        (box / ".claude").mkdir()
        shutil.copy(ROOT / ".claude" / "settings.json", box / ".claude" / "settings.json")
        before = tree_hash(box / "app")
        meta = {"app": args.app, "rep": args.rep, "framework": app["framework"],
                "template": str(template.relative_to(ROOT)), "sandbox": "<tmp>/app",
                "plugins": plugin_versions(), "allowed_tools": ALLOW, "denied_tools": DENY,
                "app_hash_before": before}

        if args.dry_run:
            agents = subprocess.run(["claude", "plugin", "list"], cwd=box, capture_output=True, text=True, timeout=120)
            print(agents.stdout[-3000:] or agents.stderr[-3000:])
            print(f"--- prompt ({template.name}, {len(prompt)} chars) ---\n{prompt[:600]}...")
            return 0

        cmd = ["claude", "-p", prompt, "--output-format", "json", "--no-session-persistence",
               "--allowedTools", *ALLOW, "--disallowedTools", *DENY]
        if args.model:
            cmd += ["--model", args.model]
        t0 = time.time()
        p = subprocess.run(cmd, cwd=box, capture_output=True, text=True, timeout=args.timeout)
        meta["seconds"] = round(time.time() - t0, 1)
        meta["exit"] = p.returncode
        meta["app_hash_after"] = tree_hash(box / "app")
        meta["app_unchanged"] = meta["app_hash_after"] == before
        try:
            res = json.loads(p.stdout)
        except json.JSONDecodeError:
            res = {"result": "", "raw_stdout": p.stdout[-5000:], "stderr": p.stderr[-5000:]}
        for k in ("total_cost_usd", "num_turns", "duration_ms", "modelUsage", "usage", "is_error", "subtype"):
            if k in res:
                meta[k] = res[k]
        md.write_text(res.get("result", ""))
        meta_path.write_text(json.dumps(meta, indent=1, default=str))
        print(f"{args.app} r{args.rep}: exit={p.returncode} {meta['seconds']}s "
              f"cost=${meta.get('total_cost_usd', '?')} unchanged={meta['app_unchanged']} -> {md.relative_to(ROOT)}")
        return 0 if p.returncode == 0 and meta["app_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
