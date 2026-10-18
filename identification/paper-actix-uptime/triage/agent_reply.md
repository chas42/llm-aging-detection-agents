- **Cards:**
  - `paper-actix-uptime-F1` — supported — priority 1 — `POST /heartbeat` keeps adding to the process-wide `AppState.db` map (`src/main.rs:97-98`) and never removes anything, so every distinct (token, serviceId) pair stays in memory for the life of the process. The card also covers the read-side cost of the same chain: `POST /services` copies, sorts and serializes the growing per-token map while holding the single global `Mutex` (`src/main.rs:116-140`), so its lock hold time grows with the map and delays heartbeats for every token.
- **Non-aging / unsupported:** 7 in total.
  - Non-aging (4): C2, the lock held during the read path, which is non-aging on its own and merged into F1 as the read side of the same chain; C3, validation trims keys but storage doesn't; C5, the timestamp is taken before the lock, so an older value can overwrite a newer one; C6, the wall clock isn't monotonic and falls back to 1970.
  - Unsupported (3): C4, a poisoned mutex causing permanent 500s, but nothing under the lock can panic; C7, no connection timeouts configured, found only in r3; C8, unused `regex` and `tokio` dependencies.
  - C9, the disagreement over the JSON body limit, is not a mechanism. It is documented as a detail of F1.
- **Source contribution:** AI only 0 / tools only 0 / both 1. F1 was found by all three AI repetitions and by `semgrep-aging:aging-rs-shared-map-growth`. The read-side part merged into F1 came from the AI reviews only. The tool output had nothing else in scope.
- **Open questions:**
  1. **JSON body limit:** the actix-web default limit for the resolved 4.x version is unknown; reviewers said either 32 KiB or 2 MiB. Line 156 sets no `.limit()`, and the inputs don't settle it. It sets how much memory each request can add, not whether the store grows.
  2. **`supported` vs `conditional`:** your rubric lists "distinct keys" as an example of a conditional element, but I classified F1 as `supported`. In the code, every new key adds an entry, and plain HTTP traffic fully controls whether keys are new. Please confirm this reading matches what your pipeline expects.
  3. **`/services` slowdown:** how much `/services` latency and heartbeat tail latency will rise depends on how many services sit under one token, how many workers run (CPU count by default) and how concurrent the traffic is. Static evidence can't say whether it's measurable at realistic map sizes.
  4. **`app` path:** I followed the schema template and set `app: corpus/paper-actix-uptime/src`. The source for this run is actually in `app/`, so adjust that field if your pipeline resolves paths differently.

Files are in `out/`:
- `paper-actix-uptime-F1.yaml`
- `triage.md`