# Nuclens V5.1 Refactor Checkpoint

- schema_version: `V5.1`
- updated_at_utc: `2026-09-05T01:32:08Z`
- current_phase: `PHASE 5`
- current_sub_step: `PR #85 main-push CI passed; waiting under §9 for the first natural Crawl/Daily run that executes merge 77b4f80 or its state-only descendant`
- initial_baseline_main_sha: `bbe62a4c06c85d28962a54ee85d01db9109079dc`
- latest_main_sha: `77b4f8057072fafa60c878ce445208dfe469e398`
- architecture_state_map_baseline_sha: `bbe62a4c06c85d28962a54ee85d01db9109079dc`
- work_branch: `refactor/v5-checkpoint (wait state; merged PR #85 branch retained remotely)`
- merge_mode: `autonomous-merge-permitted`
- harness_version: `4bdfd75 (test: add V5 refactor safety harness)`
- characterization_baseline: `SHA-256 b9753b325a00505f4b496b6e907ad35c5cf472a5b36d0326c01e4fbe916a5786; 13 tests; 3 stable runs 2.063/2.018/2.020s; PYTHONHASHSEED 1/17/101 identical`
- state_schema_changed: `no`
- persistent_state_changed: `no`
- pending_operating_verification: `PR #85 merge 77b4f80: first natural Crawl or Daily pre-brief that checks out the merge or a state-only descendant; main-push Python already passed`
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

PHASE 1 PR #80 merged and verified at `7432fc5`. PHASE 2 PR #81 merged and verified at `a1dfd5d`. PHASE 3 PR #82 merged and verified at `1bc3965`. PHASE 4 PR #83 merged and verified at `9b85797`. PHASE 4 PR #84 merged and verified at `2610183`. PHASE 5 PR #85 (`refactor: extract curation value normalization`) passed both exact-head CIs and the Merge Gate, then merged at `77b4f80` on 2026-09-05T01:30:22Z. PR #85 is the sole unverified V5.1 merge.

## Validation status

- last_passed_tests: `PHASE 5: local/post-main gates and both PR CIs passed; Merge Gate PASS; main-push Python 33936367064 passed at exact merge 77b4f80 in 1m11s`
- last_failed_tests: `advisory-only web suite without NUCLENS_SKIP_DATA_GATES: 1/561 failed`
- failure_cause: `live weekly sample totals [50,48,112,159,129,140], max/min 3.3125 > advisory threshold 2; explicitly excluded from deploy gate by existing contract`
- workflows_to_verify: `per-PR impact path: Python tests; Deploy web; Nuclear news crawl; Daily Brief; Weekly report; Deploy crawl watchdog`
- verification_conditions: `checkout contains merge SHA or its state-descendant; workflow success; expected state-only diff; no schema/order/identity/ranking/grouping/request-count drift; no degraded/failure increase`

## ABANDON history

None.

## Next action

On resume, fetch main and inspect the first Crawl or Daily pre-brief created after 2026-09-05T01:30:22Z. It must check out `77b4f80` or a state-only descendant and actually execute `news_bot.py`; a claim-only/skip run is insufficient. Attribute only that run's state commits, then verify normal collection/curation/request/cache/build metrics, schema/order/identity invariants, expected state scope, and no new degraded/failure domain. If it passes, remove PR #85 from the unverified set, decide the PHASE 5 exit criterion, and continue to PHASE 6. Do not force a validation-only run or repeatedly poll.

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
- Merge Gate: fresh `origin/main` equals PR base `a83f3bba4bf71558c4b5e89642b87a270eb87d31`; no intervening commit or stale state exists. PR is open and mergeable with no required review, exact head/CI success, and only the declared two-file 47+/40- diff. Relevant 18 tests reran successfully, diff checks are clean, and GitHub reported no active or queued workflow. No Daily/Weekly send window or critical state commit is in progress. Gate result: PASS; ordinary PR merge authorized.
- PR #82 merged by ordinary merge at `1bc396549496cb8e4443ad2c6b9f2ad69e5bede9` on 2026-09-04T20:36:38Z. Main-push Python run `33917083106` and first relevant Deploy web run `33917083131` started at the exact merge SHA; both were in progress when this post-merge checkpoint was written.

## PHASE 3 production verification (complete)

