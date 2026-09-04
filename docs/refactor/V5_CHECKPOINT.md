# Nuclens V5.1 Refactor Checkpoint

- schema_version: `V5.1`
- updated_at_utc: `2026-09-04T20:34:54Z`
- current_phase: `PHASE 3`
- current_sub_step: `PR #82 CI 33916811781 passed at 04cd09a; common Merge Gate starting`
- initial_baseline_main_sha: `bbe62a4c06c85d28962a54ee85d01db9109079dc`
- latest_main_sha: `a83f3bba4bf71558c4b5e89642b87a270eb87d31`
- architecture_state_map_baseline_sha: `bbe62a4c06c85d28962a54ee85d01db9109079dc`
- work_branch: `refactor/v5-phase3-build-data-extraction`
- merge_mode: `autonomous-merge-permitted`
- harness_version: `4bdfd75 (test: add V5 refactor safety harness)`
- characterization_baseline: `SHA-256 b9753b325a00505f4b496b6e907ad35c5cf472a5b36d0326c01e4fbe916a5786; 13 tests; 3 stable runs 2.063/2.018/2.020s; PYTHONHASHSEED 1/17/101 identical`
- state_schema_changed: `no`
- persistent_state_changed: `no`
- pending_operating_verification: `none`
- stop_status: `no`
- stop_reason: `none`

## GitHub authority and policy

- authenticated_account: `wavyhairs`
- repository_permissions: `admin, maintain, push, triage, pull`
- main_branch_protection: `not enabled (GitHub API 404: Branch not protected)`
- required_reviews: `none enforced`
- required_checks: `none enforced by branch protection`
- linear_history: `not enforced`
- admin_bypass: `not applicable; bypass will not be used`
- repository_auto_merge_setting: `disabled`
- ordinary_pr_merge_by_authenticated_owner: `permitted`

## PR history

PHASE 1 PR #80 merged and verified at `7432fc5`. PHASE 2 PR #81 (`fix: atomically replace crawler snapshots`) merged by ordinary PR merge at `a1dfd5d` on 2026-09-04T13:47:54Z after CI `33879823426`, passed the Merge Gate, and passed production verification in Crawl run `33890549173`. PHASE 3 PR #82 (`refactor: extract publication title helper`) is open at `04cd09a` and awaiting CI. There are no unverified V5.1 merges.

## Validation status

- last_passed_tests: `PHASE 3 commit 04cd09a: candidate tests 5 + V5 harness 13 OK; candidate mutation detected 2 failures; moved AST identical; call-time patch OK; root 1,470 OK in 126.974s; web offline 561 OK/3 skip in 21.780s; offline Node date/weekly/trend/event/admin/render/DOM contracts OK; PR CI 33916811781 success in 58s at exact head`
- last_failed_tests: `advisory-only web suite without NUCLENS_SKIP_DATA_GATES: 1/561 failed`
- failure_cause: `live weekly sample totals [50,48,112,159,129,140], max/min 3.3125 > advisory threshold 2; explicitly excluded from deploy gate by existing contract`
- workflows_to_verify: `per-PR impact path: Python tests; Deploy web; Nuclear news crawl; Daily Brief; Weekly report; Deploy crawl watchdog`
- verification_conditions: `checkout contains merge SHA or its state-descendant; workflow success; expected state-only diff; no schema/order/identity/ranking/grouping/request-count drift; no degraded/failure increase`

## ABANDON history

None.

## Next action

Check all PR #82 checks at head `04cd09a`. If CI succeeds, refresh `origin/main`, classify any intervening state commits, update the branch without taking stale state, rerun relevant tests, inspect the exact two-file diff and active production workflows, then apply the common Merge Gate. Merge only on PASS; afterward verify the first relevant Deploy web run before any later code merge.

## PHASE 1 local gate (complete; PR pending)

