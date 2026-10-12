"""Summarizes the tool channel over the corpus -> identification/TOOLS_SUMMARY.md

    .venv/bin/python identification/summarize_tools.py

Per app: findings per category (security / quality / performance / reliability / resource-leak /
aging) and per source; then every aging-rule and LeakAudit finding with its code line, for review.
"""
import collections
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATS = ["aging", "resource-leak", "performance", "reliability", "quality", "security"]


def main() -> int:
    apps = yaml.safe_load((ROOT / "corpus" / "manifest.yaml").read_text())["apps"]
    rows, details, totals = [], [], collections.Counter()
    by_rule = collections.Counter()
    for a in apps:
        f = ROOT / "identification" / a["id"] / "tools" / "tool_findings.json"
        if not f.exists():
            rows.append(f"| {a['id']} | {a['group']} | " + " | ".join("–" for _ in CATS) + " | não rodado |")
            continue
        findings = json.loads(f.read_text())
        cats = collections.Counter(x["category"] for x in findings)
        totals.update(cats)
        rows.append(f"| {a['id']} | {a['group']} | " + " | ".join(str(cats.get(c, 0)) for c in CATS)
                    + f" | {len(findings)} |")
        src = ROOT / "corpus" / a["id"] / "src"
        for x in findings:
            if x["category"] in ("aging", "resource-leak"):
                by_rule[x["rule"]] += 1
                code = ""
                try:
                    code = (src / x["file"]).read_text(errors="ignore").splitlines()[x["line"] - 1].strip()[:110]
                except (OSError, IndexError, TypeError):
                    pass
                details.append(f"| {a['id']} | {x['rule']} | `{x['file']}:{x['line']}` | `{code}` |")
    lines = ["# Canal de ferramentas: resumo do corpus", "",
             "Contagem de achados por categoria (`aging` = regras novas v1.0; `resource-leak` = LeakAudit;",
             "as demais vêm das suítes do paper, categorizadas pelas tags das regras).", "",
             "| app | grupo | " + " | ".join(CATS) + " | total |", "|---|---|" + "---|" * (len(CATS) + 1)]
    lines += rows
    lines += ["| **total** | | " + " | ".join(f"**{totals.get(c, 0)}**" for c in CATS)
              + f" | **{sum(totals.values())}** |", "",
              "## Achados de aging e de vazamento de recurso (para revisão)", "",
              "| app | regra | local | código |", "|---|---|---|---|"] + details
    lines += ["", "## Por regra", "", "| regra | achados |", "|---|---|"]
    lines += [f"| {r} | {n} |" for r, n in by_rule.most_common()]
    out = ROOT / "identification" / "TOOLS_SUMMARY.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"{out.relative_to(ROOT)}: {sum(totals.values())} findings, {totals.get('aging', 0)} aging, "
          f"{totals.get('resource-leak', 0)} resource-leak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
