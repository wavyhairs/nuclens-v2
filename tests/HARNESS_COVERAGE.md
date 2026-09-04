# V5.1 refactor harness coverage

Baseline: `bbe62a4c06c85d28962a54ee85d01db9109079dc`.

| Responsibility | Executed by harness | Behavior protected | Known unprotected surface |
|---|---|---|---|
| `story_identity.ensure`, `display_group_id` | Yes | exact IDs, legacy handling, Unicode distinction, mutated metadata | registry migration against full production history |
| `ranking.rank_and_select` and scoring/dedup internals | Yes | exact selected order, scores, breakdowns, duplicate drops, grouping metadata | live semantic/editorial LLM callbacks |
| `issue_continuity.annotate` | Yes | exact follow-up verdict, progression, penalty, identity inheritance metadata | full historical delivery-log distribution |
| `web/build_data.cluster_selected_articles` | Yes | exact issue IDs, grouping, member order, approved/rejected overrides | live embeddings and LLM review |
| `channel_queue.ensure_batch`, `add_item` | Yes | exact idempotent state transition and item order | Telegram publish transport |
| `news_bot.save_json` | Yes | exact UTF-8 JSON bytes and terminal-newline behavior | collection and real external feeds |
| `daily_brief.save_queue`, `save_outbox` | Yes | exact UTF-8 JSON bytes | live LLM synthesis and Telegram send |
| `daily_brief.region` + shared `ranking.rank_and_select` selection path | Yes | exact Daily region mapping and core selected order | final live synthesis/card rendering |
| `weekly_bot.weekly_stories`, `week_window`, `week_id` | Yes | exact Weekly story selection and KST calendar boundaries | live weekly synthesis and delivery |
| `channel_queue.save_queue` | Yes | exact UTF-8 JSON bytes including final newline | production queue concurrency |
| `gemini_client.call_json` | Yes | canonical request hash, frozen response, call counter, unknown-request failure | real model quality and quota behavior |
| `llm_cache.load`, `save`, `is_current` | Yes | exact envelope bytes, hit and stale-version behavior | cache concurrency |
| major module imports | Yes, subprocess | blank-secret import succeeds, internet blocked, production source hashes unchanged | code executed only under CLI entrypoints |
| workflow commands/path filters | Yes, static contract | production entrypoints, offline CI command and path triggers | GitHub runner/service availability |
| workflow direct-script import mode | Yes, temporary checkout subprocess | repo-root cwd, blank secrets, blocked network, import initialization, exit code | destructive/live CLI main bodies |

The fixture intentionally includes normal, multi-source, stage progression,
must-separate, legacy/malformed, duplicate/reordered JSONL, override, KST/daily/weekly/month
boundaries, Korean/Hanja/emoji, and NFC/NFD-shaped records. It contains synthetic data only.

## Required negative controls

Before merging a refactor phase, run a temporary mutation without committing it and record
the detecting test. The Phase 1 bootstrap controls are: ranking weight, duplicate threshold,
sort direction/omission, story-id seed, Gemini prompt, and snapshot-write omission. Later
extraction phases must add a candidate-specific mutation before their first PR.

Phase 1 bootstrap results (2026-09-04; runtime monkeypatches only, no mutation retained):

| Mutation | Detected? | Detecting test |
|---|---:|---|
| ranking `korea_relevance` weight +0.5 | yes | `test_exact_business_characterization` |
| duplicate threshold 0.82 → 1.01 | yes | `test_exact_business_characterization` |
| selected result order reversed | yes | `test_exact_business_characterization` |
| first story-id hash input removed | yes | `test_exact_business_characterization` |
| Gemini system prompt changed | yes | `test_unknown_gemini_request_fails_closed` |
| snapshot write omitted | yes | `test_json_snapshot_serialization_is_exact` |