- Production algorithm/state/config/prompts/models/thresholds were not changed. The only workflow change records resolved Python/Node dependency versions without pinning them.
- Added a sanitized synthetic frozen fixture with baseline SHA, creation date, real regression test references, malformed/legacy/Unicode/NFC-NFD/state-boundary/admin-override cases, fixed embeddings, and no secret/private/full-body data.
- Exact characterization digest protects selected order, complete ranking diagnostics and numeric values, story/issue grouping and IDs, identity metadata, continuity, Daily region selection path, Weekly story/calendar selection, state transition, JSONL bytes, and configured thresholds.
- Gemini fixture lookup is keyed by canonical request hash containing model and exact structured body; only JSON object key order is canonicalized. Unknown hashes fail. Counter multiset and total call count are asserted. Cache hit/stale/envelope bytes are asserted.
- IPv4/IPv6 connections are blocked while local IPC remains available. Hash-seed subprocesses and temporary-checkout workflow import-mode subprocesses use the same block with blank secrets and `PYTHONDONTWRITEBYTECODE=1`; production source hashes are unchanged.
- Comparator allowlist contains only the explicit `generated_at` path and rejects all other drift.
- Bootstrap negative controls all detected: ranking weight, dedup threshold, reversed sort, removed story-id seed, changed Gemini prompt, and omitted snapshot write. Runtime monkeypatches only; no mutation remains.
- Validation: root 1,461 tests OK (128.542s); deploy-mode web 561 OK/3 skip (22.490s); Node app/date/weekly/event/trend/admin gate/render/DOM all OK; harness 13 OK and three consecutive runs stable at 2.063/2.018/2.020s.
- Initial harness implementation corrections: subprocess socket-class replacement was narrowed to `connect/connect_ex/create_connection`; Windows subprocess decoding was fixed to UTF-8; cache expected bytes gained the existing final newline. Each correction was followed by rerun; no production change resulted.
- GitHub CI run `33878508360` passed at head `4bdfd75` in 1m21s. Resolved versions included Python 3.12.14, pip 26.2.1, requests 2.34.2, google-genai 2.22.0, feedparser 6.0.14, Node 22.23.2, npm 10.9.8. The local/CI google-genai version differs (2.18.1 vs 2.22.0), but exact characterization and all CI tests passed; no contract drift was observed.
- Merge Gate: freshly fetched `origin/main` remained `bbe62a4`; PR was clean/mergeable with no review requirement; only the eight declared harness/docs/workflow files differed; no generated state, production fixture, prompt/model/threshold/config change, debug code, or temporary mutation was present; no Crawl/Daily/Weekly workflow was active. Gate result: PASS.

## PHASE 1 production verification (complete)

- Workflow/run: Python tests main push, `33878813214`; checkout/merge SHA `7432fc5f238762e8c4c1191ccd47448703816c97`; started 2026-09-04T13:34:23Z, completed 2026-09-04T13:35:39Z; conclusion `success`; duration 1m13s.
- This harness/workflow-only merge triggers no production state writer, deployment, LLM/API call, cache, article selection, or delivery path. State commit SHA/parent SHA: not applicable; output/state diff: none expected and none produced by the workflow.
- Root tests and offline front-end contracts passed. Existing test-generated warning/error annotations were unchanged contract fixtures, not workflow failures. No schema/order/identity/ranking/grouping/request-count/config/automation drift was observed.
- PHASE 1 result: COMPLETE; removed from the unverified-merge set.

## PHASE 2 local gate (complete; PR pending)

