# V5.1 refactor exit criteria

Registered before the first production-code refactor. Baseline commit:
`bbe62a4c06c85d28962a54ee85d01db9109079dc`.

## Mandatory safety criteria

- Every merged phase has a green required CI result and satisfies the V5.1 Merge Gate.
- At most one merged refactor PR may await its first relevant production verification.
- Characterization output is byte-for-byte stable under `PYTHONHASHSEED=1,17,101`.
- Real internet access is blocked in characterization runs; unknown external requests fail.
- Production source/config/workflow hashes do not change during import-safety checks.
- Golden fixtures and comparator normalization are not changed in a production refactor PR.
- No state schema, delivery semantics, ranking weights, dedup thresholds, prompt text, or
  external call count changes are accepted as refactoring.
- Each changed responsibility has a passing negative control capable of detecting a
  behavior change.

## Structural goals

- `web/build_data.py`: baseline 6,776 lines, 160 top-level functions, `build` 768 lines.
- `news_bot.py`: baseline 3,423 lines, 83 top-level functions, `main` 503 lines.
- `daily_brief.py`: baseline 1,898 lines, 45 top-level functions, `plan_briefs` 333 lines.
- `weekly_bot.py`: baseline 1,534 lines, 41 top-level functions, largest operational
  responsibility 153 lines.
- `issue_continuity.py`: baseline 1,238 lines, 29 top-level functions, `annotate` 136 lines.
- `story_identity.py`: baseline 270 lines, 14 top-level functions, largest function 47 lines.
- Mutable global owners: `news_bot` source-error/diagnostic/quota/config state;
  `gemini_client._CALL_LOG`; `weekly_bot` story/contract caches.
- Direct internal import counts (fan-out observation): web builder 18, news bot 15, daily 14,
  weekly 14. `news_bot -> email_ingest -> news_bot` is the observed circular dependency.
- Externally patched/imported names are concentrated in `news_bot`, Daily Gemini/datetime/path,
  weekly state paths, and dedup Gemini hooks; these names remain compatibility boundaries.
- Relevant persistent snapshot sizes at baseline: `curated.json` about 16.7 MiB and
  `digest_queue.json` about 3.9 MiB. These measurements motivate Phase 2 qualification but
  are not line-count or rewrite targets.
- Baseline full root regression: 1,448 tests, 125.457 s on local Python 3.14.6. Production
  workflows use Python 3.12. Phase 1 characterization runs on the harness commit were
  2.063/2.018/2.020 s (median 2.020 s); the representative frozen Gemini contract makes
  exactly one registered call with a request-hash multiset of cardinality one.
- Target only pure, behavior-protected responsibility extraction; no target line count is
  mandatory. A smaller file that weakens ownership or testability does not qualify.
- The `news_bot -> email_ingest -> news_bot` import cycle, workflow concurrency redesign,
  dependency pinning, and live-data balance are separate projects unless a later V5.1 gate
  explicitly qualifies a narrow change.

## Completion decision

The refactor completes only when all eligible, pre-registered phase candidates have either:

1. merged and passed their first relevant production run verification; or
2. been explicitly marked ABANDON with the governing reason and evidence.

Performance work additionally requires five comparable baseline and candidate runs, with
median and IQR recorded, one measured bottleneck, and no semantic-output difference.
