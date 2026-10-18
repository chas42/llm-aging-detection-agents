# 1. Performance-engineer final consolidated report (verbatim)

**Static-analysis hypotheses only.** These findings come from reading `/tmp/aging-review-_glrlplg/app/app.py` and need runtime confirmation. Nothing was executed.

## Consolidated findings

### 1. Unbounded conversion workload and concurrency (merges CR-1, FL-1, FL-4, FL-6)
- **Static validity:** Qualified. The code lacks a pixel-count budget, any `-limit` option, any concurrency cap, and any frames-per-input check. The specific numbers are estimates, not code facts.
  - **Cap checks:** The 200-file cap is at line 74 and the 10000 per-dimension cap at line 37. `MAX_CONTENT_LENGTH` (line 14) limits compressed bytes only.
  - **Frame list:** `build_frame_list` (lines 93-94) lets the list reach about 2N-2 entries (up to 398), which was verified.
  - **Single process:** Line 118 passes all inputs to one `convert` process, and `-coalesce` runs before `-resize` (lines 119-120).
  - **Threading:** `app.run` (line 261) uses the Werkzeug threaded default, so there is no concurrency limit.
  - **Overstated claims:**
    - The "tens to hundreds of GB" figure depends on the Q8/Q16 build and on `policy.xml`, which can cap resources.
    - The "multi-frame inputs add an unbounded number of frames" claim depends on the format and the policy.
    - FL-6's doubled spooling is only plausible. Werkzeug's multipart spooling threshold and `convert`'s handling of duplicate paths are unverified.
- **Standalone or merged:** Merged: CR-1, FL-1, FL-4, FL-6.
- **Performance relevance:** Direct.
- **Affected resource:** `convert` process RAM, the disk pixel cache (TMPDIR), CPU, and request threads.
- **Triggering conditions:** Large `targetSize`, many files, `appendReverted=true`, animated or multi-page inputs, or highly compressed large rasters. Concurrent requests make it worse.
- **Existing cleanup:** `TemporaryDirectory` removes the files after `convert` returns. Memory and the pixel cache are freed when the child exits.
- **Bounds:** Per-request only. The 50 MB upload cap, the 200-file cap, and the dimension cap are all independent of each other.
- **Direct evidence vs. assumption:**
  - *Direct:* the missing limits and the multiplicative caps.
  - *Assumption:* the host's memory cgroup, the `policy.xml` limits, the ImageMagick version and quantum depth, and the WSGI deployment.
- **Mechanism class:** Finite-resource exhaustion, with transient per-request overhead. Resources are released at the end of each request, so nothing is retained across requests.
- **Aging relevance:** Non-aging performance fault. It needs no long-run accumulation: one oversized or concurrent burst triggers it, and the resources return afterward. Nothing persists between requests.
- **Final severity / confidence:** High / medium. Existence of the missing bounds is high confidence. The practical impact depends on the environment.

### 2. No timeout on `convert`, with request threads and temp directories held (merges CR-2, FL-1 timeout portion, FL-5 timeout portion)
- **Static validity:** Confirmed. Neither `subprocess.run` call (lines 66 and 126) has a `timeout`.
- **Standalone or merged:** Merged: CR-2, FL-1 (timeout part), FL-5 (timeout part).
- **Performance relevance:** Conditional.
- **Affected resource:** Request threads, `convert` child processes, and the temp directory and its files.
- **Triggering conditions:** `convert` stalls or runs very long. Causes include heavy input (finding 1), delegate hangs, and a slow disk cache. A stuck request is also never stopped by a client disconnect.
- **Existing cleanup:** The `with` block (line 210) cleans up only when `convert` returns. `subprocess.run` kills the child only if an exception interrupts the wait.
- **Bounds:** None in the code. Any bound would have to come from an external proxy or server timeout, or a `policy.xml` time limit. Those were not examined.
- **Direct evidence vs. assumption:**
  - *Direct:* the missing timeouts and the held `with` block.
  - *Assumption:* that `convert` actually stalls in practice, and what the deployment's timeouts are.
