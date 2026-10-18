# convert-hang: PATH shim that makes ImageMagick `convert` stall

Environment-level trigger for finding `bax-flask-image-converter-F1` (`subprocess.run(["convert", ...])`
with no timeout on the request thread). The app is not modified: the campaign puts this directory first
on the app's PATH, so the app's bare `convert` resolves here (the instrumentation's start line records it
as `convert_resolved`).

```yaml
env: {TMPDIR: '{tmpdir}', PATH: '{root}/env/shims/convert-hang:{path}', CONVERT_HANG_PROB: '0.02'}
```

| variable | default | meaning |
|---|---|---|
| `CONVERT_HANG_PROB` | `0` | probability that a conversion stalls (`0`/unset = never, `>= 1` = always) |
| `CONVERT_HANG_S` | `36000` | stall length in s, after which the real convert runs with the original arguments |
| `CONVERT_HANG_SEED` | `42` | seed of the per-call decision |
| `CONVERT_HANG_STATE` | `${TMPDIR:-/tmp}` | where `convert-hang.counter`, `.lock` and `.log` live |

## Behavior

1. **Find the real convert**: the first executable `convert` on PATH that is not this file (`-ef`), so
   the shim works whatever PATH it is prepended to.
2. **Transparent path** (p = 0, or an info query): run the real convert by name with PATH minus the shim
   directory, so argv[0] stays `convert` as the app passed it. ImageMagick takes its client name from
   argv[0], and exec'ing it by absolute path would change error messages from `convert-im6.q16: ...`
   to `convert: ...`. Arguments, stdin/stdout/stderr, and exit status are untouched. The only other
   difference the real convert sees is the PATH entry removed. Checked: identical GIF bytes, identical
   `-version` output, and identical stderr/exit code (1) for a failing call, compared with the real
   convert. With p = 0 no state files are written.
3. **Only conversions stall, never `convert -version`.** The app runs the `-version` probe at the top
   of every POST, before it reads the upload or creates the temp dir. A stall there holds only the
   thread, the child and the pipes. A conversion stall holds the full resource set of the card: the
   thread, the child, the pipes, the socket and the `gif_creator_*` dir with the saved uploads. That
   gives the clean "+1 of each per stall" relation that the activation criteria ask for. It is also
   the realistic failure: a hang comes from reading or decoding input, a delegate, or the pixel
   cache, and a version banner touches none of these. A `-version` stall would mix two
   footprints into one gauge series.
4. **Reproducible decision**: `k` = the call's ordinal among the conversions of this state dir
   (counter file incremented under `flock`). The call stalls iff `hash32(hash32(seed) + k) < p * 2^32`.
   `hash32` is a xorshift-multiply hash in POSIX shell arithmetic, with no overflow. The set of
   stalled ordinals is identical across runs with the same seed. The mapping from ordinal to request is
   deterministic when conversions do not overlap, which holds for the targeted workload (1 req/s,
   ~0.25 s per conversion). Each decision is logged to `convert-hang.log` as `epoch k stall|pass pid N`.
   With p = 0.02 and seeds 42/43/7, the share of stalls over 20 000 ordinals was 2.0 / 2.1 / 1.9 %.
   Every run gets a fresh private TMPDIR, so the counter starts at 0 each run.
5. **Stall**: the shim re-execs itself under `setpriv --pdeathsig KILL`. The stalled process therefore
   keeps comm `convert` and PPid = the app, so the probe counts it in `proc.children.convert`. It
   holds the app's stdout/stderr pipes open and silent. It then runs `setpriv --pdeathsig KILL --
   sleep N` (one `sleep` grandchild per stall, visible in `proc.descendants`), and after N s execs
   the real convert. The real convert inherits pdeathsig, which has no effect while the app is alive.
   From the app's point of view this is a `convert` that hangs, like a stuck delegate or a pixel
   cache on a hung volume.
6. **Cleanup**: if the app (strictly, the request thread parked in `communicate()`) dies, the stalled
   shim gets SIGKILL, and its `sleep` follows. Checked: no `convert-hang` or `sleep 36000` process
   survives the app. Without `setpriv` (util-linux) the shim falls back to plain re-exec and `sleep`,
   and orphans then live up to N s. Without `flock` the counter is updated without a lock, which is
   still fine for non-overlapping calls.

Why N = 36000 s: with rate r and probability p, held stalls grow as r·p·t while t < N and then plateau
at r·p·N (Little's law). N longer than the 8 h run keeps the growth linear for the whole run.

Requires: POSIX `sh`, `awk`, `date`, `sleep`; `setpriv` and `flock` (util-linux) are recommended.
