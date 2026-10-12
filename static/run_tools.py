"""Tool channel of the identification stage: runs the static-analysis tools on corpus apps.

    .venv/bin/python static/run_tools.py --app bax-fastapi-uptime          # paper suites + aging rules
    .venv/bin/python static/run_tools.py --all --only paper                 # every app, paper suites only

Output per app, in identification/<app>/tools/:
    *.sarif.gz / *.json     raw tool outputs (one file per tool x suite; SARIF gzipped, since
                            Semgrep embeds its whole ruleset: ~2 MB per file uncompressed)
    tool_findings.json      every result normalized to one schema (see normalize())
    run.json                tool versions, commands, exit codes, timings

Suites reproduce the previous paper (generated-software-llm/code-review-reports and
baxbench_LLM_code/static-analysis-results), with the same CodeQL CLI and pack versions
(see static/install_tools.sh). Semgrep registry rulesets (p/default, p/security-audit) are NOT
versioned by the registry: a snapshot taken on 2026-10-12 lives in static/rules/registry/ and is
used by default (set AGING_SEMGREP_LIVE=1 to use the live registry instead). Against the paper's
run, p/default and p/security-audit already differed (registry drift); p/flask matched.
"""
import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aging_rules import rules_version, scan as scan_aging  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CODEQL = ROOT / "tools" / "codeql" / "codeql"
SEMGREP = ROOT / ".venv-tools" / "bin" / "semgrep"
SLOWQL = ROOT / ".venv-tools" / "bin" / "slowql"
LEAKAUDIT = ROOT / ".venv-tools" / "bin" / "leakaudit"
COMMUNITY = "0.2.1"
REGISTRY = ROOT / "static" / "rules" / "registry"  # snapshot of the Semgrep registry rulesets

# (output name, query suite) per language, as in the previous paper
CODEQL_SUITES = {
    "python": [
        ("codeql-security-quality", "codeql/python-queries:codeql-suites/python-security-and-quality.qls"),
        ("codeql-security-experimental", "codeql/python-queries:codeql-suites/python-security-experimental.qls"),
        ("codeql-quality-extended", "codeql/python-queries:codeql-suites/python-code-quality-extended.qls"),
        ("codeql-community-default", f"githubsecuritylab/codeql-python-queries@{COMMUNITY}:suites/python.qls"),
        ("codeql-community-audit", f"githubsecuritylab/codeql-python-queries@{COMMUNITY}:suites/python-audit.qls"),
    ],
    "javascript": [
        ("codeql-security-quality", "codeql/javascript-queries:codeql-suites/javascript-security-and-quality.qls"),
        ("codeql-community-default", f"githubsecuritylab/codeql-javascript-queries@{COMMUNITY}:suites/javascript.qls"),
    ],
    "rust": [
        ("codeql-security-quality", "codeql/rust-queries:codeql-suites/rust-security-and-quality.qls"),
    ],
}
SEMGREP_CONFIGS = {
    "python": [("semgrep-default", "p/default"), ("semgrep-security-audit", "p/security-audit")],
    "javascript": [("semgrep-default", "p/default")],
    "rust": [("semgrep-default", "p/default")],
}
SEMGREP_FRAMEWORK_CONFIGS = {"flask": [("semgrep-flask", "p/flask")]}  # paper: *_flask_specific.sarif
SLOWQL_LANGS = {"python": "*.py", "javascript": "*.js"}
SRC_EXT = {"python": ["*.py"], "javascript": ["*.js", "*.mjs", "*.cjs", "*.ts"], "rust": ["*.rs"]}


def run(cmd: list[str], log: list[dict], cwd: Path | None = None, timeout: int = 1800) -> int:
    t = time.time()
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    log.append({"cmd": [str(c) for c in cmd], "exit": p.returncode, "seconds": round(time.time() - t, 1),
                "stderr_tail": p.stderr[-1500:] if p.returncode else ""})
    return p.returncode


# ----------------------------------------------------------------------------- tools

def run_codeql(app: dict, src: Path, out: Path, log: list[dict]) -> None:
    lang = app["language"]
    with tempfile.TemporaryDirectory(prefix="codeql-db-") as tmp:
        db = Path(tmp) / "db"
        if run([CODEQL, "database", "create", db, f"--language={lang}", f"--source-root={src}",
                "--build-mode=none", "--overwrite", "--threads=0"], log):
            return
        for name, suite in CODEQL_SUITES[lang]:
            run([CODEQL, "database", "analyze", db, suite, "--format=sarif-latest",
                 f"--output={out / (name + '.sarif')}", "--threads=0", "--sarif-add-snippets"], log)