- Main-push Python run `33917083106` checked out merge SHA `1bc3965` and passed in 1m15s. Its annotations were existing intentional warning/error fixtures emitted by regression tests, not job failures or production observations.
- First relevant production run: Deploy web `33917083131`; push event; checkout SHA exactly `1bc396549496cb8e4443ad2c6b9f2ad69e5bede9`; started 2026-09-04T20:36:40Z, completed 20:54:52Z; deploy job 18m07s; conclusion `success`. It produced no persistent state commit, and freshly fetched `origin/main` remained the exact merge SHA.
- Dependency, embeddings, and audio caches all hit. Admin override sync reported no change. Build processed 10,236 archive records into 4,746 news items; card clustering produced 453 issues/15,100 candidates; evidence attachment completed for 727 then 952 records with 120,778 candidates before review; LLM review asked 0 and failed 0; final candidate audit was 124,474 full and 5,000 shipped. Gemini calls: 0.
- Output was 4,746 displayed articles, 617 briefing articles, 596 issue cards, 431 detail pages, and 50 date briefs. Build mode was `ok`, identity quarantined 0. Existing archive repair (17 quarantined/642 date-normalized) and preselection-headroom (15/952, 1.6%) warnings remained advisory and showed no new failure/degraded domain caused by the extraction.
- All app/date/weekly/event/trend/admin contracts, real-browser admin DOM, and 561 web tests passed in the production workflow. Cloudflare deployment completed at `https://44c39c5a.nuclens-v2.pages.dev`; live missing-data-path smoke returned the expected 404; `/admin/` and `/admin/data/merges.json` both returned the expected 401.
- PHASE 3 result: COMPLETE; PR #82 removed from the unverified-merge set. No revert or retry was required.

## PHASE 4 iteration 1 local gate (complete; PR pending)

- Selected one additional pure presentation responsibility adjacent to the verified module: `gist_adds_nothing(gist, title_kr) -> bool`. It has no network, write, environment, mutable-state, identity, Gemini, ranking, dedup, path-base, or giant-module dependency; repository search found no direct patch consumer.
- Commit `a9753d2` moves only the exact function body into `web/publication_title.py` and imports it back into `web/build_data.py`. Existing production entrypoint and public symbol remain unchanged; the dependency direction is still one-way and acyclic.
- The moved function AST is identical. A runtime mutant changing the similarity boundary from 0.7 to 1.0 is killed by the live-derived title/gist regression test before and after extraction. An initial post-extraction diagnostic incorrectly replaced only the new module attribute after `build_data` had imported the function object, so it did not exercise the mutant; the command was corrected to replace the existing public symbol, detected the expected failure, and required no source change.
- `load_publications` was exercised with a temporary publication and a patched `build_data.gist_adds_nothing`; the patched return value controlled gist removal, confirming call-time lookup compatibility.
- V5 harness 13/13, full root 1,470/1,470, deploy-mode web offline 561/561 with three intentional skips, Python compile, and all offline Node date/weekly/trend/event/admin render/real-DOM contracts passed. Final diff is exactly two files, 23 insertions and 22 deletions, with no unrelated change.
- PR #83: https://github.com/wavyhairs/nuclens-v2/pull/83.
- PR CI `33919089786` passed at exact head `a9753d2a30d0801144a26c5ce63947746137c7bd` from 2026-09-04T21:01:12Z to 21:02:27Z. Common Merge Gate is in progress.
- Merge Gate: fresh `origin/main` equals PR base `1bc396549496cb8e4443ad2c6b9f2ad69e5bede9`; no intervening state/code commit exists. PR is open and mergeable with no required review, exact successful CI head, and only the declared two-file 23+/22- diff. Relevant 14 tests and compile/diff checks reran successfully. GitHub reported no active or queued workflow, and no Daily/Weekly critical window or state commit was active. Gate result: PASS; ordinary PR merge authorized.
- PR #83 merged at `9b85797a575ec2e072b31c08a8c39a8970b94ccc` on 2026-09-04T21:04:01Z. Main-push Python `33919325642` and Deploy web `33919325566` started at the exact merge SHA and were in progress at checkpoint time.

## PHASE 4 iteration 1 production verification (complete)