- **Mechanism class:** Finite-resource exhaustion. Stuck threads, processes, and temp files accumulate only while stalls keep occurring.
- **Aging relevance:** Conditionally plausible aging mechanism. The accumulation and the missing cleanup are supported by the code. The repeatable trigger (a recurring hang) and the long-run degradation are not shown in the code, and they depend on the workload.
- **Final severity / confidence:** Medium / medium.

### 3. `convert -version` spawned on every request (merges CR-5, FL-5)
- **Static validity:** Confirmed. Line 200 calls it before any validation, and the result is not cached.
- **Standalone or merged:** Merged: CR-5, FL-5.
- **Performance relevance:** Direct, but small.
- **Affected resource:** CPU, fork/exec cost, and latency.
- **Triggering conditions:** Every POST, including invalid ones.
- **Existing cleanup:** `subprocess.run` reaps the child. Nothing is retained.
- **Bounds:** None on the rate. The overhead is constant per request.
- **Direct evidence vs. assumption:** *Direct:* the code. *Assumption:* the actual start-up cost of `convert` on the host.
- **Mechanism class:** Transient or repeated overhead.
- **Aging relevance:** Non-aging performance fault.
- **Final severity / confidence:** Low / high.

### 4. `send_file` returned inside the temp-directory `with` block (merges CR-3, FL-3)
- **Static validity:** Qualified. Lines 241-247 return from inside the `with`, so cleanup runs before the body is streamed. Under typical Flask/Werkzeug on Linux, `send_file` opens the file eagerly, so the response still works (this is outside the cited code). The failure modes depend on the platform and configuration. They are `USE_X_SENDFILE`, Windows, and a wrapper that defers the open. The reviewers' Windows 500 claim, and FL-3's "leaked directories" claim, are speculative.
- **Standalone or merged:** Merged: CR-3, FL-3.
- **Performance relevance:** Conditional, and weak.
- **Affected resource:** The unlinked-but-open output file's disk space until the response closes. Slow clients can hold it.
- **Triggering conditions:** Slow downloads on Linux. The functional failures need the other platform or configuration conditions.
- **Existing cleanup:** The `with` block removes the directory. The open file descriptor is released when the response closes.
- **Bounds:** One GIF per in-flight response. The held space is bounded by the number of in-flight responses.
- **Direct evidence vs. assumption:** *Direct:* the ordering of return and cleanup. *Assumption:* Werkzeug's eager open and the deployment platform.
- **Mechanism class:** Transient overhead. The finding is mainly a correctness concern, with a minor transient disk effect.
- **Aging relevance:** Non-aging performance fault. It leaves no persistent accumulation on the Linux path.
- **Final severity / confidence:** Low / low.

### 5. Blanket `except Exception` converts 413 and 400 errors into 500 (FL-2)
- **Static validity:** Qualified. `request.files` and `request.form` are accessed inside the `try` (lines 202-207), and `except Exception` (line 254) would catch a Werkzeug `HTTPException` raised during lazy parsing. Whether the exception is raised there depends on the Werkzeug version. With `MAX_CONTENT_LENGTH`, the 413 may be raised at that first access.
- **Standalone or merged:** Standalone.
- **Performance relevance:** None. This is an error-semantics issue. The only possible runtime effect is retry amplification by clients, which is speculative.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity / confidence:** Low / medium (for the purpose of this review).

### 6. Delay rounding and the 1 cs default (CR-6)
- **Static validity:** Confirmed. Line 107 maps 1 to 14 ms to 1 cs, and Python's `round` is banker's rounding. Viewer clamping behavior is outside the code.
- **Standalone or merged:** Standalone.
- **Performance relevance:** None. It affects playback timing, which is a correctness issue.
- **Mechanism class:** No performance impact.
- **Aging relevance:** Unrelated to performance.
- **Final severity / confidence:** Low / high.