def run_semgrep(app: dict, src: Path, out: Path, log: list[dict], configs: list[tuple[str, str]]) -> None:
    for name, config in configs:
        cwd = None
        snapshot = REGISTRY / (config.replace("/", "-") + ".yaml") if config.startswith("p/") else None
        if snapshot and snapshot.exists() and os.environ.get("AGING_SEMGREP_LIVE") != "1":
            # run from the snapshot dir so rule ids carry no path prefix (same ids as the registry)
            config, cwd = snapshot.name, REGISTRY
        run([SEMGREP, "scan", "--config", config, "--sarif", "--output", (out / f"{name}.sarif").resolve(),
             "--metrics=off", "--disable-version-check", "--quiet", src.resolve()], log, cwd=cwd)


def run_slowql(app: dict, src: Path, out: Path, log: list[dict]) -> None:
    pattern = SLOWQL_LANGS.get(app["language"])
    files = sorted(src.rglob(pattern)) if pattern else []
    if not files:
        return
    with tempfile.TemporaryDirectory(prefix="slowql-") as tmp:
        run([SLOWQL, *files, "--non-interactive", "--no-intro", "--no-cache", "--dialect", "sqlite",
             "--export", "json", "sarif", "--out", tmp], log)
        for f in Path(tmp).iterdir():  # timestamped names -> stable names
            shutil.copy(f, out / ("slowql" + f.suffix))


def run_leakaudit(app: dict, src: Path, out: Path, log: list[dict]) -> None:
    if app["language"] != "python":
        return
    t = time.time()
    p = subprocess.run([str(LEAKAUDIT), str(src), "--json"], capture_output=True, text=True, timeout=600)
    log.append({"cmd": [str(LEAKAUDIT), str(src), "--json"], "exit": p.returncode,
                "seconds": round(time.time() - t, 1), "stderr_tail": p.stderr[-1500:] if p.returncode else ""})
    if p.stdout.strip():
        (out / "leakaudit.json").write_text(p.stdout)


def run_aging(app: dict, src: Path, out: Path, log: list[dict]) -> None:
    """Aging rule set: semgrep + file-level conditions (static/aging_rules.py), JSON output."""
    t = time.time()
    kept, dropped, raw = scan_aging(src, app["language"])
    for item in kept + dropped:
        item["path"] = str(Path(item["path"]).relative_to(src))
    (out / "semgrep-aging.json").write_text(json.dumps(
        {"rules_version": rules_version(), "kept": kept, "dropped": dropped,
         "errors": raw.get("errors", [])}, indent=1))
    log.append({"cmd": ["aging_rules.scan", str(src), app["language"]], "exit": 0,
                "seconds": round(time.time() - t, 1), "stderr_tail": ""})


# ----------------------------------------------------------------------------- normalization

def _level(rule: dict, result: dict) -> str:
    return result.get("level") or rule.get("defaultConfiguration", {}).get("level", "warning")


def sarif_stem(path: Path) -> str:
    return path.name.removesuffix(".gz").removesuffix(".sarif")


def normalize_sarif(path: Path, src: Path) -> list[dict]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    data = json.loads(raw)
    stem = sarif_stem(path)
    out = []
    for r in data.get("runs", []):
        driver = r["tool"]["driver"]
        rules = {x.get("id"): x for x in driver.get("rules", [])}
        for ext in r["tool"].get("extensions", []):
            rules.update({x.get("id"): x for x in ext.get("rules", [])})
        for res in r.get("results", []):
            rule = rules.get(res.get("ruleId"), {})
            loc = (res.get("locations") or [{}])[0].get("physicalLocation", {})
            uri = loc.get("artifactLocation", {}).get("uri", "")
            for prefix in (str(src) + "/", src.as_uri() + "/"):
                uri = uri.removeprefix(prefix)
            tags = rule.get("properties", {}).get("tags", [])
            out.append({
                "tool": driver.get("name"), "source": stem, "rule": res.get("ruleId"),
                "level": _level(rule, res), "file": uri, "line": loc.get("region", {}).get("startLine"),
                "message": res.get("message", {}).get("text", "")[:500], "tags": tags,
                "category": categorize(stem, res.get("ruleId") or "", tags),
            })
    return out


def categorize(source: str, rule: str, tags: list[str]) -> str:
    """Coarse category; 'aging' only for our own rule set, the rest is what the tool says it is."""
    t = " ".join(tags).lower() + " " + rule.lower()
    if source.startswith("semgrep-aging"):
        return "aging"
    if "security" in t or "cwe" in t or "injection" in t:
        return "security"
    if source == "leakaudit":
        return "resource-leak"
    if any(k in t for k in ("performance", "efficiency", "resource", "leak")):
        return "performance"
    if any(k in t for k in ("reliability", "correctness", "error-handling")):
        return "reliability"
    return "quality"


