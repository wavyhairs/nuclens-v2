# Gemini reasoning implementation progress

Updated: 2026-09-06 (Asia/Seoul)

## Main / PR state

- PR #88 `Gemini reasoning policy and semantic verification infrastructure`: merged.
- PR head: `0fef3429a67d7485e0b659cf55b89c2fab857278`.
- Reviewed base: `3c16d95344c217d2ccd7b612238b4cce10cab084`.
- Merge SHA / post-merge main baseline: `3b50a56bb29de8fdb76f0d6e1a06bf002b0f8caf`.
- Merged at: `2026-09-06T05:09:57Z`.
- Required Python CI passed; there were no reviews, comments, conflicts, or blockers at merge time.
- This follow-up is isolated on `feat/gemini-gold-candidates`; production paths are unchanged.

## Post-merge validation

- Root Python: 1,525 tests passed with `GEMINI_API_KEY` blank.
- Targeted V5/policy/Gold/semantic contracts: 50 tests passed before candidate expansion; the expanded Gold/evaluator suite now contributes 16 passing focused tests.
- Web Python, CI-equivalent (`NUCLENS_SKIP_DATA_GATES=1`): 569 passed, 3 skipped.
- Front-end Node contracts: syntax plus date, weekly selector/sections, event calendar, trend state, admin gate, and admin render all passed.
- Strict live-data web run without the CI skip still has the pre-existing week-balance failure: totals `[50, 48, 112, 160, 131, 149, 143]`, ratio `3.33`; it reproduced on the untouched pre-change worktree (ratio `3.31`).
- Full Build on the merge baseline: 86.5 seconds wall / 86.0 seconds phase, 10,468 archive rows, 4,846 visible rows, 525 issues, 709 evidence attachments, and 0 Gemini calls.
- Deployed news/briefing/issue/trend/entity JSON contained zero instances of the known bad `자동정지 및 사업기간 연장` title after the deterministic presentation repair.
- Policy inventory: 23 reasoning profiles, every thinking setting unspecified; sampling remains explicit/current; Fast semantic blocking is off.

## Recent operational workflows

- Post-merge SHA `3b50a56b`: Python tests succeeded and Deploy web succeeded.
- Latest crawl before merge succeeded. Its downstream Daily Brief and Weekly workflows succeeded at the workflow level but were correctly skipped by their preflight gates.
- Most recent full Daily Brief run (`33992998604`) succeeded. Collection, Telegram send/confirm, Fast → Expert audio generation, subscriber-channel publish, web deploy, audio-cache save, and render smoke steps all succeeded.
- No post-merge crawl/Daily/Weekly full run has occurred yet. No production workflow was manually dispatched for this tooling-only follow-up.

## Gold candidate state

### Identity Gold

- Candidates: 150 pairs.
- Human-labelled: 0.
- `HUMAN_LABEL_REQUIRED`: 150.
- Selection mix: 115 stratified controls, 28 disagreement probes, 7 pinned edge/regression cases.
- Pinned coverage: Paks continuity, Gori LTO continuity, Saeul stop vs project period, same plant/different stage, same meeting/multi-source, same policy/separate announcement, and broader issue vs specific event.
- Cached Gemini review data is stored only under `REFERENCE_ONLY_NOT_GOLD`; model comparison is `NOT_EVALUATED`.

### Curation Gold

- Candidates: 40.
- Human-labelled/user-specified: 1 (`4da5b7ab6c225c78`, `REPAIR`).
- `HUMAN_LABEL_REQUIRED`: 39.
- Every candidate contains source title/link/hash/date, current Nuclens output, and blank human fields for event boundary, scope, stage, date, and causality.
- Domain counts: policy 8, reactor 7, power market 7, SMR 6, waste/fusion 6, supply-chain/general 6.

### Semantic Gold

- Candidates: 77 claims.
- Human-labelled/user-specified: 5 Saeul contract cases.
- `HUMAN_LABEL_REQUIRED`: 72.
- Balanced generated queue: 24 source-aligned, 24 controlled perturbations, 24 unsupported inferences.
- Six domains have 12 generated claims each. All ten semantic error types are covered as review-focus metadata, never as a human answer.
- Saeul contract includes false causality/chronology, separate events, unit 3, unit 4, and project-wide 3·4 scope.

### Candidate audit

- `python tools/validate_reasoning_gold.py`: passed with 0 errors and 0 warnings.
- Identity has 150 unique unordered pairs; no direct or reverse duplicates.
- Required source fields are present, label statuses are internally consistent, domain/risk mixes were inspected, and all ten semantic error types are covered.
- Human-readable sheets: `docs/gold-labeling/identity.md`, `curation.md`, and `semantic.md`.
- Structured queues: `tests/fixtures/gemini_reasoning/identity_candidates.json`, `curation_gold.json`, and `semantic_gold.json`.

## API evaluation

- Live evaluation calls in this follow-up: 0.
- Reason: Identity has no human labels; Curation has one and Semantic has five user-specified labels, which is not a sufficient basis for a production reasoning decision.
- A zero-call planning run confirmed the Semantic queue currently has five runnable human-labelled combinations for one model/config/repeat.
- `tools/llm_eval.py` writes every attempt immediately, resumes by model/config/fixture/repeat key, ignores untrusted `expected_verdict`, defaults to at most 30 new calls, and stops immediately after a quota response.
- Quota state: not hit; no live call was attempted.

## Production reasoning state

- Reasoning activation: unchanged / disabled.
- All task thinking levels: unspecified.
- Fast semantic production blocking: off.
- Production shadow calls: none.
- Sampling, similarity thresholds, schedules, ranking, embeddings, TTS, Telegram, Cloudflare deployment, and web data contracts: unchanged.

## Remaining human action

Start with `docs/gold-labeling/identity.md` and label the first 60 pairs as `MERGE`, `SEPARATE`, or `AMBIGUOUS`, with one reason code. Transfer those answers to the corresponding JSON fields as `human_label`, `reason_code`, and `label_status: HUMAN_LABELLED`. Do not edit cached/model reference fields.

After labels are entered, regenerate/audit without losing labels:

```powershell
python tools/generate_reasoning_gold_candidates.py
python tools/validate_reasoning_gold.py
```

Then plan the exact API work without making a call:

```powershell
$env:GEMINI_API_KEY=''
python tools/llm_eval.py --task IDENTITY_REVIEW --fixtures tests/fixtures/gemini_reasoning/identity_candidates.json --config unspecified --config level:medium --config level:high --repeat 3 --out .eval/identity --max-new-calls 0
```

Run one bounded checkpoint after reviewing the plan:

```powershell
python tools/llm_eval.py --task IDENTITY_REVIEW --fixtures tests/fixtures/gemini_reasoning/identity_candidates.json --config unspecified --config level:medium --config level:high --repeat 3 --out .eval/identity --max-new-calls 30
```

Re-running the same command skips completed keys and resumes from the first pending combination. Production activation remains a separate decision after sufficiently broad human labels and statistically clear results.