## Rejected or out of scope
- **CR-4 (wrong output with mixed sizes or multi-frame inputs):** Rejected as a performance finding. It is a correctness concern. Its frame-count expansion is already counted in finding 1. Its claims about `-coalesce` behavior depend on the ImageMagick version and were not verified.

## SELF-SOURCED — UNVALIDATED
None. The cited code has no cross-request state, caches, or global collections. Resources are per-request and released at request end.

---

# 2. Appendix: raw Phase-1 handoffs (unedited)

## Handoff A: code-reviewer (CR-*)

**CR-1**
- **ID / file / lines:** CR-1, /tmp/aging-review-_glrlplg/app/app.py, lines 14, 37-38, 74-75, 84-94, 113-126 (concurrency context: 261)
- **Fault and evidence:** No limit bounds the total pixel work that `convert` is asked to do. Each cap is checked on its own, and the caps multiply.
  - Line 74 allows up to 200 files: `if len(files) > 200`.
  - Line 37 allows target sizes up to 10000x10000: `if w > 10000 or h > 10000`.
  - Lines 93-94, `paths + list(reversed(mid))`, nearly double the frame list (up to 398 frames).
  - Lines 118-121 run `*input_paths, "-coalesce", "-resize", resize_arg, "-layers", "Optimize"` in a single ImageMagick process, which holds the whole sequence in memory.
  - Line 14's `MAX_CONTENT_LENGTH` (50 MB) limits compressed upload bytes, not decoded pixels. Small, highly compressed PNG/GIF/TIFF files can decode to very large rasters. Multi-frame inputs (animated GIF/TIFF, allowed at line 17) add an unbounded number of frames per file.
- **Execution path:** POST /create-gif → `create_gif` (198) → `safe_input_files` (203) → `parse_target_size` (205) → save loop (213-233) → `build_frame_list` (235) → `run_convert_make_gif` (238) → `subprocess.run` (126).
- **Affected state or resource:** Host RAM and swap, ImageMagick's disk-backed pixel cache (usually in TMPDIR), CPU, and the Flask worker thread.
- **Triggering conditions:**
  - Many frames combined with a large `targetSize` and/or `appendReverted=true`. For example, 398 frames × 10000×10000 is about 4×10^10 pixels, which is tens to hundreds of GB of pixel cache at Q16.
  - Small decompression-bomb inputs.
  - Concurrent requests. `app.run` (261) uses Werkzeug's threaded server by default, and nothing limits how many conversions run at once.
- **Existing bounding logic and why it is insufficient:** The three caps (upload bytes, frame count, per-dimension size) are applied separately. Nothing caps width × height × frames, decoded input size, or frames inside each input. No ImageMagick `-limit` options are passed and there is no per-process rlimit. The only cleanup is `TemporaryDirectory`, which runs after `convert` finishes.
- **Plausible runtime consequence:** Memory exhaustion that triggers the OOM killer (which may pick the Flask process or other host processes), heavy swapping, disk filling from the pixel cache, and requests that take minutes or hours. Concurrent requests make all of these worse.
- **Severity / confidence:** high / high
- **Assumptions to validate at runtime:** The ImageMagick build and its policy.xml resource limits (a distro default policy may cap memory, disk, width, or height). Quantum depth (Q8 or Q16). Whether a production WSGI server sets worker or thread limits.

**CR-2**
- **ID / file / lines:** CR-2, /tmp/aging-review-_glrlplg/app/app.py, lines 126 and 66 (held resources: 210-247)
- **Fault and evidence:** Neither subprocess call has a timeout.
  - Line 126: `proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)`
  - Line 66: `subprocess.run(["convert", "-version"], check=True, ...)`
  - The request thread blocks synchronously until `convert` exits. While it waits, the `with tempfile.TemporaryDirectory(...)` block (210) stays open, so the saved uploads and any partial output stay on disk.