- Qualification: PHASE 0 found critical tracked snapshots (`sent.json`, `curated.json`, `digest_queue.json`) owned by `news_bot.save_json`, a direct truncating writer, plus concrete job timeout/cancellation/reset-retry interruption exposure. This satisfies the conditional entry gate without claiming a historical corruption incident.
- Scope is exactly one responsibility group: crawler snapshot JSON writes. Append-only `archive/*.jsonl` and `delivery_log.jsonl`, Daily/Weekly outboxes, channel queue, and transient handoffs are untouched.
- Implementation: same-directory exclusive `.nuclens-atomic-*.tmp`, complete legacy JSON serialization, flush, close, `os.replace`, and cleanup on failure. Existing mode is copied; new files use mode 0666 subject to process umask, matching `Path.write_text` creation semantics. The temp residue pattern is ignored.
- `fsync` is intentionally omitted: workflow success followed by git commit/push is the relevant persistence boundary; local disk survival across runner/power loss is not consumed as durable state.
- Failure injections passed for temp-open failure, replace failure, existing file, malformed existing file, empty dict/list, simulated ENOSPC, and interrupted partial write. Every pre-replace failure preserved the complete old file and removed temp residue. All three owner wrappers route through the hardened writer.
- Validation at final commit `ac673de`: atomic + V5 harness 22 tests OK; exact characterization digest unchanged; 3 hash seeds/import/workflow smoke inherited through the harness; full root 1,470 tests OK in 94.057s; diff check clean.
- PR CI `33879823426` passed at `ac673de` in 1m12s. Merge Gate: fresh `origin/main` remained verified SHA `7432fc5`; PR clean/mergeable with no required review; final diff only `.gitignore`, `news_bot.py`, and the atomic failure-injection test; no state/config/schema/prompt/model/threshold/generated artifact or append-only writer change; no active/queued Crawl, Daily, or Weekly workflow. Gate result: PASS.
- PR #81 merged at `a1dfd5d`. Immediate main-push Python run `33880064664` checked out that exact SHA and succeeded from 2026-09-04T13:47:56Z to 13:49:20Z (job duration 1m18s). It produced no production state by design. At 13:49:40Z no production workflow was active or queued.
- The writer itself requires a naturally occurring Crawl or Daily pre-brief for post-merge production verification. The next scheduled Crawl type is expected after 15:11Z, so V5.1 §9 forbids repeated polling or a validation-only dispatch. Checkpoint saved and session paused safely with one unverified merge.

## PHASE 2 production verification (complete)

- First relevant production execution: Nuclear news crawl run `33890549173`, existing `backup_watchdog` recovery automation for the missing `2026-09-04T15:00:00Z` slot; event `workflow_dispatch`, trigger state `schedule_missing_recovery`; started 2026-09-04T15:37:29Z and completed 16:12:51Z; conclusion `success`; checkout SHA exactly PR #81 merge `a1dfd5d1b7d775ab65cba9d80a9e04a9523a0024`.
- Run-attributed commits are isolated and linear: claim `d14db7437714ec60d1ff6fd6cb2cc68f8f98f9da` with parent `a1dfd5d`, then state `dab3b194bc13fc29934fb19d7ff9c5e2b59be874` with parent `d14db74`. The following scheduled run `33894414250` correctly recognized the same completed slot and skipped collection.
- Collection/build metrics were normal for the input: 43 candidates, 42 URL/title and fuzzy-unique, 39 after semantic dedup, 39 new articles, Gemini 3 calls; collection state `success_with_articles`. Existing source-health and candidate-headroom warnings reported no service impact and no new failure domain.
- The state commit changed the expected crawl-owned set: the three hardened snapshots plus crawl/discovery/adaptive state, append-only archive/delivery log, and daily publication/event refresh files. No code, config, workflow, prompt, model, threshold, or unrelated persistent-state owner changed.
- `sent.json`, `curated.json`, and `digest_queue.json` all parsed successfully. Top-level types and record schema signatures were unchanged; all common curated/sent/queue identities retained their relative order. Curated changed by 31 additions and 74 retention removals; queue changed 583 to 577 entries; no new schema signature appeared. No tracked `.nuclens-atomic-*.tmp` residue exists.
- Pip and embeddings caches restored successfully. Web build completed with 10,216 archive records, 4,737 displayed articles, 600 briefing articles, 579 issue cards, 417 detail pages, and 49 date briefs; Cloudflare deploy succeeded and live smoke returned 4,737 articles, latest briefing 2026-09-04, and six valid JSON outputs. Failure-domain publication recorded collect/state/build/deploy/smoke all `success`, build mode `ok`, identity quarantined `0`.
- PHASE 2 result: COMPLETE; PR #81 removed from the unverified-merge set. Latest observed `main` is the state-only descendant `a83f3bba4bf71558c4b5e89642b87a270eb87d31`.

## PHASE 3 local gate (complete; PR pending)