- Main-push Python run `33919325642` passed at exact merge SHA `9b85797` in 1m15s. First relevant Deploy web run `33919325566` checked out that exact SHA and passed from 2026-09-04T21:04:04Z to 21:26:54Z; deploy job duration 22m46s.
- All dependency/embedding/audio caches hit. With the same source state as PHASE 3 verification, metrics were identical: 10,236 archive records, 4,746 displayed news, 453 initial issues/15,100 candidates, 727 first-pass evidence attachments/120,778 candidates, review asked 0/failed 0, 124,474 full candidates, 5,000 shipped, 617 briefing articles, 596 issue cards, 431 detail pages, and 50 date briefs. Gemini calls were 0; build mode `ok`; identity quarantined 0.
- Existing archive repair and 15/952 (1.6%) preselection-headroom warnings were unchanged advisory signals. All web/Node/admin/real-browser tests passed. Cloudflare deployed `https://2b88e1b5.nuclens-v2.pages.dev`; missing-path live smoke returned expected 404 and both admin URLs returned expected 401.
- Fresh `origin/main` remained exact merge SHA `9b85797`; the deploy produced no persistent state commit or unexpected diff. The longer duration versus the immediately prior 18m07s run occurred at identical input/output/candidate/API/cache metrics and is not attributed to this pure function move.
- PHASE 4 iteration 1 result: COMPLETE; PR #83 removed from the unverified set. No revert or retry was required.

## PHASE 4 iteration 2 local gate (complete; PR pending)

- Selected exactly one cohesive pure responsibility: publication display classification. `publication_relevance`, `publication_drop_reason`, their three private compiled regexes, and the public immutable relevance tuple moved to `web/publication_policy.py`.
- Every function is dict-to-string with no network, write, environment, mutable-state mutation, identity, Gemini call, ranking, dedup threshold, path-base, or giant-module dependency. Repository-wide search found no existing patch of these functions/constants. `build_data` re-exports the public functions and relevance tuple; private regex ownership is singular and the new module is acyclic.
- All six moved AST nodes are identical. A runtime mutant removing the explicit `off_topic=False` precedence is killed before and after extraction by the documented `Workshop on Regulatory Harmonisation` regression. Seven direct publication behavior tests and the 13-test V5 harness pass.
- Temporary-fixture execution proved that patches to `build_data.publication_drop_reason` and `build_data.publication_relevance` still control `load_publications` at call time; the public relevance tuple retains object identity with the new owner.
- Full root 1,470/1,470, deploy-mode web offline 561/561 with three intentional skips, Python compile, and all offline Node date/weekly/trend/event/admin render/real-DOM contracts passed. Final diff is exactly `web/build_data.py` plus new `web/publication_policy.py`, 93 insertions/82 deletions, with no unrelated change.
- PR #84: https://github.com/wavyhairs/nuclens-v2/pull/84.
- PR CI `33921625269` passed at exact head `c1828e35043e839e63938e9ab5f11abae4a6ea48` from 2026-09-04T21:33:12Z to 21:34:37Z. Common Merge Gate is in progress.
- Merge Gate: fresh `origin/main` equals PR base `9b85797a575ec2e072b31c08a8c39a8970b94ccc`; no intervening state/code commit exists. PR is open and mergeable with no required review, successful exact-head CI, and only the declared two-file 93+/82- diff. Relevant 20 tests and compile/diff checks reran successfully. GitHub reported no active or queued workflow and no critical state/send window. Gate result: PASS; ordinary PR merge authorized.
- PR #84 merged at `261018338638d169659a68eb720f1b6cd0b32084` on 2026-09-04T21:36:09Z. Main-push Python `33921851020` and Deploy web `33921851015` started at the exact merge SHA and were in progress at checkpoint time.

## PHASE 4 iteration 2 production verification (complete)

