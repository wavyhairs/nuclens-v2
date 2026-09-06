# Gemini reasoning validation progress

Updated: 2026-09-06 (Asia/Seoul)

## Repository and Gold state

- Gold PR #91 passed CI, was mergeable, and was merged at
  `2ee5c4ad5720c0e0e8297f951be715ca9396d471`.
- Final PR #91 audit found only the canonical Identity fixture, candidate audit, and
  Gold fixture test. Exactly cases 1–60 changed in human-owned fields: 13 MERGE,
  47 SEPARATE, 0 AMBIGUOUS. Cases 61–150 remain null and
  `HUMAN_LABEL_REQUIRED`; no model/cache value was promoted to Gold.
- Identity fixture SHA-256:
  `8c9d9a3b59c32ad89589ee3d0b94c135099494527259676841410890b7566479`.
- Current work branch: `feat/gemini-reasoning-validation`, based on merged main.
- Production policy is unchanged: all 23 profiles request unspecified thinking and
  Fast semantic blocking remains disabled.

## Identity 30-call checkpoint

Model: `gemini-3.1-flash-lite`; 10 balanced cases (5 MERGE / 5 SEPARATE),
`unspecified`, `medium`, and `high`, repeat 1.

| Config | Accuracy | false_merge | false_split | Prediction mix | p50 / p95 latency | Thought tokens |
| --- | ---: | ---: | ---: | --- | --- | ---: |
| unspecified | 9/10 | 0 | 1 | 4 MERGE / 6 SEPARATE | 2.95s / 3.73s | 0 |
| medium | 7/10 | 1 | 2 | 4 MERGE / 6 SEPARATE | 3.65s / 6.31s | 3,029 |
| high | 7/10 | 1 | 2 | 4 MERGE / 6 SEPARATE | 4.62s / 9.06s | 4,859 |

All 30 calls succeeded. There were no API, retry, JSON/schema, truncation, mapping,
or loop failures. No retry was observed in the live run log, although the original
rows did not persist retry count. Cross-config agreement was 6/10; repeat consistency is not
meaningful with one repeat. The uniform 4/6 output is a mild separation propensity,
not a structural checkpoint failure. The checkpoint therefore authorized the full
run; it did not select a production config.

The same balanced checkpoint was also run against the actual `issue_review` model,
`gemini-3.5-flash-lite`, without repeating any successful row:

| Config | Accuracy / balanced | MERGE recall | SEPARATE recall | false_merge | false_split | p50 / p95 latency | Thought / total tokens |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| unspecified | 80% / 80% | 60% | 100% | 0 | 1 | 0.97s / 1.22s | 0 / 1,255 |
| medium | 70% / 70% | 60% | 80% | 0 | 2 | 3.40s / 4.68s | 4,718 / 5,963 |
| high | 80% / 80% | 60% | 100% | 0 | 2 | 3.69s / 8.38s | 8,331 / 9,511 |

All 30 calls succeeded with zero retries, API/schema/JSON failures, or truncations.
This authorized the resumable 3.5 full run; it did not select a production config.

## Identity full live evaluation checkpoint

- Planned: 60 human-labelled cases × 3 configs × 3 repeats = 540 combinations.
- The 30 successful checkpoint rows were copied into the full checkpoint with a
  provenance file; no successful combination was called twice.
- Current durable result: 499 successes, one daily-quota failure, 41 pending.
- Completed by config: unspecified 180/180; medium 180/180; high 139/180.
- Quota response was classified as daily RPD and stopped immediately. No retry loop
  or unexpected call expansion occurred.
- Result path (ignored, local): `.eval/identity-full/results.jsonl`.
- Summary path (ignored, local): `.eval/identity-full/summary.json`.

Partial metrics are diagnostic only because high is missing the tail of the ordered
case set:

| Config | n | Accuracy | Balanced accuracy | MERGE recall | SEPARATE recall | false_merge | false_split | Consistency | p50 / p95 / max | Thought tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| unspecified | 180 | 81.67% | 68.82% | 46.15% | 91.49% | 9 | 21 | 100% (60/60 groups) | 2.68s / 22.13s / 44.57s | 0 |
| medium | 180 | 78.33% | 61.13% | 30.77% | 91.49% | 12 | 27 | 100% (60/60 groups) | 4.58s / 10.33s / 28.62s | 49,797 |
| high | 139 | 83.45% | 66.94% | 39.29% | 94.59% | 6 | 17 | 100% (46 complete groups) | 4.83s / 9.43s / 16.47s | 70,336 |

Across the 499 successes: accuracy 80.96%, balanced accuracy 65.52%, MERGE recall
38.68%, SEPARATE recall 92.37%, false_merge 27, false_split 65, and prediction mix
68 MERGE / 428 SEPARATE / 3 AMBIGUOUS. There were zero truncations, JSON failures,
schema failures, or non-quota API failures. Older rows did not persist full token
breakdown/retry detail, so retry count for those rows is formally unavailable.