- **Execution path:** `create_gif` → `ensure_imagemagick_available` (200 → 66) and `run_convert_make_gif` (238 → 126).
- **Affected state or resource:** Worker threads, child processes, and the per-request temporary directory with its files.
- **Triggering conditions:** `convert` runs for a long time or hangs. This can happen with heavy inputs (CR-1), malformed or pathological images, delegate programs (for example for TIFF or WebP) that stall, or a slow pixel cache backed by disk.
- **Existing lifecycle logic and why it is insufficient:**
  - `subprocess.run` does kill the child, but only when an exception interrupts the wait. No timeout ever raises one.
  - A client disconnect does not interrupt the thread.
  - `TemporaryDirectory` cleanup runs only after `convert` returns.
  - With no cap on concurrency, stuck requests pile up.
- **Plausible runtime consequence:** Threads and `convert` processes accumulate over time, along with their temp directories. This leads to thread or process exhaustion, growing disk usage in the temp directory, and the service becoming unresponsive.
- **Severity / confidence:** med / med
- **Assumptions to validate at runtime:** How often `convert` stalls in practice. Whether a front proxy or WSGI server enforces request timeouts and kills workers. Whether policy.xml sets a `time` limit.

**CR-3**
- **ID / file / lines:** CR-3, /tmp/aging-review-_glrlplg/app/app.py, lines 210, 237-247
- **Fault and evidence:** The response streams `output.gif` from inside the temporary directory, but the directory is deleted before the response body is sent.
  - `send_file(out_path, ...)` (241-247) is returned from inside `with tempfile.TemporaryDirectory(...) as tmpdir:` (210).
  - The `return` exits the `with` block, which runs `rmtree(tmpdir)`, before Flask/Werkzeug iterates the response body.
- **Execution path:** `create_gif` → `send_file` (241) → `with` exit (cleanup) → the WSGI server iterates the response.
- **Affected state or resource:** The `output.gif` file and its open descriptor, and the temporary directory.
- **Triggering conditions:**
  - Every successful request goes through this path.
  - It actually fails when Werkzeug does not hold an open file handle at cleanup time. Examples: `USE_X_SENDFILE` is enabled (the proxy would read a path that no longer exists), or the app runs on a non-POSIX filesystem or OS where an open file cannot be removed.
  - On Windows, `rmtree` would raise inside the `with` exit. That exception is caught by `except Exception` (254) and turned into a 500, even though the GIF was generated.
- **Existing lifecycle logic and why it is insufficient:** It works on Linux only by accident. Werkzeug 3.1 opens the path inside `send_file`, and POSIX allows unlinking a file that is still open, so the bytes are still served. The code itself does not ensure the file outlives the response.
- **Plausible runtime consequence:** Under X-Sendfile, empty or 404 responses. On Windows, 500 errors on every successful request. On Linux, deleted-but-open files keep their disk space until the response is closed, so slow clients pin that space.
- **Severity / confidence:** med / med
- **Assumptions to validate at runtime:** The deployment OS and filesystem, the Flask `USE_X_SENDFILE` setting, and the Werkzeug version's `send_file` behavior (whether it opens the file eagerly).

**CR-4**
- **ID / file / lines:** CR-4, /tmp/aging-review-_glrlplg/app/app.py, lines 84-94, 113-123
- **Fault and evidence:** The output is wrong when input images have different sizes or when an input has more than one frame.
  - Lines 115 and 119 apply `"-dispose", "previous"` and `"-coalesce"` to separate image files.
  - `-coalesce` composes the sequence onto a canvas defined by the first image's page geometry. Later frames that are larger get cropped, and smaller ones are composited over a disposed or transparent background.
  - `-resize` (120) runs after coalescing, so it scales the already cropped or padded canvas rather than each source image.
  - `build_frame_list` (84-94) reverses file paths, not frames. An animated GIF or multi-page TIFF input is inserted again with its internal frames still in forward order, and it expands into many frames, so the "boomerang" is not a true reverse.
