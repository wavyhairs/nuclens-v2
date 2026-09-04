# Nuclens V5.1 Refactor Checkpoint

- schema_version: `V5.1`
- updated_at_utc: `2026-09-04T13:12:27Z`
- current_phase: `PHASE 1`
- current_sub_step: `PHASE 0 complete; create Refactor Safety Harness`
- initial_baseline_main_sha: `bbe62a4c06c85d28962a54ee85d01db9109079dc`
- latest_main_sha: `bbe62a4c06c85d28962a54ee85d01db9109079dc`
- architecture_state_map_baseline_sha: `bbe62a4c06c85d28962a54ee85d01db9109079dc`
- work_branch: `refactor/v5-checkpoint`
- merge_mode: `autonomous-merge-permitted`
- harness_version: `not created (PHASE 1 next)`
- characterization_baseline: `root 1,448 tests OK; deploy-mode web 561 tests OK (3 intentional skips); Node offline contracts OK`
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

No V5.1 PRs created, merged, or reverted.

## Validation status

- last_passed_tests: `python -m unittest discover -s tests -q: 1,448 OK in 125.457s; NUCLENS_SKIP_DATA_GATES=1 web suite: 561 OK, 3 skipped in 21.487s; Node offline contracts: all OK`
- last_failed_tests: `advisory-only web suite without NUCLENS_SKIP_DATA_GATES: 1/561 failed`
- failure_cause: `live weekly sample totals [50,48,112,159,129,140], max/min 3.3125 > advisory threshold 2; explicitly excluded from deploy gate by existing contract`
- workflows_to_verify: `per-PR impact path: Python tests; Deploy web; Nuclear news crawl; Daily Brief; Weekly report; Deploy crawl watchdog`
- verification_conditions: `checkout contains merge SHA or its state-descendant; workflow success; expected state-only diff; no schema/order/identity/ranking/grouping/request-count drift; no degraded/failure increase`

## ABANDON history

None.

## Next action

Create PHASE 1 harness-only branch from current `origin/main`; freeze sanitized fixtures and exact contracts; add network/write/import/workflow smoke protection, dependency reporting, coverage map, exit criteria, and prove all negative controls before opening a PR.

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