The completed baseline currently exceeds medium on accuracy, balanced accuracy,
MERGE recall, false_merge, false_split, latency, and thought-token cost. This is strong
evidence against medium, but the production decision remains pending until all 41
high rows exist. No reasoning level was changed.

### Exact resume point

First pending key:
`gemini-3.1-flash-lite|level:high|8b486e4051da2e66--eeb5f60b2c8223fd|2`.

After the daily quota resets, load the existing API key in the process and run:

```powershell
python -X utf8 tools/llm_eval.py `
  --task IDENTITY_REVIEW `
  --fixtures tests/fixtures/gemini_reasoning/identity_candidates.json `
  --config unspecified --config level:medium --config level:high `
  --repeat 3 --out .eval/identity-full --max-new-calls 41
```

The evaluator skips all 499 successful keys. The prior quota row is not complete and
is retried once as part of the 41-call bound.

## Identity 3.5 full live evaluation checkpoint

The same 60 Human Gold cases were evaluated against the production `issue_review`
model, `gemini-3.5-flash-lite`. The 30 successful balanced-checkpoint rows were
seeded with provenance and were not called twice.

- Current durable result: 489/540 successes, with 51 combinations pending.
- Completed by config: unspecified 179/180, medium 180/180, high 130/180.
- One unspecified response parsed as JSON but was not an object; it is a single
  schema failure and remains pending. There were no repeated schema failures.
- One daily-quota response stopped the run immediately after the client's single
  bounded per-minute retry. There was no unexpected call expansion.
- Result path (ignored, local): `.eval/identity-3.5/results.jsonl`.
- Summary path (ignored, local): `.eval/identity-3.5/summary.json`.

High is missing an ordered tail and is therefore diagnostic only:

| Config | n | Accuracy | Balanced accuracy | MERGE recall | SEPARATE recall | false_merge | false_split | Consistency | p50 / p95 / max | Thought / total tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| unspecified | 179 | 83.24% | 68.01% | 41.03% | 95.00% | 7 | 20 | 91.67% | 1.01s / 38.43s / 51.06s | 0 / 22,240 |
| medium | 180 | 82.78% | 69.53% | 46.15% | 92.91% | 8 | 20 | 90.00% | 4.14s / 16.09s / 47.54s | 76,225 / 97,817 |
| high | 130 | 82.31% | 65.36% | 39.13% | 91.59% | 8 | 14 | 93.02% (43 groups) | 4.37s / 11.93s / 16.60s | 88,745 / 104,332 |

Across the 489 successes: accuracy 82.82%, balanced accuracy 67.94%, MERGE recall
42.57%, SEPARATE recall 93.30%, false_merge 23, false_split 54, and prediction mix
66 MERGE / 416 SEPARATE / 7 AMBIGUOUS. Telemetry recorded one bounded retry, zero
truncations, zero JSON failures, and zero non-quota API failures. Medium's small
balanced-accuracy/MERGE-recall gain over the nearly complete baseline comes with
lower overall/SEPARATE accuracy, one more false merge, lower repeat consistency,
and materially higher latency/tokens. This is not clear activation evidence; no
production policy changed.

First pending key:
`gemini-3.5-flash-lite|unspecified|962102da67dd64bb--f396174affa85472|1`.

After the daily quota resets, load the existing API key in the process and run:

```powershell
python -X utf8 tools/llm_eval.py `
  --task IDENTITY_REVIEW `
  --fixtures tests/fixtures/gemini_reasoning/identity_candidates.json `
  --model gemini-3.5-flash-lite `
  --config unspecified --config level:medium --config level:high `
  --repeat 3 --out .eval/identity-3.5 --max-new-calls 51
