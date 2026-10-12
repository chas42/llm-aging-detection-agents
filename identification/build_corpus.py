"""Builds corpus/: read-only snapshots of every application analyzed in the identification stage.

Each app lands in corpus/<id>/ as:
    src/          the application code only (what tools and AI reviewers analyze)
    prompt.txt    the generation prompt, kept OUT of src/ (the paper's review excluded it)
and corpus/manifest.yaml describes all of them (language, framework, scenario, origin, commit).

BaxBench apps are taken from the git HEAD of ../baxbench_LLM_code (never from its working tree,
which contains an uncommitted patch). Secrets, databases, dependencies and build outputs are excluded.

    .venv/bin/python identification/build_corpus.py          # (re)build everything
"""
import fnmatch
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RESEARCH = ROOT.parent
BAX = RESEARCH / "baxbench_LLM_code"
GEN = RESEARCH / "generated-software-llm"
CORPUS = ROOT / "corpus"

# never copied: secrets, data, dependencies, build output, tool metadata
EXCLUDE = [".env", "*.sqlite3", "*.db", "uptime_db", "node_modules", "target", "package-lock.json",
           "Cargo.lock", ".bolt", "__pycache__", "*.pyc", ".git", "migrations"]

SCENARIOS = {"UptimeService": "uptime", "Monitor": "monitor", "CreditCardService": "credit-card",
             "ImageConverter": "image-converter"}


def excluded(rel: Path) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in EXCLUDE)


def git_head_files(repo: Path, subdir: str) -> dict[str, bytes]:
    data = subprocess.run(["git", "-C", str(repo), "archive", "HEAD", subdir], check=True,
                          capture_output=True).stdout
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            if m.isfile():
                out[m.name[len(subdir):].lstrip("/")] = tar.extractfile(m).read()
    return out


def write_tree(dest: Path, files: dict[str, bytes]) -> int:
    n = 0
    for rel, content in files.items():
        if excluded(Path(rel)):
            continue
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        n += 1
    return n


def disk_files(src: Path) -> dict[str, bytes]:
    if src.is_file():
        return {src.name: src.read_bytes()}
    return {str(p.relative_to(src)): p.read_bytes() for p in src.rglob("*")
            if p.is_file() and not excluded(p.relative_to(src))}


def head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def apps() -> list[dict]:
    out = []
    # BaxBench, generated with GPT-5.2 Thinking, Python
    for fw_dir in sorted((BAX / "coding-tasks").iterdir()):
        fw = fw_dir.name.split("-", 1)[1].lower()  # python-fastAPI -> fastapi
        for sc_dir in sorted(fw_dir.iterdir()):
            sub = f"coding-tasks/{fw_dir.name}/{sc_dir.name}"
            out.append({"id": f"bax-{fw}-{SCENARIOS[sc_dir.name]}", "language": "python", "framework": fw,
                        "scenario": SCENARIOS[sc_dir.name], "group": "baxbench", "generator": "GPT-5.2 Thinking",
                        "origin": f"baxbench_LLM_code/{sub}", "commit": head(BAX), "_git": (BAX, sub)})
    # paper (generated-software-llm): Python (Flask, ChatGPT), JS (Express, Bolt), Rust (Actix, Gemini)
    py_prompts = {"uptime": "uptime-prompt.txt", "monitor": "monitor-prompt.txt",
                  "credit-card": "credit-card-prompt.txt", "image-converter": "image-converter-prompt.txt"}
    js_prompts = {"uptime": "prompt_uptime.txt", "monitor": "prompt_monitor.txt",
                  "credit-card": "prompt_credit_card_service.txt", "image-converter": "prompt_convert_image.txt"}
    rs_prompts = {"uptime": "Uptime_Prompt.txt", "monitor": "Monitor_Prompt.txt",
                  "credit-card": "credit_card_Prompt.txt", "image-converter": "Image_Converter_Prompt.txt"}
    for sc in ["credit-card", "image-converter", "monitor", "uptime"]:
        out.append({"id": f"paper-flask-{sc}", "language": "python", "framework": "flask", "scenario": sc,
                    "group": "paper", "generator": "ChatGPT", "origin": f"generated-software-llm/generated-projects/python/{sc}.py",
                    "_disk": GEN / f"generated-projects/python/{sc}.py", "_prompt": GEN / "prompt/python" / py_prompts[sc]})
        out.append({"id": f"paper-express-{sc}", "language": "javascript", "framework": "express", "scenario": sc,
                    "group": "paper", "generator": "Bolt", "origin": f"generated-software-llm/generated-projects/js/{sc}",
                    "_disk": GEN / f"generated-projects/js/{sc}", "_prompt": GEN / "prompt/js" / js_prompts[sc]})
        out.append({"id": f"paper-actix-{sc}", "language": "rust", "framework": "actix", "scenario": sc,
                    "group": "paper", "generator": "Gemini", "origin": f"generated-software-llm/generated-projects/rust/{sc}",
                    "_disk": GEN / f"generated-projects/rust/{sc}", "_prompt": GEN / "prompt/rust" / rs_prompts[sc]})
    # human-written open-source baselines
    for sc, fw, d in [("uptime", "fastapi", "UptimeService_fastAPI/calmmage-service-registry-api-main"),
                      ("monitor", "fastapi", "Monitor_fastAPI/System-Monitor-main"),
                      ("image-converter", "flask", "ImageConverter_flask/gif-creator-python-main")]:
        out.append({"id": f"os-{fw}-{sc}", "language": "python", "framework": fw, "scenario": sc,
                    "group": "open-source", "generator": "human", "origin": f"generated-software-llm/open-source-projects/{d}",
                    "_disk": GEN / "open-source-projects" / d})
    for a in out:
        if "_disk" in a:
            a["commit"] = head(GEN)
    return out


def main() -> int:
    if CORPUS.exists():
        shutil.rmtree(CORPUS)
    manifest = []
    for a in apps():
        dest = CORPUS / a["id"]
        if "_git" in a:
            files = git_head_files(*a["_git"])
            prompt = files.pop("baxbench-prompt.txt", None)
            files = {k[len("code/"):] if k.startswith("code/") else k: v for k, v in files.items()}
        else:
            files = disk_files(a["_disk"])
            prompt = a["_prompt"].read_bytes() if a.get("_prompt") else None
            files.pop("Prompt.txt", None)  # rust apps ship their prompt inside the project
        n = write_tree(dest / "src", files)
        if prompt:
            (dest / "prompt.txt").write_bytes(prompt)
        loc = sum(len(p.read_text(errors="ignore").splitlines()) for p in (dest / "src").rglob("*")
                  if p.suffix in {".py", ".js", ".rs", ".ts", ".tsx"})
        manifest.append({k: v for k, v in a.items() if not k.startswith("_")} | {"files": n, "loc": loc})
        print(f"{a['id']:32s} {n:3d} files {loc:5d} loc")
    (CORPUS / "manifest.yaml").write_text(yaml.safe_dump({"apps": manifest}, sort_keys=False))
    print(f"{len(manifest)} apps -> corpus/manifest.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