- PHASE 0 map reconciliation found no post-baseline change to `web/build_data.py`; intervening changes were the V5 harness, PHASE 2 writer, and expected production state commits. Repository-wide patch/monkeypatch/direct-assignment search found no consumer patching `strip_org_prefix` or its private regexes.
- Selected exactly one safest candidate: publication-title organization-prefix stripping. Inputs are title plus organization labels and output is one string. It has no network, write, environment access, mutable-state mutation, identity, Gemini, ranking, dedup threshold, path-base, or giant-module dependency.
- Commit `04cd09a` moves only `strip_org_prefix` and its two private compiled regexes to `web/publication_title.py`; `web/build_data.py` imports the same public symbol after its existing repository-root path setup. Production entrypoints remain `python web/build_data.py`; the new pure module does not import the giant module and creates no cycle.
- The three moved AST nodes are identical before/after. A candidate mutation that disables acronym recognition is killed by two boundary assertions in `PublicationTitleTests`, both before and after extraction; no source mutation remains. Five candidate tests pass normally.
- Runtime call-time lookup was verified by replacing `build_data.strip_org_prefix`, invoking `load_publications`, and observing the replacement result. This preserves current/future patch behavior at the existing public symbol even though no repository consumer currently patches it.
- V5 harness 13/13 passed, including exact characterization, three hash seeds, frozen Gemini request counter, cache bytes, CLI, imports, workflow execution mode, and trigger contract. Full root 1,470/1,470 and deploy-mode web offline 561/561 with three intentional skips passed. Node syntax/date/weekly/trend/event/admin render and real-DOM contracts passed.
- `render_smoke.mjs` was intentionally excluded from the offline gate after a local attempt exited before test execution because Playwright is not installed. Its documented contract is a live-site Daily workflow check after `npm install --no-save playwright@1` and Chromium installation; it is not an offline test and this extraction does not alter browser code.
- Final staged diff: two files, 47 insertions and 40 deletions; no state, schema, generated artifact, config, workflow, prompt, model, threshold, API, cache, or unrelated cleanup change. PR #82: https://github.com/wavyhairs/nuclens-v2/pull/82.
- PR CI run `33916811781` checked out exact head `04cd09a2d5af3b10313b93b6c4d2aae069a10525` and passed from 2026-09-04T20:33:24Z to 20:34:30Z; root and front-end date/selector jobs both succeeded. Common Merge Gate is now in progress.

## PHASE 0 audit (complete)

All maps below use baseline SHA `bbe62a4c06c85d28962a54ee85d01db9109079dc`. No production code, workflow, configuration, state, secret, or automation toggle was changed during the audit.

### Architecture map

| Module | Size / top-level functions / longest function | Responsibilities and coupling |
|---|---|---|
| `web/build_data.py` | 6,776 lines / 160 / `build` 768 lines | web data orchestration, story/issue grouping, trends, publications, entities, admin artifacts; 18 internal imports; no `web/__init__.py`; direct-script mode inserts repository root into `sys.path` |
| `news_bot.py` | 3,423 / 83 / `main` 503 | source collection, parsing, curation, cache/state/queue persistence, diagnostics; 15 internal imports; patched public functions/constants and mutable runtime state |
| `daily_brief.py` | 1,898 / 45 / `plan_briefs` 333 | select, plan/claim/send/confirm, queue/outbox/log transitions; 14 internal imports |
| `weekly_bot.py` | 1,534 / 41 / `format_weekly` 153 | weekly aggregation/synthesis and plan/claim/send/confirm; run-local `_STORY_CACHE` and `_CONTRACT_CACHE` |
| `issue_continuity.py` | 1,238 / 29 / `verdict_for` 136 | high-risk issue continuity and stable identity; imports story identity/fingerprint/cluster, event stage, admin overrides |
| `story_identity.py` | 270 / 14 / `audit_registry` 47 | canonical and legacy-compatible story identity; high-risk, leave intact absent concrete defect |