def collect(out: Path, src: Path) -> list[dict]:
    findings = []
    for f in sorted([*out.glob("*.sarif"), *out.glob("*.sarif.gz")]):
        if sarif_stem(f) == "slowql":
            continue  # normalized from its JSON below (richer)
        findings += normalize_sarif(f, src)
    sj = out / "slowql.json"
    if sj.exists():
        for issue in json.loads(sj.read_text()).get("issues", []):
            loc = issue.get("location", {}) or {}
            findings.append({"tool": "SlowQL", "source": "slowql", "rule": issue.get("rule_id") or issue.get("id"),
                             "level": issue.get("severity"), "file": Path(loc.get("file") or "").name or None,
                             "line": loc.get("line"), "message": (issue.get("message") or "")[:500],
                             "tags": [issue.get("dimension", "")],
                             "category": categorize("slowql", str(issue.get("rule_id", "")), [issue.get("dimension", "")])})
    aj = out / "semgrep-aging.json"
    if aj.exists():
        data = json.loads(aj.read_text())
        for x in data["kept"]:
            findings.append({"tool": f"aging-rules {data['rules_version']}", "source": "semgrep-aging",
                             "rule": x["rule"], "level": x["severity"], "file": x["path"], "line": x["line"],
                             "message": x["message"], "tags": [x["class"]], "category": "aging",
                             "aging_class": x["class"]})
    lj = out / "leakaudit.json"
    if lj.exists():
        for x in json.loads(lj.read_text()).get("findings", []):
            findings.append({"tool": "LeakAudit", "source": "leakaudit", "rule": x.get("rule"),
                             "level": x.get("severity"), "file": str(Path(x["file"]).relative_to(src))
                             if x.get("file", "").startswith(str(src)) else x.get("file"),
                             "line": x.get("line"), "message": x.get("message", "")[:500], "tags": [],
                             "category": "resource-leak"})
    return findings


# ----------------------------------------------------------------------------- main

def analyze(app: dict, only: str) -> dict:
    src = ROOT / "corpus" / app["id"] / "src"
    out = ROOT / "identification" / app["id"] / "tools"
    out.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []
    t0 = time.time()
    if only in ("all", "paper"):
        run_codeql(app, src, out, log)
        run_semgrep(app, src, out, log, SEMGREP_CONFIGS[app["language"]]
                    + SEMGREP_FRAMEWORK_CONFIGS.get(app["framework"], []))
        run_slowql(app, src, out, log)
        run_leakaudit(app, src, out, log)
    if only in ("all", "aging"):
        run_aging(app, src, out, log)
    for f in out.glob("*.sarif"):  # fresh outputs: drop any stale .gz, then compress
        (out / (f.name + ".gz")).unlink(missing_ok=True)
    findings = collect(out, src)
    for f in out.glob("*.sarif"):
        (out / (f.name + ".gz")).write_bytes(gzip.compress(f.read_bytes(), mtime=0))
        f.unlink()
    (out / "tool_findings.json").write_text(json.dumps(findings, indent=1))
    versions = (ROOT / "tools" / "versions.txt").read_text() if (ROOT / "tools" / "versions.txt").exists() else ""
    summary = {"app": app["id"], "seconds": round(time.time() - t0, 1), "versions": versions, "commands": log,
               "counts": {}}
    for f in findings:
        summary["counts"][f["source"]] = summary["counts"].get(f["source"], 0) + 1
    (out / "run.json").write_text(json.dumps(summary, indent=1))
    failed = [c for c in log if c["exit"] not in (0, 1)]  # semgrep/slowql use 1 for "findings present"
    print(f"{app['id']:32s} {len(findings):4d} findings  {summary['counts']}"
          + (f"  !! {len(failed)} command(s) failed, see run.json" if failed else ""), flush=True)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--app", action="append", help="corpus app id (repeatable)")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--only", choices=["all", "paper", "aging"], default="all")
    args = ap.parse_args()
    manifest = {a["id"]: a for a in yaml.safe_load((ROOT / "corpus" / "manifest.yaml").read_text())["apps"]}
    ids = list(manifest) if args.all else args.app
    unknown = [i for i in ids if i not in manifest]
    if unknown:
        print(f"unknown app id(s): {unknown}", file=sys.stderr)
        return 2
    for i in ids:
        analyze(manifest[i], args.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
