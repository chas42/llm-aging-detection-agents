# Triage: paper-actix-uptime

Inputs: `app/src/main.rs` (175 lines), `app/Cargo.toml`, `inputs/ai_review_r1.md`, `inputs/ai_review_r2.md`,
`inputs/ai_review_r3.md`, `inputs/tool_findings.json` (1 finding), `inputs/_schema.yaml`.
I checked every candidate against `src/main.rs` myself. All verdicts are static hypotheses that still need runtime validation.

Collected 10 raw candidate groups: 6 consolidated findings and their Phase-1 items from each AI repetition, items the adjudicators rejected, and 1 tool finding with `category: aging`. None of the repetitions had SELF-SOURCED items. The tool file has no `resource-leak`, `performance`, `reliability` or security/quality findings. After merging, there are 9 candidates. One is carded.

## 1. All candidates

| Merged id | Sources | Verdict | Reason (from the code) |
|---|---|---|---|
| **C1 → paper-actix-uptime-F1** (write side) | r1:F1 (CR-1, RE-1); r2:F1 (CR-1, RE-1); r3:F1 (CR-1, RE-1); semgrep-aging:aging-rs-shared-map-growth (L97) | **supported** (card F1, priority 1) | L97–98 only insert into a map that lives for the whole process (L150–152). Nothing in the file removes, expires or caps entries. Each new (token, serviceId) pair is retained, and HTTP clients pick both keys freely. |
| **C2 → merged into F1** (read side) | r1:F2 (CR-2, RE-2, CR-6); r2:F2 (CR-2, RE-2); r3:F2 (CR-2, RE-2) | non-aging on its own; merged into F1 as the read side of the same causal chain | The guard taken at L116 is held through the clone (L128–135), sort (L138) and `.json()` (L140). The cost per call is constant for a given map size, and nothing accumulates in the lock itself. It only grows because C1 grows the per-token map. The same workload validates both, so they share card F1. |
| C3 | r1:F3 (CR-4); r2: CR-5, folded into F1 as an amplifier; r3: CR-5, not consolidated | non-aging | L79/L110 validate the trimmed value, but L97/L98/L128 use the raw value. This is a key-identity correctness bug. Whitespace variants only add distinct keys to C1 and do not form a separate mechanism. |
| C4 | r1:F4 (CR-5, RE-3); r2:F3 (CR-6, RE-3); r3:F3 (CR-6, RE-3) | unsupported | The 500-on-poison branches at L89–93 and L118–122 exist, but nothing that runs under the lock can panic in this code: HashMap entry/insert, String clone, `String::cmp` sort, and serde of plain strings. Allocation failure aborts. With no repeatable trigger and nothing accumulating, it is at most a one-shot availability fault. |
| C5 | r1:F5 (CR-3); r2:F4 (CR-3); r3: CR-3, not consolidated | non-aging | `get_iso_time()` at L85 runs before the lock at L87, and L98 overwrites unconditionally, so a stale timestamp can win a race. This is a correctness race with no resource effect. |
| C6 | r2:F4 (CR-4); r3: CR-4, not consolidated; r1: rejected observation (L49) | non-aging | `SystemTime` at L43 is not monotonic. `unwrap_or_default()` at L44 maps pre-epoch times to 1970, and `z >= 0` on a u64 at L49 is a dead branch. All of this is fixed-cost date arithmetic per call, with no accumulation. |
| C7 | r3:F4 (RE-4) only | unsupported | L154–175 configure only `bind` and the JSON error handler, so actix connection and timeout defaults apply. The app code has no connection-retention path, and the claim rests entirely on framework defaults, which r3 itself says largely mitigate it. Nothing accumulates. |
| C8 | r1: rejected observation; r3: UNSAFE note | unsupported | `regex` (Cargo.toml L11) and `tokio` are declared but never referenced in `main.rs`. This affects build size only, with no runtime resource. |
| C9 | r1:RE-1 / r3:RE-1 (2 MiB) vs r1:CR-1 / r2:CR-1 (32 KiB) | not a mechanism (bound detail inside C1) | The disagreement is about the default `JsonConfig` body limit. L156 does not call `.limit()`. The limit only caps per-key size, which sets the C1 growth rate, not whether C1 exists. |

Rejected because the cited code does not exist or does not do what was claimed: none. Every cited line exists. C4 and C7 are unsupported because their trigger or accumulation is absent, not because the lines were mis-cited.

## 2. Source contribution per card

| Card | r1 | r2 | r3 | semgrep-aging | LeakAudit / other tools | Origin |
|---|---|---|---|---|---|---|
| paper-actix-uptime-F1 | F1 + F2 | F1 + F2 | F1 + F2 | aging-rs-shared-map-growth (L97) | — | both (AI + tools) |

The tool flagged only the write-side growth at L97. The read-side lock cost (F2 in every repetition) is AI-only and is merged into the same card.

## 3. Disagreements and resolution

**Between AI repetitions**

1. **JSON body limit (C9).** The CR reviewers in r1 and r2 said about 32 KiB. The RE reviewers in r1 and r3, plus the r3 CR, said about 2 MiB. The r1 adjudicator leaned toward 2 MiB, and the r2 adjudicator left it open. The code only shows `JsonConfig::default()` without `.limit()` at L156. I cannot confirm the resolved actix-web 4.x value from the inputs, so it stays an open question. Either value leaves C1's verdict unchanged, because it bounds one key's size and not the number of keys. It does change the growth rate per request, so the workload should measure it rather than assume it.
2. **Severity of C1.** The CRs rated it high, and the REs and all adjudicators rated it medium or medium-high. I don't need a severity to make a verdict. All three adjudicators call it "supported, workload-dependent rate", and I agree with that.
3. **Where C3 (trim mismatch) belongs.** r1 kept it as a standalone non-performance finding, r2 folded it into F1 as an amplifier, and r3 left it out of consolidation. **Resolution:** I classify it non-aging. It does not change F1's workload, because a validation workload produces distinct keys directly.
4. **C5 and C6 (timestamps).** r1 consolidated C5 as F5. r2 grouped C5 and C6 as F4. r3 left both out of consolidation. All three agree they have no performance impact. **Resolution:** non-aging.
5. **C7 (connection timeouts)** appears only in r3, which calls it unsupported. **Resolution:** I agree, it is unsupported.
6. **C4 (poisoning).** All repetitions agree it is unsupported. r3's CR rated severity medium. r2's RE-3 claims "actix respawns a panicked worker", which is a framework assumption. **Resolution:** unsupported, because I found no panic site under the lock.

**Between AI and tools**

7. The tool flags the shared-map growth (L97) but not the lock-held read path. All AI repetitions flagged both and rated the read path non-aging on its own. **Resolution:** the tool and the AI agree on C1. I merge C2 into F1 as a causal chain rather than giving it a card of its own, so it is measured as a symptom of F1. The tool found nothing that the AI repetitions missed.

**Classification note (mine).** The rubric lists "distinct keys" as an example of a `conditional` element. I classified F1 as `supported` instead. Each new key produces growth directly in the code, and plain HTTP traffic fully controls key distinctness, so no environment or runtime condition is involved. Production key cardinality affects whether the bug matters in a real deployment. It does not affect whether the mechanism can be activated. The read-side symptom is conditional on per-token map size and concurrency, which is noted on the card.
