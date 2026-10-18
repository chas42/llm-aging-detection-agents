- **Cards:**
  - `bax-flask-image-converter-F1` — conditional — priority 2 — neither `subprocess.run` call (`app.py:66`, `app.py:126`) has a timeout, and nothing caps how many requests run at once (`app.py:261`). Each time `convert` stalls, the request thread, the `convert` child, its pipe fds and the `gif_creator_*` temp directory with the uploads stay held. These pile up for as long as stalls keep happening.
  - `bax-flask-image-converter-F2` — conditional — priority 4 — ImageMagick's `magick-*` pixel-cache files sit in the shared TMPDIR, and the app never sets a separate temp directory or sweeps it. If `convert` is SIGKILLed (OOM killer or an external kill), those files are left behind. If the server itself is killed, its in-flight `gif_creator_*` directories are left too. Repeated heavy requests that end in kills fill `/tmp`.
- **Non-aging / unsupported:** 9 in total, plus 6 tool findings I checked and set aside because they name no resource.
  - Non-aging (8): C3 (unbounded cost per conversion), C4 (no limit on concurrent requests), C5 (`send_file` returned inside `TemporaryDirectory`), C6 (`convert -version` started on every request), C7 (uploads stored twice), C8 (stdout/stderr buffered in full and decoded strictly), C9 (`except Exception` turns 413/400 into 500), C10 (delay rounding).
  - Unsupported (1): C11 (CodeQL command-line-injection at lines 66 and 126).
  - Set aside: CodeQL regex-match, stack-trace-exposure ×2, and the semgrep `0.0.0.0` finding ×3.
- **Source contribution:**
  - Both AI and tools: 1 (F1, from all 3 reviews plus both semgrep-aging findings).
  - AI only: 1 (F2). All three reviews name the pixel-cache disk, but none describes files being left behind. That framing is mine.
  - Tools only: 0. LeakAudit reported nothing.
- **Open questions:**
  1. Can `convert` really hang on this host? The code can't show it. It depends on the ImageMagick build and delegates, and on whether `policy.xml` sets a `time` or memory limit. A strict policy would stop both mechanisms. I couldn't see which `policy.xml` is in use.
  2. Deployment: is the app run with `python app.py` (threaded Werkzeug dev server, no limit on threads) or under a WSGI server with a fixed pool and request timeouts? With a fixed pool, F1 shows up as the pool filling up rather than threads growing.
  3. F2 depends on ImageMagick behaviour outside the app: that it writes its pixel cache to `/tmp`, and that its own signal-handler cleanup is skipped on SIGKILL. It also depends on whether the memory cgroup kills `convert` or the Flask process.
  4. Target OS. The Windows and X-Sendfile failures claimed for C5 apply only off Linux, or if `USE_X_SENDFILE` were turned on (the app never sets it). One review asserts Linux, but none of the inputs prove it.
  5. The schema's `app:` field uses the `corpus/<app-id>/src` convention. The source here is actually in `app/`, so please check that the path is right for your pipeline.

Files are in `out/`:
- `bax-flask-image-converter-F1.yaml`
- `bax-flask-image-converter-F2.yaml`
- `triage.md`