- Main-push Python run `33921851020` passed at exact merge SHA `2610183` in 1m20s. First relevant Deploy web run `33921851015` checked out that exact SHA and passed from 2026-09-04T21:36:11Z to 21:58:18Z; deploy job duration 22m03s.
- Dependency, embedding, and audio caches all hit. Metrics remained identical to the prior verified run: 10,236 archive records, 4,746 displayed news, 453 initial issues/15,100 candidates, 727 first-pass evidence attachments/120,778 candidates, review asked 0/failed 0, 124,474 full candidates, 5,000 shipped, 617 briefing articles, 596 issue cards, 431 detail pages, and 50 date briefs. Gemini calls were 0; build mode was `ok`; identity quarantined 0.
- Existing archive repair and 15/952 (1.6%) preselection-headroom warnings remained unchanged advisory signals. All web/Node/admin/real-browser tests passed. Cloudflare deployed `https://85afee64.nuclens-v2.pages.dev`; the missing-path smoke returned expected 404 and both admin URLs returned expected 401.
- Concurrent normal Daily automation subsequently advanced `origin/main` through state-only commits `aa03222e05ec` (pre-brief crawl state: archive/curated/delivery log/digest queue/discovery/sent), `dbcbdd7b` (Daily delivery confirmation), `846f43d` (operational alert state), and `77d20f2a` (daily leads/trend insights). Their parent chain descends from `2610183`; no V5 code was altered or overwritten.
- PHASE 4 iteration 2 result: COMPLETE; PR #84 removed from the unverified set. No revert or retry was required.

## PHASE 4 exit decision (complete)

- Two gradual follow-up extractions left publication title/gist normalization and publication display classification in small acyclic pure modules while preserving the existing `build_data` entrypoint and patch surface.
- Remaining nearby candidates couple file/time I/O, KEEI/Gemini behavior, ranking, identity, mutable ownership, or provide too little maintenance benefit for the extraction risk. Additional splitting would now primarily divide files rather than reduce maintenance risk.
- PHASE 4 therefore ends under its benefit-versus-risk exit criterion. No candidate reached an ABANDON condition and no STOP condition exists. Proceed to PHASE 5.

## PHASE 5 inventory and candidate freeze (in progress)

- Fresh `origin/main` is state-only descendant `77d20f2`; the branch contains the verified V5 code and no later code drift. `refactor/v5-phase5-curation-normalization` was created directly from it.
- Rechecked mutable owners: `SOURCE_FETCH_ERRORS`/`OFFICIAL_FETCH_ERRORS`, `SOURCE_FETCH_DIAGNOSTICS`, `QUOTA_EXHAUSTED`, `CONFIG_ERROR`, and the curation quality/drop counters remain in `news_bot.py`. Rechecked patch-sensitive source lists/fetch functions, Gemini aliases, batch controls, quota flag, delivery/state paths, time sleep, and atomic writer OS calls; none will move.
- Selected one narrow pure normalization responsibility: `norm_scope`, `norm_topics`, `norm_countries`, and `norm_article_type`, together with only their immutable controlled vocabularies. They have explicit inputs/outputs and no network, write, environment, time, path, state mutation, identity, ranking, dedup, or Gemini behavior.
- Repository search found direct public use through `news_bot` but no existing patch of these functions or vocabulary constants. The extraction must keep every public symbol re-exported from `news_bot`; its existing callers must continue global call-time lookup so a patch on `news_bot.norm_topics` controls `normalize_curation_item`.
- Freeze result: existing `TestControlledTagNorm` 3/3 passed. A pre-extraction runtime mutant increasing the controlled-topic cap from 3 to 4 was killed by `test_topics_whitelist_and_cap` with the expected four-versus-three failure. Recorded AST hashes: scope `c378bdd1`, topics `4a918c39`, countries `c683e251`, article type `7abc4c77`.
- No ABANDON or STOP condition exists.

## PHASE 5 local gate (complete; PR pending)

