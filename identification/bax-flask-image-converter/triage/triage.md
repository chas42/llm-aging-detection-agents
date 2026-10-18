# Triage — bax-flask-image-converter

Inputs: `app/app.py` (261 lines, single module, Flask 3.1.0 / Werkzeug 3.1.3), `inputs/ai_review_r1..r3.md`,
`inputs/tool_findings.json` (10 findings), `inputs/_schema.yaml`. All verdicts below are static hypotheses for
runtime validation, not confirmations of aging.

The module has no module-level mutable state: no cache, list, dict, pool or global counter. Every
resource is scoped to a request (`TemporaryDirectory`, `subprocess.run`). So all aging candidates depend on
a request or child process that **never finishes** (F1), or on cleanup being **bypassed by an abnormal kill** (F2).

## 1. All candidates

| Merged id | Sources | Verdict | One-line reason |
|---|---|---|---|
| **C1 → F1** | r1:Finding 2 (CR-2, FL-1, FL-5 timeout parts); r2:Finding 1 (hang part), r2:Finding 2 (amplifier); r3:Finding 1 (hang part), r3:Finding 2 (amplifier); r1:CR-5 / r2:CR-4 / r3:CR-5 (probe hang); semgrep-aging:aging-py-subprocess-without-timeout @66 and @126 | **conditional** | `subprocess.run` at lines 66 and 126 has no timeout. Each stalled child pins a thread, the child, its fds and the `gif_creator_*` tmpdir, with no cap (line 261). The accumulation needs an external stall. |
| **C2 → F2** | r1:Finding 1, r2:Finding 1, r3:Finding 1 (pixel-cache disk as a resource); orphaning on SIGKILL added by triage | **conditional** | The child's `magick-*` temp files in the shared TMPDIR, and in-flight `gif_creator_*` directories when the server is killed, are never swept. Persistence needs an OOM or external SIGKILL. |
| C3 | r1:Finding 1 (CR-1, FL-1, FL-4, FL-6); r2:Finding 1 (non-hang part, CR-1, FL-2); r3:Finding 1 (non-hang part, CR-1, CR-4, FL-1); r1:CR-4 (frame-expansion part) | non-aging | Real per-request exhaustion: there is no pixel or frame budget, `-coalesce` runs before `-resize` (119-120), boomerang gives about 2N−2 frames (93-94), and multi-frame exts are allowed (17). All of it is released when `convert` exits, and nothing persists across requests. |
| C4 | r1:Finding 1 (concurrency part); r2:Finding 2 (CR-2); r3:Finding 2 (CR-2, FL-2) | non-aging | No admission control is confirmed: there is no semaphore or queue in the module, and `app.run` is at line 261. This is a load-proportional capacity limit present from the first burst. It only feeds aging through F1, where it is recorded as the missing bound. |
| C5 | r1:Finding 4 (CR-3, FL-3); r2:Finding 3 (CR-3, FL-4); r3:Finding 3 (CR-3, FL-4) | non-aging | `send_file` is returned inside the `with` block (210, 241-247), so on Linux the unlinked GIF and its fd are held until the response closes. The hold is bounded by live connections and released on close. Windows and X-Sendfile sub-claims: unsupported for a Linux deployment (`USE_X_SENDFILE` is never set). |
| C6 | r1:Finding 3 (CR-5, FL-5); r2:Finding 4 (CR-4, FL-3); r3:Finding 5 (CR-5, FL-2/FL-5 probe part) | non-aging | Uncached `convert -version` fork on every POST (200 → 66), reaped each time. Constant per-call cost. Its missing timeout is merged into F1. |
| C7 | r3:Finding 4 (FL-3); r1:FL-6 (merged into r1:Finding 1) | non-aging | Upload is spooled by Werkzeug and copied again by `f.save` (228). That is about 2× per request, bounded by 50 MB (line 14) and removed by TemporaryDirectory. |
| C8 | r2:Finding 5 (CR-5) | non-aging | `text=True` + PIPE (126) buffers the full output, and strict decode can raise `UnicodeDecodeError`, which becomes a 400 (249). Stdout is normally empty because the output goes to a file. Transient, and a correctness issue. |
| C9 | r1:Finding 5 (FL-2); r3 rejected FL-5 | non-aging | `except Exception` (254) catches Werkzeug `RequestEntityTooLarge`/`BadRequest` raised by lazy parsing at 202-207 and returns 500 without logging. Error semantics and diagnosability only. |
| C10 | r1:Finding 6 (CR-6); r2:Finding 6 (CR-6, FL-5); r3 rejected CR-6 | non-aging | Delay quantization at line 107 (1-14 ms → 1 cs, banker's rounding). Playback correctness, with no performance or resource effect. |
| C11 | codeql:githubsecuritylab/audit/command-line-injection @66, @126 | unsupported | Argv lists without a shell. Every argv element is a constant, a validated int, or a tmpdir path built from an index and a whitelisted extension (226-227), so no untrusted string reaches it. The process resource it touches is covered by F1. |

Tool findings glanced at and not taken as candidates (no resource named, no aging relevance):

| Tool finding | Note |
|---|---|
| codeql:py/untrusted-data-to-external-api @30 | Anchored `TARGET_RE.match`. The pattern has no nested quantifiers, so matching is linear. Not resource-related. |
| codeql:py/stack-trace-exposure @24 (×2, experimental + quality) | `str(e)` of RuntimeError carries up to 2000 chars of ImageMagick stderr (129) to the client. Information exposure only. |
| semgrep:avoid_app_run_with_bad_host @261 (×3, default/flask/security-audit) | Bind address only. Line 261 matters to F1 only because it selects the threaded dev server. |

## 2. Source contribution per card

| Card | r1 | r2 | r3 | semgrep-aging | LeakAudit | Other tools | Class |
|---|---|---|---|---|---|---|---|
| F1 (no timeout → stuck threads/children/tmpdirs) | Finding 2 (conditional aging) | Finding 1 + 2 (non-aging, conditional hang path noted) | Finding 1 + 2 (non-aging, "nearest candidate") | aging-py-subprocess-without-timeout @66, @126 | — | CodeQL command-line-injection @66/@126 (same lines, rejected as unrelated) | both |
| F2 (temp residue after SIGKILL) | Finding 1 (pixel-cache resource only) | Finding 1 (resource only) | Finding 1 (resource only) | — | — | — | AI only (the mechanism was framed by triage) |

LeakAudit (`resource-leak`) reported nothing for this app. Every `category: aging` finding (2/2) maps to F1.

## 3. Disagreements and resolutions

**Between AI repetitions**

- **Missing timeout (F1):** r1 rates it *conditionally plausible aging*. r2 and r3 fold it into a *non-aging* finding about unbounded conversion cost, but their overall notes call the hang path the closest conditional candidate. **Resolution:** I split it out. The transient cost (C3) is non-aging. The never-returning child is a separate mechanism with all four elements plausible and the trigger dependent on the environment, so it is **conditional** (F1). The probe call at line 66 has the same gap on the same handler and thread, so it is merged in. It does not hold a tmpdir because it runs before line 210.
- **send_file inside TemporaryDirectory (C5):** r2 says "conditionally plausible at best, likely non-aging". r1 and r3 say non-aging. CR-3 and FL-3 in r1 and r2 claim leaked directories and a 500 on Windows. **Resolution:** non-aging. On Linux, Werkzeug 3.1 opens the file inside `send_file`, so the space is held only for the life of the connection. A stalled reader on the dev server can hold it indefinitely, but that is a per-connection hold that ends when the client closes, not accumulation. The Windows and X-Sendfile paths are not active in this code.
- **Upload double buffering (C7):** r3 reports it standalone. r1 merges it into Finding 1. r2 does not report it. **Resolution:** non-aging either way.
- **Error-handling findings (C8, C9):** each appears in only one consolidated report (C8 in r2, C9 in r1), and r3 rejected its FL-5. **Resolution:** non-aging (correctness and diagnosability).
- **Delay rounding (C10):** r1 and r2 keep it as "no performance impact". r3 rejects it. **Resolution:** non-aging (correctness).
- **No SELF-SOURCED items** in any repetition. Each states that the module has no cross-request state, and I confirmed that in the code.

**Between AI and tools**

- **semgrep-aging @66/@126 (C4 finite-resource exhaustion)** versus the mostly non-aging AI verdicts: the tool is right about the missing bound, but it cannot show the trigger. **Resolution:** conditional (F1), controllability `environment`, priority 2, not 1.
- **CodeQL command-line-injection:** no AI review flags injection. **Resolution:** unsupported (C11).
- **Concurrency (C4):** the AI reviews treat it as its own finding. No tool flags it. **Resolution:** non-aging on its own, and recorded in F1 as the missing bound that lets stuck requests pile up.
- **F2:** no tool and no review states the orphaning mechanism. It is a triage extension of the pixel-cache resource that all three reviews name. It is given low priority (4) because its accumulation happens in ImageMagick, not in the app code, and it needs an external kill.

## Notes

- `app:` in the cards follows the schema's `corpus/<app-id>/src` convention. In this invocation that location is `app/`.
- `requirements.txt` lists unused packages (passlib, fpdf, pyjwt). None of them are imported in `app.py`.