- Internal cycle: `news_bot.py -> email_ingest.py -> news_bot.py` (existing direct cycle).
- Significant mutable owners: `news_bot.SOURCE_FETCH_ERRORS`, `SOURCE_FETCH_DIAGNOSTICS`, `QUOTA_EXHAUSTED`, `CONFIG_ERROR`; `gemini_client._CALL_LOG`; weekly run-local caches. Do not duplicate owners.
- Monkeypatch-sensitive targets include `news_bot.RSS_SOURCES`, `OFFICIAL_DIRECT_SOURCES`, source fetch functions, Gemini wrappers, batch constants, delivery path and quota state; `daily_brief` Gemini functions/datetime/path constants; weekly result/report/channel paths; `dedup` Gemini wrappers. Any extraction must preserve call-time lookup.
- Path/import contract: root scripts generally derive persistent paths from `__file__`; `news_bot.py` state paths are working-directory relative; production invokes `python news_bot.py` and `python web/build_data.py` from repository root. `web/build_data.py` is not a package module and supports `BOT_DIR`, `OUTPUT_DIR`, `ADMIN_OUTPUT_DIR`, and `AUDIT_OUTPUT_DIR` overrides.

### Workflow execution contract

| Workflow | Trigger and timing | Concurrency / checkout / relevant commands |
|---|---|---|
| Nuclear news crawl | schedule `11 */3 * * *`, dispatch | `nuclens-state`, no cancellation, latest `main`, Python 3.12; `python news_bot.py`, embedding refresh, `python web/build_data.py`; crawl job 45m, deploy step 8m |
| Daily Brief | schedule `25 19 * * *` (04:25 KST), successful crawl `workflow_run`, dispatch | `nuclens-state`, no cancellation, latest `main`, Python 3.12; pre-brief `news_bot.py`, daily plan/send/confirm, web build/deploy/audio |
| Weekly report | schedule `7 8 * * 5` (17:07 KST Friday), successful crawl `workflow_run`, dispatch | separate `weekly` group, no cancellation, latest `main`, Python 3.12; weekly plan/send/confirm then reusable web deploy |
| Deploy web | push to `web/**`, entity registry, weekly reports, functions or workflow; call; dispatch | `deploy-web`, cancel stale; checkout event/input ref; Python 3.12; build, offline tests, Wrangler 4 deploy |
| Python tests | PR/push for Python/tests/config paths; dispatch | per-ref group, cancel stale; Python 3.12; 20m; root unittest plus offline Node tests |
| Deploy crawl watchdog | push to worker/workflow; dispatch | dedicated group, cancel stale; Node test and Wrangler 4 deploy; 10m |

- Merge-trigger awareness: Python/test changes immediately trigger Python tests on push to main; `web/**` changes also trigger Deploy web; watchdog paths trigger its deploy. Root production Python outside `web/**` waits for crawl/Daily/Weekly schedule or recovery unless another path trigger applies.
- Avoid Daily send window around 04:25 KST and Weekly send window around 17:07 KST Friday; prefer shortly after successful delivery.
- At PHASE 0 close, GitHub reported no active run. Latest crawl `33873720568`, Daily `33874189982`, and Weekly `33874190293` completed successfully at the baseline head/state descendant.
- `AUTOMATION_ENABLED=true` and `BRIEFING_ENABLED=true`; neither was modified.

### State ownership map