- Commit `49a0f74` moves exactly the four frozen normalizer bodies and seven controlled-vocabulary expressions into new acyclic root module `curation_normalization.py`; `news_bot.py` imports and re-exports every prior public symbol.
- All four moved function ASTs and all seven moved constant-expression ASTs are identical to `origin/main`. The topic-cap mutant was killed again after extraction. The added contract patches `news_bot.norm_topics` and observes its return through `normalize_curation_item`, proving existing call-time patch behavior; identity assertions prove a single vocabulary owner.
- Targeted curation/normalization tests passed 44/44. V5 harness passed 13/13, including three hash seeds, frozen request hashes/call counts, cache/state bytes, temporary-checkout execution mode, and import/network-write guards.
- Full root suite passed 1,472/1,472 in 126.859s. Deploy-mode web offline suite passed 561/561 with three intentional skips in 21.128s. All offline Node date/weekly/trend/event/admin gate/render/real-DOM contracts, Python compile, direct-script smoke, and diff checks passed.
- Final diff is exactly three files, 102 insertions and 70 deletions. It changes no state, schema, persistence, workflow, prompt, model, threshold, ranking, identity, dedup, source collection, API, cache, path, or mutable owner.
- PR #85: https://github.com/wavyhairs/nuclens-v2/pull/85. It was created at initial head `49a0f74c`.
- PR CI `33924299348` checked out exact initial head `49a0f744cd615a260f05c520cf7db025a2cb6f00` and passed from 2026-09-04T22:09:36Z to 22:10:49Z (1m13s).
- Resume audit found `origin/main` advanced linearly from `77d20f2` to `3dfd080` through six automation commits: issue-review/data-gate state, two crawl claim/state pairs, and a final issue-review cache update. Aggregate changed paths are only `archive/2026-09.jsonl`, `crawl_runs.json`, `curated.json`, `delivery_log.jsonl`, `digest_queue.json`, `discovery_state.json`, `issue_insights.json`, `issue_llm_reviews.json`, and `sent.json`; no code, workflow, config, schema owner, prompt, model, or threshold file changed.
- The associated Crawl/Daily/Weekly workflows completed successfully, and GitHub reports no active or queued critical workflow in the inspected recent set. Because §7.1 requires the branch to contain latest main, the initial successful CI is not yet the final merge authorization; integration and replacement CI are pending.
- Merged `origin/main` `3dfd080` into the feature branch without conflicts, producing head `40160e0145691dec2f5dea708793520c4873a822`. Latest state files were accepted unchanged from main. The PR-relative diff remains exactly `curation_normalization.py`, `news_bot.py`, and `tests/test_archive.py` at 102+/70-.
- Post-integration relevant validation passed: targeted curation/normalization 44/44, V5 harness 13/13 in 2.008s, four function AST comparisons identical, and diff check clean. Replacement CI run `33936238616` is queued at exact head `40160e0`.
- Replacement CI `33936238616` passed at exact head `40160e0145691dec2f5dea708793520c4873a822` in 1m05s. Fresh `origin/main` remained `3dfd080`, exactly the integrated base; PR #85 is open, non-draft, mergeable, and has no required review.
- Final diff review is unchanged at the declared three files and 102+/70-. There is no generated state, debug code, mutation, formatting churn, unrelated cleanup, secret, production fixture, config/model/prompt/threshold drift, or moved-body AST difference.
- GitHub reported no active or queued workflow. Current timing is after the Daily and Friday Weekly send windows with ample time before the next send. There is no unverified earlier V5 merge. Common Merge Gate result: PASS; ordinary PR merge is authorized.
- PR #85 merged by ordinary merge at `77b4f8057072fafa60c878ce445208dfe469e398` on 2026-09-05T01:30:22Z. Its parents are latest main `3dfd080` and feature head `40160e0`. Main-push Python run `33936367064` started at the exact merge SHA and is in progress. No Deploy web run is expected from this root-only path change.

## PHASE 5 production verification (waiting)

- Main-push Python run `33936367064` checked out exact merge SHA `77b4f8057072fafa60c878ce445208dfe469e398` and passed from 2026-09-05T01:30:28Z to 01:31:39Z; job duration 1m11s. Logged annotations are existing intentional regression fixtures plus the runner's Node 20 deprecation notice, not production observations or job failures.
- A single post-CI check found no Crawl or Daily run created after the merge. Latest Crawl `33933586931` and Daily `33934961042` both predate `77b4f80`. Fresh `origin/main` remains the exact merge SHA.
- The next normal Crawl schedule type is expected at/after 03:11Z, subject to GitHub scheduling or the existing watchdog recovery. V5.1 §9 forbids a validation-only dispatch and repeated polling; the session is therefore pausing safely with one unverified merge. Local power/session is not required for GitHub automation to continue.
- Resume condition: inspect the first post-merge run once. A skipped already-claimed slot does not exercise the extraction; wait for the first run whose logs show collection/curation execution. Preserve all newer state-only descendants and do not merge another code PR before verification completes.

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
