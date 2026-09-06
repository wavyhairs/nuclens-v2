# Gemini reasoning implementation progress

Base SHA: `ab0a82ff0f840a8e22c67da5ef27ad6628eb685d`
Branch: `feat/gemini-reasoning`
Last verified commit: `8590f32` (Stage F, after curation guard `4e89985`)

Completed stages:
- Stage 0: latest `origin/main` re-audited; the 14 production generative call groups, embedding, TTS, and non-production exclusions still match the plan.
- Stage A: wrapper modernization, bounded telemetry, model wiring, and startup model diagnostics implemented without changing existing request bodies.
- Stage B: central no-op task policy wired to production callers; resumable offline evaluator and split Gold fixtures/candidate generation added.
- Stage C: generation policy fingerprints and bounded soft-stale refresh implemented for issue review, KEEI matching, and issue insights; old values survive deferred/failed refreshes.
- Stage F infrastructure: shared strict semantic verdict/taxonomy, source-evidence separation, Expert verifier promotion with zero added calls, and the complete Fast verify/repair/re-audit/re-verify chain implemented. Fast production blocking remains disabled pending broader human Semantic Gold.
- Curation regression: real Saeul mixed-event headline is deterministically separated, and source-unsupported causal wording in optional `implication`/`why_important` is removed without an extra LLM call.
- Archive presentation boundary: the same Saeul title repair is applied after clustering/signature calculation, so historical JSONL and story identity remain unchanged while deployed titles are corrected.

Current stage: final audit and PR preparation.
Next action: commit the archive presentation repair, compare with latest `origin/main`, then push/open the PR if still clean.

Tests last passed:
- python tests: 1,518 passed (`GEMINI_API_KEY="" python -m unittest discover -s tests`).
- Full Build: 82.7s, 10,447 archive rows, 525 issues, 705 evidence attachments, 0 Gemini calls; deployed JSON has zero occurrences of the bad mixed headline.
- web tests: 569 run, 568 passed, 2 skipped, 1 failed. The sole failure is the pre-existing live-data week-balance gate (`[50,48,112,160,131,149,143]`, ratio 3.33); it reproduces in the untouched original worktree (ratio 3.31).
- node/contracts: unavailable; the repository has no `package.json`. Workflow/static contracts are covered by Python tests.
- V5 characterization: 27 targeted V5/policy/eval/Gold tests pass; `expected.characterization_sha256` and frozen request fixture are unchanged.

Gold Sets:
- Identity: 144 stratified candidates generated; every answer is `HUMAN_LABEL_REQUIRED` and cached Gemini verdicts are context-only.
- Curation: real archive regression `4da5b7ab6c225c78` pinned from the user-specified failure contract; broader human Gold is absent.
- Semantic: five user-specified Saeul chronology/scope contract cases added; broader balanced human Gold is absent.
- Synthesis: not started; production activation remains out of scope without labels.

Live API evaluation:
- models/configs tested: none in this implementation branch; the plan's 2026-09-06 24-call probe remains the only live evidence.
- completed samples: 0.
- remaining samples: all Identity candidates and broader Curation/Semantic human-labelled Gold combinations.
- blocked by quota?: no API run attempted; human labels are absent.
- last successful evaluation output: none.

Production policy changes currently enabled:
- No task reasoning level changed.
- Existing model-selection variables are now wired consistently in workflows.
- Active caches keep existing decisions while policy-stale entries refresh within an independent 20-item budget; failures retain old decisions.
- Expert's existing verifier now uses the shared 10-type taxonomy, strict compact verdict, an independent model resolver, and source-bound evidence distinct from generated dossiers; its call count is unchanged.
- Audio manifests carry semantic gate/model/thinking/verdict-digest metadata; narrative gate v2 makes unsent legacy audio stale while preserving `stale_sent` no-resend behavior.
- Saeul mixed-event titles and optional unsupported causal analysis are deterministically neutralized.

Explicitly NOT enabled:
- Identity/Curation/Context/Narrative/Semantic thinking promotion.
- Fast semantic production blocking (the full chain is present behind a false constant).
- Sampling/temperature/top-p/top-k changes.
- Production shadow calls or cache-wide invalidation.

Known failures / blockers:
- Human-labelled Identity, Curation, and Semantic Gold Sets are not present, so reasoning promotion and blocking semantic verification cannot be activated.
- The live-data week-balance web test is already red on the untouched worktree and is outside this reasoning-policy change; all other web tests pass.

Exact resume command / next step:
- `cd C:\AI\nuclens-v2\.worktrees\gemini-reasoning`
- `git status --short && git diff origin/main...HEAD --check`
- Fetch/compare latest `origin/main`, review the complete diff, and prepare the PR; keep Fast blocking and all reasoning promotion disabled pending broader human Gold.