```

The evaluator skips all 489 successful keys. The schema and quota rows are pending,
not accepted results, and each can be retried only within the 51-call bound.

## Evaluation harness changes

- Parsed JSON now passes an explicit task/verdict/error-type schema before a row is
  marked successful.
- Summary output now records per-config accuracy, balanced accuracy, class recalls,
  false merge/split, prediction propensity, repeat consistency, p50/p95/max latency,
  thought and available token totals, and retry count.
- JSON, schema, API, quota, and truncation failures are distinct. Requested thinking
  and observed call detail are persisted for new rows.
- Successful-result resume semantics and immediate append/flush are unchanged.

## Other task Gold and labeling readiness

| Task | Candidates | Human labels | Pending | Production state |
| --- | ---: | ---: | ---: | --- |
| Identity | 150 | 60 | 90 intentionally unused | Evaluation 499/540; activation pending |
| Curation | 40 | 1 | 39 | `HUMAN_LABEL_REQUIRED` |
| Semantic | 77 | 5 | 72 | `HUMAN_LABEL_REQUIRED`; Fast gate stays off |
| Synthesis / Narrative / Extract | — | 0 adequate task contracts | — | `HUMAN_LABEL_REQUIRED` |

Candidate generation and audit were already complete: Curation covers event boundary,
scope, stage, date, causality and the Saeul regression; Semantic covers 24 aligned,
24 controlled-perturbation, 24 unsupported-inference cases plus five Saeul contracts.
The audit passes with zero errors and zero warnings.

Complete Sol input packages and strict output schemas were generated for the 39
Curation and 72 Semantic pending cases under `docs/gold-labeling/sol-review/`.
Exact-model GPT-5.6 Sol High orchestration agents independently judged both packages;
no substitute model, Gemini cache, or prior Gemini verdict was used. Curation rows
include available verified entities, stages, claims, quantities, dates, source
title/link, and generated output; Semantic rows include the generated statement,
source evidence, entities, topics, dates, and chronology claims.

| Provisional task | Completed | Label distribution | Confidence | Hard cases | Initial Human review |
| --- | ---: | --- | --- | ---: | ---: |
| Curation | 39/39 | 25 CORRECT / 9 INCORRECT / 5 AMBIGUOUS | 30 high / 4 medium / 5 low | 10 | 24 |
| Semantic | 72/72 | 24 SUPPORTED / 48 UNSUPPORTED / 0 AMBIGUOUS | 72 high | 39 | 41 |

Semantic provisional error coverage is: causality 28, overclaim 30, chronology 8,
unsupported relation 7, contradiction 6, attribution 5, other 1, and none 24.
Both imports passed strict validation with zero missing/duplicate rows. All 111 rows
remain `human_reviewed=false`, and both canonical fixtures have an empty Git diff.

Sol JSONL is imported only as `AI_ASSISTED_PROVISIONAL_NOT_GOLD` with
`human_reviewed=false`. The importer validates schema, case/source mapping, duplicate
IDs, candidate hashes, confidence/reason/evidence fields, and assigns hard-first
review priority. It never writes canonical fixtures:

```powershell
python tools/sol_provisional_gold.py --task curation --import-provisional path\to\curation-sol-output.jsonl
python tools/sol_provisional_gold.py --task semantic --import-provisional path\to\semantic-sol-output.jsonl
```

The loopback UI then shows source/evidence, generated output, Sol label/reason/
confidence/error type, and supports A=Approve, C=Change, U=Needs review, arrows,
Enter, and one-step undo. It imports no Gemini client, binds only to `127.0.0.1`,
hides unrelated selection metadata, saves atomically to ignored Human sidecars, and
exports only rows explicitly marked `human_reviewed=true`:

```powershell
python tools/review_gold_labeler.py --task curation
python tools/review_gold_labeler.py --task semantic
```

After provisional import, review ordering places low-confidence, ambiguous,
causality/chronology/event-boundary/wrong-link cases first. The initial representative
target is computed from the actual provisional distribution (base 24 Curation / 36
Semantic, enlarged when hard-case or label/error/confidence coverage requires it).
High-confidence rows still require an explicit human approval. If the initial Gold
does not separate configs clearly, the remaining queue is reviewed incrementally.

## Profile decisions

- Identity 3.1 profiles (`dedup`, `dedup_final`, `keei_match`): decision pending the
  final 41 high calls and targeted regressions.
- Identity 3.5 profile (`issue_review`): 489/540 model-appropriate live results are
  durable; baseline retained while 51 combinations remain pending.
- Curation and Semantic: `HUMAN_LABEL_REQUIRED`; no inference from Identity Gold.
- Synthesis, narrative, planning, and extraction profiles: `HUMAN_LABEL_REQUIRED`;
  no classification-style accuracy claim and no assumption that high writes better.
- Activated profiles: none.

The complete per-profile inventory, call sites, safety nets, caches, criticality, and
Gold status is in `docs/2026-09-06-gemini-task-inventory.md`.

## Verification / PR checkpoint

- Root Python with Gemini/Telegram disabled: 1,558 tests passed.
- Web Python with CI data gates skipped: 569 passed, 3 skipped.
- Node syntax and date, weekly selector/sections, event calendar, trend state, admin
  gate/render contracts passed; real-browser admin DOM smoke passed.
- Gold validator: 0 errors, 0 warnings. Latest focused evaluator/labeler suite: 24 passed.
- Local Full Build: 83.7 seconds, 10,489 archive rows, 4,859 visible rows,
  525 issue details, 709 evidence attachments, 0 Gemini calls. It produced the known
  local degraded identity diagnostic (two quarantined clusters) because this isolated
  worktree has no embeddings file; it did not modify production data.
- GitHub PR/CI state is pending commit and push.

No Telegram send, deployment, production data mutation, or other external side effect
is part of this branch.
