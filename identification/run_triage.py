"""Runs the aging-finding-triage agent for one app, isolated like the AI reviews.

    .venv/bin/python identification/run_triage.py --app bax-fastapi-uptime

The sandbox (temporary directory outside the project) holds only what the agent may see:
    app/        copy of corpus/<app>/src
    inputs/     the VALID ai_review_r*.md of this app, tool_findings.json, _schema.yaml
    out/        where the agent writes cards and triage.md
so it cannot read the manual finding card, the previous paper's reports or other apps.
Results are copied to identification/<app>/triage/ (cards stay there until a human promotes
them to findings/).
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / ".claude" / "agents" / "aging-finding-triage.md"
DENY = ["Bash", "NotebookEdit", "WebFetch", "WebSearch", "KillShell", "Task", "Agent"]
ALLOW = ["Read", "Grep", "Glob", "Write"]


def tree_hash(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    ident = ROOT / "identification" / args.app
    reviews = []
    for md in sorted(ident.glob("ai_review_r[0-9].md")):
        meta = json.loads(md.with_suffix(".json").read_text())
        if meta.get("valid"):
            reviews.append(md)
    tools = ident / "tools" / "tool_findings.json"
    if not reviews or not tools.exists():
        print(f"need at least one valid AI review and tool_findings.json in {ident}", file=sys.stderr)
        return 2
    out_dir = ident / "triage"
    if out_dir.exists():
        print(f"{out_dir.relative_to(ROOT)} exists; delete it to rerun", file=sys.stderr)
        return 2

    instructions = re.sub(r"^---\n.*?\n---\n", "", AGENT.read_text(), flags=re.S)
    with tempfile.TemporaryDirectory(prefix="aging-triage-") as tmp:
        box = Path(tmp)
        shutil.copytree(ROOT / "corpus" / args.app / "src", box / "app")
        (box / "inputs").mkdir()
        for md in reviews:
            shutil.copy(md, box / "inputs" / md.name)
        shutil.copy(tools, box / "inputs" / "tool_findings.json")
        shutil.copy(ROOT / "findings" / "_schema.yaml", box / "inputs" / "_schema.yaml")
        (box / "out").mkdir()
        before = {"app": tree_hash(box / "app"), "inputs": tree_hash(box / "inputs")}
        prompt = (instructions + "\n\n## This invocation\n\n"
                  f"- Application id: `{args.app}`\n- Application source: `app/`\n"
                  f"- AI review reports: {', '.join(f'`inputs/{m.name}`' for m in reviews)}\n"
                  "- Tool findings: `inputs/tool_findings.json`\n- Card schema: `inputs/_schema.yaml`\n"
                  "- Output directory: `out/` (write the cards and triage.md there, nowhere else)\n")
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--no-session-persistence",
               "--allowedTools", *ALLOW, "--disallowedTools", *DENY, "--permission-mode", "acceptEdits"]
        if args.model:
            cmd += ["--model", args.model]
        t0 = time.time()
        p = subprocess.run(cmd, cwd=box, capture_output=True, text=True, timeout=args.timeout)
        meta = {"app": args.app, "reviews": [m.name for m in reviews], "seconds": round(time.time() - t0, 1),
                "exit": p.returncode, "allowed_tools": ALLOW, "denied_tools": DENY,
                "unchanged": {"app": tree_hash(box / "app") == before["app"],
                              "inputs": tree_hash(box / "inputs") == before["inputs"]}}
        try:
            res = json.loads(p.stdout)
        except json.JSONDecodeError:
            res = {"result": "", "raw_stdout": p.stdout[-5000:], "stderr": p.stderr[-5000:]}
        for k in ("total_cost_usd", "num_turns", "modelUsage", "is_error", "subtype"):
            if k in res:
                meta[k] = res[k]
        shutil.copytree(box / "out", out_dir)
        (out_dir / "agent_reply.md").write_text(res.get("result", ""))
        meta["cards"] = sorted(f.name for f in out_dir.glob("*.yaml"))
        (out_dir / "run.json").write_text(json.dumps(meta, indent=1, default=str))
    print(f"{args.app}: exit={p.returncode} {meta['seconds']}s est_usage_usd={meta.get('total_cost_usd', '?')} "
          f"cards={meta['cards']} unchanged={meta['unchanged']}")
    return 0 if p.returncode == 0 and all(meta["unchanged"].values()) and meta["cards"] else 1


if __name__ == "__main__":
    sys.exit(main())