- **Execution path:** `create_gif` → `build_frame_list` (235) → `run_convert_make_gif` (238) → `convert` command (113-123).
- **Affected state or resource:** The content of the generated GIF.
- **Triggering conditions:** Uploaded images with different dimensions or page offsets, or any animated or multi-page input, especially with `appendReverted=true`.
- **Existing logic and why it is insufficient:** Every frame is resized to the target size, but only after coalescing, so the cropping and padding has already happened. Nothing normalizes input sizes or `+repage`s before `-coalesce`, and nothing checks or flattens the number of frames per input.
- **Plausible runtime consequence:** GIFs with cropped or misplaced frames and transparent or garbage borders, wrong frame order in boomerang mode, and more frames than expected (which also feeds CR-1).
- **Severity / confidence:** med / med
- **Assumptions to validate at runtime:** The coalesce semantics of the installed ImageMagick version (IM6 vs IM7) and the page geometry metadata in typical uploads.

**CR-5**
- **ID / file / lines:** CR-5, /tmp/aging-review-_glrlplg/app/app.py, lines 64-68, 200
- **Fault and evidence:** Every request checks for ImageMagick by spawning a process. `create_gif` calls `ensure_imagemagick_available()` (200), which runs `subprocess.run(["convert", "-version"], check=True, ...)` (66). This happens before any input validation, so even requests that are rejected with a 400 pay for a fork/exec and ImageMagick start-up.
- **Execution path:** POST /create-gif → line 200 → line 66.
- **Affected state or resource:** CPU, process table entries, and request latency.
- **Triggering conditions:** Any POST to /create-gif, including invalid or high-rate requests.
- **Existing bounding logic and why it is insufficient:** The result is not cached and there is no rate limiting. The check repeats work whose answer does not change within a process lifetime. Combined with CR-2, a hung `convert -version` blocks the request indefinitely.
- **Plausible runtime consequence:** Added latency and CPU cost on every request, and more process churn under load. A flood of cheap invalid requests still produces one child process each.
- **Severity / confidence:** low / high
- **Assumptions to validate at runtime:** The actual start-up cost of `convert` on the host (delegate and config loading), and the request rate.

**CR-6**
- **ID / file / lines:** CR-6, /tmp/aging-review-_glrlplg/app/app.py, lines 18, 42-53, 107
- **Fault and evidence:** The delay the user asks for is not the delay the GIF plays at, for small values.
  - Line 107 converts milliseconds to centiseconds: `delay_cs = max(1, int(round(delay_ms / 10.0)))`.
  - The API default is `DEFAULT_DELAY_MS = 10` (18), and validation accepts values down to 1 ms (49). All of these become `delay_cs = 1`. Values from 1 to 14 ms all map to 1 cs, so the precision is lost.
  - Python's banker's rounding makes the result inconsistent at halves: `round(2.5) = 2`, so 25 ms becomes 20 ms, while `round(3.5) = 4`, so 35 ms becomes 40 ms.
  - Most GIF decoders (major browsers) treat delays of 1 cs or less as about 10 cs.
- **Execution path:** `create_gif` → `parse_delay_ms` (206), which returns 10 when `delay` is omitted → `run_convert_make_gif` (107) → `-delay 1` (116).
- **Affected state or resource:** Frame timing in the generated GIF.
- **Triggering conditions:** API calls that omit `delay` (the default of 10 ms applies), or that pass any delay below about 15 ms or a value that ends in exactly 5 ms.
- **Existing bounding logic and why it is insufficient:** Validation only checks the 1-60000 range. It does not account for GIF's 10 ms granularity or for how decoders clamp very small delays.
- **Plausible runtime consequence:** API clients that rely on the default get GIFs that play about 10 times slower than requested (around 100 ms per frame instead of 10 ms), and some delays are rounded inconsistently.
- **Severity / confidence:** low / high
- **Assumptions to validate at runtime:** How the target viewers clamp delays of 1 cs or less (browser and decoder behavior).

## Handoff B: python-pro (FL-*)