| Class | Files | Writer / reader / lifecycle and reset contract |
|---|---|---|
| Snapshot JSON | `sent.json`, `curated.json`, `digest_queue.json` | crawl and Daily pre-brief via `news_bot`; read by collection, Daily, Weekly, web and monitoring; tracked and committed after each production stage; direct truncating writes currently |
| Snapshot JSON | `outbox.json` | Daily plan/claim/send/confirm; tracked claim is committed before send; `outbox_result.json` reapplies send result after reset; absence means no durable pending Daily result |
| Snapshot JSON | `channel_outbox.json` | Daily and Weekly channel queue; tracked claim/confirmation state, sent items never return to pending; shared across concurrency groups |
| Snapshot JSON | `weekly_reports.json` | Weekly plan/confirm, web reader; tracked; `weekly_result.json` carries send result across reset within the job |
| Snapshot JSON | `crawl_runs.json` | crawl preflight claim/finalize gate; tracked; `crawl_gate_result.json` is the same-step decision handoff |
| Snapshot JSON | `discovery_state.json`, `adaptive_state.json` | crawl discovery planners; tracked across runs for query TTL/spend/minted/retired behavior |
| Snapshot/cache JSON | `publications.json`, `event_schedule.json`, `daily_leads.json`, `trend_insights.json`, `issue_llm_reviews.json`, `keei_llm_matches.json`, `issue_insights.json` | crawl/Daily/Weekly/web producers as applicable; tracked; some are regenerable but loss changes cost, continuity, or displayed output |
| External overlay | `admin_overrides.json` | Cloudflare KV is external source; sync tool writes local tracked overlay; readers must preserve unknown kinds/fallback behavior |
| Append-only JSONL | `archive/*.jsonl`, `delivery_log.jsonl` | crawl/Daily/Weekly append; tracked with `merge=union`; snapshot replacement is forbidden in normal operation |
| Ephemeral cache | `embeddings.json`, `web/public/data/audio/**` | ignored; Actions cache owners are crawl and Daily respectively; restore consumers include web deploy; absence causes recompute/fallback, not state reset |
| Transient handoff | `outbox_result.json`, `weekly_result.json`, `crawl_gate_result.json` | ignored, same-job send/confirm or claim handoff; intentionally survives `git reset --hard`; regenerated/reused only in its retry path; absence means no handoff result |
| Generated artifact | `web/public/data/**`, `web/public/rss.xml`, `web/public/issue/**`, `web/public/brief/**`, `web/public/admin/data/**`, `web/_audit/**` | ignored build/deploy/audit output; regenerated by web builder; not persistent source of truth |

- Forward compatibility: no schema/path/serialization change is planned. Code-only extraction is revert-compatible because it must emit byte-identical state. Any state-hardening change must preserve exact bytes and new-file semantics.
- Concurrency risk (separate-project candidate): Daily/crawl share `nuclens-state`, but Weekly uses a separate group while also writing `channel_outbox.json` and `delivery_log.jsonl`; PHASE 0 observed overlapping runs. Do not redesign concurrency in V5.

### Write, nondeterminism, and runtime findings

- Concrete PHASE 2 exposure exists: critical tracked snapshots are written with direct `Path.write_text()` truncation while production jobs have cancellation/timeout and reset/retry paths. `curated.json` was about 16.7 MB and `digest_queue.json` about 3.9 MB at baseline. No proven historical partial-file incident was found, so PHASE 2 must use the explicit interruption exposure and protect one responsibility group only.
- Existing atomic writers include `publications.json` and `event_schedule.json` temp+replace paths; append-only archives/logs must not use snapshot replacement.
- Time is intentionally runtime-dependent; KST/UTC use is mixed by contract. Archive/file glob consumers are sorted. No production `random`/UUID use was found. Object `id()` is used only for in-process membership/order retention, not as persisted identity. Set/dict surfaces require 3-hash-seed contract comparison in PHASE 1.
- Runtime: production Actions use Ubuntu latest + Python 3.12; local audit used Python 3.14.6. Local resolved versions: requests 2.34.2, google-genai 2.18.1, feedparser 6.0.14; Node 24.18.0/npm 11.16.0; no global Wrangler, workflows use floating `wrangler@4`. Requirements remain lower-bound floating and were not changed.
- Gemini defaults: primary `gemini-3.1-flash-lite`; synthesis/review/insight/script defaults use `gemini-3.5-flash-lite` as defined in code. Repository has no `GEMINI_MODEL` variable override. Prompts/models/config remain frozen.

### PHASE 0 validation and risk disposition

- Root suite: 1,448 tests passed in 125.457s.
- Deploy-mode web suite: 561 tests passed, 3 intentional skips in 21.487s.
- Offline Node contracts: app parse, date window, weekly selector/sections, event calendar, trend period state, admin gate/render all passed.
- Import smoke: all major production modules plus direct-script-style `web/build_data` import passed without secrets, observed network, production write, or `SystemExit`; worktree remained clean.
- Advisory live-data test without deploy skip: one existing failure, weekly totals ratio 3.3125 > 2.0. This is explicitly non-blocking in the test/workflow contract and is recorded as an operational data-quality follow-up, not fixed in V5.
- Other separate-project candidates: workflow state concurrency redesign; dependency/runtime pinning; existing `news_bot`/`email_ingest` cycle; live weekly sample imbalance. No PHASE 0 STOP condition was found.
