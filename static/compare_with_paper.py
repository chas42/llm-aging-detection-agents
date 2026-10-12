"""I1 sanity check: compares our paper-suite results with the previous paper's raw outputs, rule by rule.

    .venv/bin/python static/compare_with_paper.py      # writes identification/SANITY.md

Paper sources (read-only): generated-software-llm/code-review-reports and
baxbench_LLM_code/static-analysis-results. Each paper file is mapped to (corpus app, our suite).
"""
import collections
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT.parent / "generated-software-llm" / "code-review-reports"
BAX = ROOT.parent / "baxbench_LLM_code" / "static-analysis-results"
GROUPS = {"Python_FastAPI": "bax-fastapi", "Python_Flask": "paper-flask",
          "JavaScript_Express": "paper-express", "Rust_Actix": "paper-actix"}


def scenario(name: str) -> str | None:
    n = name.lower()
    for key, sc in (("uptime", "uptime"), ("monitor", "monitor"), ("credit", "credit-card"), ("image", "image-converter")):
        if key in n:
            return sc
    return None


def suite(path: str) -> str | None:
    p = path.lower()
    if "slowql" in p:
        return "slowql"
    if "semgrep" in p:
        return ("semgrep-security-audit" if "security_audit" in p else
                "semgrep-flask" if "flask_specific" in p else "semgrep-default")
    if "community" in p:
        return "codeql-community-audit" if "audit" in p else "codeql-community-default"
    for key in ("security-experimental", "quality-extended", "security-quality"):
        if key in p:
            return f"codeql-{key}"
    return None


def paper_files():
    for f in GEN.rglob("*.sarif"):
        rel = str(f.relative_to(GEN))
        group, sc, st = GROUPS.get(rel.split("/")[0]), scenario(f.name), suite(rel)
        if group and sc and st:
            yield f"{group}-{sc}", st, f
    for f in BAX.rglob("*.sarif"):
        rel = str(f.relative_to(BAX))
        m = re.search(r"(django|aiohttp|fastapi|flask)", rel.lower())
        sc, st = scenario(rel), suite(rel)
        if m and sc and st:
            yield f"bax-{m.group(1)}-{sc}", st, f


def rules(f: Path) -> collections.Counter:
    raw = gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_bytes()
    d = json.loads(raw)
    return collections.Counter(r.get("ruleId") for run in d.get("runs", []) for r in run.get("results", []))


def main() -> int:
    rows, ok = [], 0
    for app, st, f in sorted(paper_files(), key=lambda x: (x[0], x[1])):
        ours_f = ROOT / "identification" / app / "tools" / f"{st}.sarif.gz"
        if not ours_f.exists():
            rows.append((app, st, sum(rules(f).values()), None, "não rodado", f))
            continue
        p, o = rules(f), rules(ours_f)
        same = p == o
        ok += same
        diff = "" if same else f"paper {dict(p)} · agora {dict(o)}"
        rows.append((app, st, sum(p.values()), sum(o.values()), "igual" if same else diff, f))
    lines = ["# Sanidade I1: suítes do paper × reexecução", "",
             f"{ok} de {len(rows)} pares (app, suíte) idênticos regra por regra.", "",
             "| app | suíte | paper | agora | resultado |", "|---|---|---|---|---|"]
    for app, st, p, o, res, f in rows:
        lines.append(f"| {app} | {st} | {p} | {'-' if o is None else o} | {res[:200]} |")
    out = ROOT / "identification" / "SANITY.md"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