**FL-1**
- File: /tmp/aging-review-_glrlplg/app/app.py, lines 126-129 (also 66, 197-247, 261)
- Fault: `subprocess.run(cmd, stdout=PIPE, stderr=PIPE, text=True)` has no `timeout`, and `convert` has no `-limit` arguments. The handler is a plain synchronous view. `app.run(host="0.0.0.0", port=5000, debug=False)` (line 261) uses Werkzeug's default `threaded=True`. That means one thread per request, with no cap on concurrent conversions.
- Execution path: POST `/create-gif` → `create_gif` → `run_convert_make_gif` → blocking `subprocess.run`. Each request spawns its own `convert` process, and the request thread waits until it exits.
- Affected state or resource: CPU, RAM, threads, and process-table entries. The temp-directory disk space is also held for the whole run.
- Triggering conditions:
  - Up to 200 frames per request (line 74) at a target of up to 10000x10000 (line 37). One resized frame at that size is about 400 MB in ImageMagick's in-memory form.
  - Several concurrent requests.
  - A pathological input that makes `convert` run for a long time.
- Existing bounding logic: `MAX_CONTENT_LENGTH` of 50 MB (line 14), the 200-file cap, and the 10000 dimension cap. None of these bound pixel count, decode cost, run time, or the number of concurrent requests. A small, highly compressed PNG expands to a large raster.
- Plausible runtime consequence: memory exhaustion or OOM kill, CPU saturation, and request threads hung indefinitely. The service stalls or crashes.
- Severity: high. Confidence: medium.
- Assumptions needing validation: the deployment uses the Werkzeug dev server or a similar unbounded-thread model, and the container has no memory cgroup or ImageMagick `policy.xml` limits.

**FL-2**
- File: /tmp/aging-review-_glrlplg/app/app.py, lines 202, 205-207, 254-255
- Fault: `request.files.getlist` (line 202) and `request.form.get` (lines 205-207) are inside `try`. `request.files` and `request.form` parse the body lazily, and parsing can raise Werkzeug `HTTPException` subclasses. These include `RequestEntityTooLarge` (413) when `MAX_CONTENT_LENGTH` is exceeded or there are too many multipart parts, and `BadRequest` (400) for a malformed multipart body. The blanket `except Exception` (line 254) catches them and returns a generic 500 JSON error.
- Execution path: an oversized or malformed upload → the first access to `request.files` raises inside `try` → `except Exception` → `json_error("Internal Server Error", 500)`.
- Affected state or resource: the HTTP response status and semantics (request lifecycle and error handling).
- Triggering conditions: a body over 50 MB, more than the multipart part limit, or a bad multipart boundary.
- Existing logic: the comment at line 13 says Flask/Werkzeug will reject larger payloads with 413. The `except Exception` clause prevents that.
- Plausible runtime consequence: clients and proxies get 500 for client-side errors. The real cause is hidden, and retry logic or alerting may misfire. Because no logging happens in the handler, the failure is also silently swallowed.
- Severity: med. Confidence: high.
- Assumptions needing validation: the Werkzeug version raises on lazy parsing inside the view rather than earlier in the request lifecycle.

**FL-3**
- File: /tmp/aging-review-_glrlplg/app/app.py, lines 210, 237-247
- Fault: `send_file(out_path, ...)` is returned from inside `with tempfile.TemporaryDirectory(...)`. The directory is deleted when the `with` block exits, which happens before the WSGI server streams the body. Correctness depends on `send_file` having already opened the file handle.
- Execution path: `return send_file(...)` → `__exit__` removes the tmpdir → the server iterates the response's open file object afterwards.
- Affected state or resource: the output GIF file handle and the temp directory.
- Triggering conditions:
  - On Linux the unlinked file stays readable through the open descriptor, so this normally works.
  - It breaks on platforms where open files cannot be deleted (Windows). There, cleanup raises an error, or the file or directory is left behind.
  - Anything that defers the open would also break it, such as a wrapper or middleware that uses the path. A `HEAD` or conditional request could also change what happens.
