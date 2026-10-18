"""Diagnostic campaign for one or more apps, each described by apps/<app>/campaign.yaml.

    APPS="uptime-fastapi uptime-actix" RUNS="R0 R0b R1" .venv/bin/python deploy/campaign.py
    (or: bash deploy/campaign.sh, which forwards the same environment variables)

campaign.yaml (per app):
    card: findings/<card>.yaml
    runs:
      R0:  {variant: instrumented, workload: workloads/<x>_control.py, args: [], env: {}}
      R0b: {variant: instrumented, workload: workloads/<x>_targeted_v1.py, args: [--key-space, '4']}
      R1:  {variant: instrumented, workload: workloads/<x>_targeted_v1.py, args: [],
            env: {PATH: '{root}/env/shims/<shim>:{path}'}}      # environment-level trigger, if any
      R2:  {variant: repaired, ...same workload/args/env as R1...}

Environment (defaults in brackets): APPS [every app with a campaign.yaml] · RUNS [R0 R0b R1] ·
DURATION [28800 = 8 h] · SEED [42] · COOLDOWN [120] · SERVER + AGING_AGENT_TOKEN (two-machine
mode; unset = one machine) · APP_CPUS / LOAD_CPUS [0 / 1-3 locally, none in two-machine mode].
Each app ends with its own results tarball (deploy/collect_results.sh), so results can be brought
back app by app during a multi-day campaign. Runs a variant that does not exist yet are skipped
(e.g. R2 before the repair).
"""
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")


def log(f, msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    f.write(line + "\n")
    f.flush()


def system_description(camp: Path, cfg: dict) -> None:
    cmds = {"date": "date -Is", "bundle": "cat BUNDLE_VERSION 2>/dev/null || echo dev-tree", "uname": "uname -a",
            "os": "head -4 /etc/os-release", "cpu": "lscpu | grep -E 'Model name|^CPU\\(s\\)|Thread|MHz'",
            "memory": "free -h", "disk": "df -h . ; findmnt -T . -o SOURCE,FSTYPE,OPTIONS",
            "python": f"{PY} -V", "packages": f"{PY} -m pip freeze",
            "tools": "convert -version 2>/dev/null | head -1; $HOME/.cargo/bin/rustc --version 2>/dev/null"}
    with open(camp / "system.txt", "w") as out:
        out.write(f"## config\n{yaml.safe_dump(cfg, sort_keys=False)}\n")
        for k, c in cmds.items():
            out.write(f"## {k}\n" + subprocess.run(c, shell=True, capture_output=True, text=True, cwd=ROOT).stdout)
        if cfg["server"]:
            out.write("## network\n" + subprocess.run(
                f"ping -c 5 -q {shlex.quote(cfg['server'].split('//')[-1].split(':')[0])} | tail -2",
                shell=True, capture_output=True, text=True).stdout)


def main() -> int:
    env = os.environ
    server = env.get("SERVER", "")
    if server and not env.get("AGING_AGENT_TOKEN"):
        print("two-machine mode needs AGING_AGENT_TOKEN", file=sys.stderr)
        return 2
    apps = env.get("APPS", "").split() or sorted(p.parent.name for p in (ROOT / "apps").glob("*/campaign.yaml"))
    cfg = {"apps": apps, "runs": env.get("RUNS", "R0 R0b R1").split(), "duration": int(env.get("DURATION", 28800)),
           "seed": int(env.get("SEED", 42)), "cooldown": int(env.get("COOLDOWN", 120)), "server": server,
           "app_cpus": env.get("APP_CPUS", "" if server else "0"),
           "load_cpus": env.get("LOAD_CPUS", "" if server else "1-3")}
    camp = ROOT / "runs" / f"_campaign_{datetime.now():%Y%m%d-%H%M%S}"
    camp.mkdir(parents=True)
    system_description(camp, cfg)
    total_h = len(apps) * len(cfg["runs"]) * (cfg["duration"] + cfg["cooldown"]) / 3600
    with open(camp / "campaign.log", "a") as lf:
        log(lf, f"campaign {camp.name} ({'remote ' + server if server else 'local'}): apps={apps} "
                f"runs={cfg['runs']} duration={cfg['duration']}s (~{total_h:.1f} h total)")
        first = True
        for app in apps:
            spec_file = ROOT / "apps" / app / "campaign.yaml"
            if not spec_file.exists():
                log(lf, f"SKIP {app}: no apps/{app}/campaign.yaml")
                continue
            spec = yaml.safe_load(spec_file.read_text())
            done = []
            for r in cfg["runs"]:
                run = (spec.get("runs") or {}).get(r)
                if not run:
                    log(lf, f"SKIP {app} {r}: not defined in campaign.yaml")
                    continue
                variant = f"apps/{app}/{run.get('variant', 'instrumented')}"
                if not (ROOT / variant).is_dir():
                    log(lf, f"SKIP {app} {r}: {variant} does not exist yet")
                    continue
                if not first:
                    log(lf, f"cooldown {cfg['cooldown']}s")
                    time.sleep(cfg["cooldown"])
                first = False
                label = f"{app}_{r}"
                cmd = [PY, "harness/run_remote.py" if server else "harness/run_diagnostic.py",
                       "--app", variant, "--instrument", "--workload", run["workload"],
                       "--duration", str(cfg["duration"]), "--seed", str(cfg["seed"]), "--label", label]
                if server:
                    cmd += ["--server", server]
                if cfg["app_cpus"]:
                    cmd += ["--app-cpus", cfg["app_cpus"]]
                if cfg["load_cpus"]:
                    cmd += ["--load-cpus", cfg["load_cpus"]]
                for k, v in (run.get("env") or {}).items():
                    cmd += ["--app-env", f"{k}={v}"]
                cmd += ["--", *[str(a) for a in run.get("args") or []]]
                log(lf, f"START {label}: {run['workload']} {' '.join(map(str, run.get('args') or []))} "
                        f"env={run.get('env') or {}}")
                rc = subprocess.run(cmd, cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT).returncode
                dirs = sorted((ROOT / "runs").glob(f"*_{label}"))
                log(lf, f"END   {label} exit={rc} dir={dirs[-1].relative_to(ROOT) if dirs else None}")
                if dirs:
                    done.append(str(dirs[-1].relative_to(ROOT)))
                    subprocess.run([PY, "harness/analyze_run.py", str(dirs[-1])], cwd=ROOT,
                                   stdout=lf, stderr=subprocess.STDOUT)
            if done:
                (camp / "runs.txt").open("a").write("\n".join(done) + "\n")
                out = subprocess.run(["bash", "deploy/collect_results.sh", str(camp.relative_to(ROOT)), *done],
                                     cwd=ROOT, capture_output=True, text=True).stdout
                log(lf, f"results for {app}: {out.strip().splitlines()[0] if out.strip() else '?'}")
        log(lf, "campaign finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
