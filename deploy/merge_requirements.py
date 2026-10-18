"""Merge the harness requirements with every app's requirements into one installable file.

Apps generated from the same BaxBench template pin slightly different versions of packages they do
not even import (e.g. pyjwt 2.10.0 vs 2.10.1), which makes a single `pip install -r ... -r ...`
impossible. For a package pinned to different versions, the highest version is kept and the
conflict is reported. Usage (stdlib only, runs with the system python3):
    python3 deploy/merge_requirements.py > merged-requirements.txt
"""
import glob
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def vkey(v: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"[.\-+]", v)]


def main() -> int:
    files = [ROOT / "requirements.txt"] + sorted(Path(p) for p in glob.glob(str(ROOT / "apps/*/original/requirements.txt")))
    pinned, unpinned, seen_by = {}, [], {}
    for f in files:
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = PIN.match(line)
            if not m:
                unpinned.append(line)
                continue
            name, extra, ver = m.group(1).lower(), m.group(2) or "", m.group(3)
            seen_by.setdefault(name, set()).add((ver, f.relative_to(ROOT).parts[1] if "apps" in f.parts else "harness"))
            if name not in pinned or vkey(ver) > vkey(pinned[name][1]):
                pinned[name] = (extra, ver)
    for name, versions in sorted(seen_by.items()):
        if len({v for v, _ in versions}) > 1:
            print(f"# conflict resolved: {name} {sorted(versions)} -> {pinned[name][1]}", file=sys.stderr)
    out = [f"{n}{e}=={v}" for n, (e, v) in sorted(pinned.items())]
    out += sorted({u for u in unpinned if PIN.match(u) is None and u.split("[")[0].split("=")[0].lower() not in pinned})
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
