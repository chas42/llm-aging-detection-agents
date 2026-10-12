"""Runs the aging rule set (static/rules/aging/<language>.yaml) and applies its file-level conditions.

Semgrep cannot express "and nothing in this file removes it", so rules may declare in
metadata.aging:
    unless_file_matches: <regex>   drop the match if the file content matches
    if_file_matches: <regex>       keep the match only if the file content matches
Metavariables ($X) in the regex are replaced by the escaped text they captured in that match.
Semgrep CE no longer exports metavariables in its JSON, but it still interpolates them in the
message, so rules that need them end their message with tags like "[X=$X]"; scan() parses the
tags into metavars and strips them from the message.
Shared by static/run_tools.py (corpus runs) and static/test_aging_rules.py (rule tests), so both
see exactly the same behavior.
"""
import io
import json
import re
import subprocess
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMGREP = ROOT / ".venv-tools" / "bin" / "semgrep"
RULES = ROOT / "static" / "rules" / "aging"
LANG_FILE = {"python": "python.yaml", "javascript": "javascript.yaml", "rust": "rust.yaml"}


def rules_version() -> str:
    for line in (RULES / "TAXONOMY.md").read_text().splitlines():
        m = re.match(r"- (\d+\.\d+) \(", line)
        if m:
            version = m.group(1)
    return version


MV_TAG = re.compile(r"\s*\[([A-Z][A-Z0-9_]*)=([^\]]*)\]")


def _subst(regex: str, metavars: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(0)[1:]
        return re.escape(metavars[name]) if name in metavars else m.group(0)
    return re.sub(r"\$[A-Z][A-Z0-9_]*", repl, regex)


def strip_comments(text: str, language: str) -> str:
    """File conditions must not be satisfied by comments (e.g. '# TODO: CACHE.pop()')."""
    if language == "python":
        try:
            toks = [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type != tokenize.COMMENT]
            return tokenize.untokenize(toks)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return re.sub(r"(?m)^\s*#.*$", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", text)  # '//' after whitespace: keeps 'http://' in strings


def scan(target: Path, language: str) -> tuple[list[dict], list[dict], dict]:
    """Return (kept, dropped, raw_semgrep_json) for one target directory or file."""
    config = RULES / LANG_FILE[language]
    p = subprocess.run([str(SEMGREP), "scan", "--config", str(config), "--json", "--metrics=off",
                        "--disable-version-check", "--quiet", "--no-git-ignore", str(target)],
                       capture_output=True, text=True, timeout=900)
    if p.returncode not in (0, 1) or not p.stdout.strip():
        raise RuntimeError(f"semgrep failed ({p.returncode}): {p.stderr[-2000:]}")
    raw = json.loads(p.stdout)
    kept, dropped, cache = [], [], {}
    for r in raw.get("results", []):
        meta = r["extra"].get("metadata", {}).get("aging", {})
        message = r["extra"]["message"]
        metavars = {k: v.strip() for k, v in MV_TAG.findall(message)}
        message = MV_TAG.sub("", message).strip()
        path = r["path"]
        if path not in cache:
            cache[path] = strip_comments(Path(path).read_text(errors="ignore"), language)
        text = cache[path]
        reason = None
        if meta.get("unless_file_matches") and re.search(_subst(meta["unless_file_matches"], metavars), text):
            reason = "unless_file_matches"
        if meta.get("if_file_matches") and not re.search(_subst(meta["if_file_matches"], metavars), text):
            reason = "if_file_matches"
        if (meta.get("unless_file_matches") or meta.get("if_file_matches") or "") and \
                re.search(r"\$[A-Z]", (meta.get("unless_file_matches") or "") + (meta.get("if_file_matches") or "")) \
                and not metavars:
            raise RuntimeError(f"rule {r['check_id']} uses metavariables in a file condition but its "
                               "message has no [NAME=$NAME] tags")
        item = {"rule": r["check_id"].split(".")[-1], "path": path, "line": r["start"]["line"],
                "end_line": r["end"]["line"], "message": message,
                "class": meta.get("class"), "mechanism": meta.get("mechanism"),
                "severity": r["extra"].get("severity"),
                "metavars": metavars}
        (dropped if reason else kept).append(item | ({"dropped_by": reason} if reason else {}))
    # one finding per (rule, file, line): sequence patterns can report overlapping ranges
    seen, unique = set(), []
    for k in kept:
        key = (k["rule"], k["path"], k["line"])
        if key not in seen:
            seen.add(key)
            unique.append(k)
    return unique, dropped, raw
