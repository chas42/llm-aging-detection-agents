"""Tests for the aging rule set, through the same scan + file-level filter used on the corpus.

Test files live in static/rules/aging/tests/<language>/. Annotate the line BEFORE the target line:
    # ruleid: aging-py-unbounded-cache      -> that rule must match the next line
    # ok: aging-py-unbounded-cache          -> that rule must NOT match the next line
(`//` comments in JavaScript and Rust.) Each file is scanned on its own, so file-level conditions
(unless_file_matches) are tested per file.

    .venv/bin/python static/test_aging_rules.py            # all languages
    .venv/bin/python static/test_aging_rules.py python     # one language
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aging_rules import LANG_FILE, RULES, scan  # noqa: E402

ANNOT = re.compile(r"^\s*(?:#|//)\s*(ruleid|ok)\s*:\s*([\w-]+(?:\s*,\s*[\w-]+)*)")
EXT = {"python": "*.py", "javascript": "*.js", "rust": "*.rs"}


def expectations(path: Path) -> dict[tuple[str, int], str]:
    exp = {}
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines, start=1):
        m = ANNOT.match(line)
        if m:
            target = i + 1
            while target <= len(lines) and ANNOT.match(lines[target - 1]):
                target += 1  # stacked annotations apply to the first code line below them
            for rule in re.split(r"\s*,\s*", m.group(2)):
                exp[(rule, target)] = m.group(1)
    return exp


def main() -> int:
    langs = sys.argv[1:] or list(LANG_FILE)
    failures, checked, per_rule = 0, 0, defaultdict(lambda: [0, 0])
    for lang in langs:
        files = sorted((RULES / "tests" / lang).glob(EXT[lang]))
        if not files:
            print(f"[{lang}] no test files")
            continue
        declared = set(re.findall(r"^\s*- id: ([\w-]+)", (RULES / LANG_FILE[lang]).read_text(), re.M))
        tested = set()
        for f in files:
            kept, _, _ = scan(f, lang)
            got = {(k["rule"], line) for k in kept for line in range(k["line"], k["end_line"] + 1)}
            got_start = {(k["rule"], k["line"]) for k in kept}
            exp = expectations(f)
            for (rule, line), kind in sorted(exp.items(), key=lambda x: x[0][1]):
                tested.add(rule)
                checked += 1
                hit = (rule, line) in got
                ok = hit if kind == "ruleid" else not hit
                per_rule[rule][0 if ok else 1] += 1
                if not ok:
                    failures += 1
                    print(f"FAIL {f.relative_to(RULES)}:{line} expected {kind}: {rule}")
            annotated_lines = {(r, l) for (r, l), k in exp.items() if k == "ruleid"}
            for rule, line in sorted(got_start - annotated_lines):
                if not any((rule, l) in annotated_lines for l in range(line, line + 20)):
                    failures += 1
                    print(f"FAIL {f.relative_to(RULES)}:{line} unexpected match: {rule}")
        for rule in sorted(declared - tested):
            failures += 1
            print(f"FAIL [{lang}] rule without tests: {rule}")
    for rule, (ok, bad) in sorted(per_rule.items()):
        print(f"  {'ok ' if not bad else 'BAD'} {rule}: {ok} passed, {bad} failed")
    print(f"{checked} assertions, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