- Existing logic: the `with` block cleans up. It is sufficient only if the file is opened eagerly and the OS permits unlinking an open file.
- Plausible runtime consequence: on affected platforms, a 500 error, or leaked `gif_creator_*` directories that fill disk. On Linux the effect is likely none.
- Severity: low. Confidence: low.
- Assumptions needing validation: the Werkzeug version opens the file eagerly in `send_file`, and the production OS is Linux.

**FL-4**
- File: /tmp/aging-review-_glrlplg/app/app.py, lines 113-123 and 213-233
- Fault: every uploaded file is passed to `convert` as a positional input. Multi-frame formats (animated GIF, multi-page TIFF, WebP) expand into many frames. `-coalesce` (line 119) then composes every frame into a full canvas before `-resize`, so memory scales with the total number of frames times the original canvas size, not with the 200-file cap.
- Execution path: 200 uploads, each with many frames → `convert` loads and coalesces all of them at full resolution → resizes → `-layers Optimize` runs over all frames.
- Affected state or resource: the `convert` process's memory and temp disk (ImageMagick spills to its disk cache), plus CPU.
- Triggering conditions: animated or multi-page inputs, or inputs with large canvases, under the 50 MB total upload.
- Existing bounding logic: the 200-file cap and the 50 MB upload limit constrain compressed bytes only. The `-resize` runs after `-coalesce`, so it does not reduce the intermediate size.
- Plausible runtime consequence: very large memory use or disk-cache spill for modest uploads, causing slow requests, OOM, or filling `/tmp`.
- Severity: med. Confidence: medium.
- Assumptions needing validation: ImageMagick's default resource limits, and how `-coalesce` ordering behaves on the installed version.

**FL-5**
- File: /tmp/aging-review-_glrlplg/app/app.py, lines 64-68 and 200
- Fault: `ensure_imagemagick_available()` runs `subprocess.run(["convert", "-version"], ...)` synchronously on every request. This happens before any validation and before the body is read. Each request, including invalid ones, forks and execs an extra process. The call also has no timeout.
- Execution path: every POST `/create-gif` → fork/exec of `convert -version`, then the real work.
- Affected state or resource: process-spawn capacity and latency. Under load, extra forks of a large Python process add overhead.
- Triggering conditions: any request, and especially floods of cheap invalid requests such as bad `targetSize` or empty `images`.
- Existing logic: none. There is no caching of the availability result, and it is not done at startup.
- Plausible runtime consequence: added per-request latency, and cheap requests cause process churn that amplifies load. If `convert` is missing, every request pays the exec attempt before failing.
- Severity: low. Confidence: high.
- Assumptions needing validation: cost of the spawn under the deployment's memory footprint and concurrency.

**FL-6**
- File: /tmp/aging-review-_glrlplg/app/app.py, lines 202-228 and 235-238
- Fault: the request's uploaded files and the copies saved to the tmpdir coexist. `request.files` parts above Werkzeug's in-memory threshold are spooled to temp files and live until request close. `f.save(path)` copies each of them again into `tmpdir`. The boomerang list (lines 93-94) passes the same paths twice, so `convert` decodes them twice.
- Execution path: multipart parse → `f.save` per file → `convert` over `frame_paths`, which can reach 2×N−2 entries.
- Affected state or resource: temp disk, request memory, and `convert` memory and CPU.
- Triggering conditions: `appendReverted=true` with up to 200 files, giving roughly 398 input frames.
- Existing bounding logic: the 200-file cap applies to uploaded files only, not to the number of frames after `build_frame_list`.
- Plausible runtime consequence: the effective frame count is up to about 2x the intended cap, with doubled storage during parsing. This roughly doubles the memory and CPU cost described in FL-1 and FL-4.
- Severity: low. Confidence: medium.
- Assumptions needing validation: Werkzeug's spooling threshold, and whether `convert` re-decodes duplicate paths or caches them.