# Gemini task/profile inventory

Updated: 2026-09-06 (Asia/Seoul)
Baseline: `main` at `2ee5c4ad5720c0e0e8297f951be715ca9396d471`

This inventory follows the central `llm_policy` registry and the production call
sites. `unspecified` means no `thinking_level` is requested; it does not assert that
the API has thinking disabled. No production profile was changed by this review.

| Profile | Class | Call site | Model / requested thinking | Purpose | Deterministic safety net / cache | Failure impact | Human Gold / state |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `curation` | B. Curation | `news_bot.py:2067` | 3.1 Flash Lite / unspecified | title, summary, facts and article features | curation schema/quality/evidence gates; durable article cache and retry queue | High: published article representation | 1/40 Curation Gold; `HUMAN_LABEL_REQUIRED` |
| `issue_review` | A. Identity | `issue_review.py:449` | 3.5 Flash Lite / unspecified | decide whether gray-band issue candidates are the same event | no verdict means no merge; bounded split/retry; fingerprinted soft-stale cache | High: issue merge/split | 60 Identity labels; 3.5 live evaluation 489/540, 51 quota-blocked; `IMPLEMENTED_BUT_NOT_ACTIVATED` |
| `keei_match` | A. Identity | `keei_match.py:180` | 3.1 Flash Lite / unspecified | link KEEI items to issues | failure is not linked; fingerprinted soft-stale cache and bounded re-ask | Medium | Identity Gold available; task-wide 3.1 evaluation 499/540; decision pending |
| `dedup` | A. Identity | `dedup.py:406` | 3.1 Flash Lite / unspecified | pre-curation event deduplication | deterministic candidate formation; failure keeps all articles | High | Identity Gold available; task-wide 3.1 evaluation 499/540; decision pending |
| `dedup_final` | A. Identity | `dedup.py:406` | 3.1 Flash Lite / unspecified | editorial-final event deduplication | stage-specific prompt; failure keeps all articles | High | Identity Gold available; task-wide 3.1 evaluation 499/540; decision pending |
| `issue_insight` | D. Synthesis | `issue_insight.py:339` | 3.5 Flash Lite / unspecified | synthesize the newest material issue change | source-copy/empty rejection; ordinary summary fallback; fingerprinted soft-stale cache | Medium | No synthesis Human Gold; `HUMAN_LABEL_REQUIRED` |
| `daily_brief` | D. Synthesis | `daily_brief.py:278` | 3.1 Flash Lite / unspecified | select investment-relevant daily items | bounded input and normalized fallback behavior; no LLM judgment cache | High | No task-specific Human Gold; `HUMAN_LABEL_REQUIRED` |
| `daily_brief_implication` | D. Synthesis | `daily_brief.py:422` | 3.5 Flash Lite / unspecified | write implications for selected items | output normalization and per-item fallback | High | No task-specific Human Gold; `HUMAN_LABEL_REQUIRED` |
| `daily_brief_report` | D. Synthesis | `daily_brief.py:618` | 3.5 Flash Lite / unspecified | synthesize daily report framing | schema/length handling and deterministic assembly | High | No task-specific Human Gold; `HUMAN_LABEL_REQUIRED` |
| `trend_insights` | D. Synthesis | `trend_insights.py:139` | 3.5 Flash Lite / unspecified | explain keyword trends | parsing/validation; failure yields no generated insight | Low | No task-specific Human Gold; `HUMAN_LABEL_REQUIRED` |
| `weekly_bot` | D. Synthesis | `weekly_bot.py:673` | 3.5 Flash Lite / unspecified | weekly briefing synthesis | deterministic source/date selection and report validation | High | No task-specific Human Gold; `HUMAN_LABEL_REQUIRED` |
| `daily_lead` | D. Narrative | `daily_lead.py:271,353` | 3.5 Flash Lite / unspecified | one-sentence daily audio lead | vague/length checks, bounded repair, final omission on failure | Medium | No narrative Human Gold; `HUMAN_LABEL_REQUIRED` |
| `audio_brief` | D. Narrative | `audio_brief.py:537` | 3.5 Flash Lite / unspecified | Fast audio script | script structure/length/evidence audit, retry/model ladder; manifest reuse | High | No narrative Human Gold; `HUMAN_LABEL_REQUIRED` |
| `pubs_translate` | E. Extract | `pubs_translate.py:177` | 3.1 Flash Lite / unspecified | translate publication titles | parse validation; failure retains original title | Low | No translation Human Gold; `HUMAN_LABEL_REQUIRED` |
| `expert_dossiers` | E. Extract | `expert_audio_brief.py:1241` via helper at `:215` | 3.1 Flash Lite / unspecified | extract source-bound dossiers | structured dossier validation; Expert build fails locally if unusable | High | No extraction Human Gold; `HUMAN_LABEL_REQUIRED` |
| `expert_plan` | D. Synthesis | `expert_audio_brief.py:1252` via helper at `:215` | 3.5 Flash Lite / unspecified | plan Expert audio sections | allocation/coverage contracts | High | No planning Human Gold; `HUMAN_LABEL_REQUIRED` |
| `expert_script` | D. Narrative | `expert_audio_brief.py:1143` via helper at `:215` | 3.5 Flash Lite / unspecified | write Expert audio blocks | structure, duration, source and block contracts; retry variant maps here | Critical | No narrative Human Gold; `HUMAN_LABEL_REQUIRED` |
| `expert_repair` | D. Narrative | `expert_audio_brief.py:1321` via helper at `:215` | 3.5 Flash Lite / unspecified | targeted repair after semantic finding | deterministic re-audit and semantic re-verification | Critical | Depends on independent Semantic Gold plus repair-quality Gold; `HUMAN_LABEL_REQUIRED` |
| `expert_reorder` | D. Narrative | `expert_audio_brief.py:1347` via helper at `:215` | 3.5 Flash Lite / unspecified | repair section ordering | ordering/section contracts | High | No narrative Human Gold; `HUMAN_LABEL_REQUIRED` |
| `expert_intro_repair` | D. Narrative | `expert_audio_brief.py:1373` via helper at `:215` | 3.5 Flash Lite / unspecified | repair intro contract | intro and deterministic script contracts | High | No narrative Human Gold; `HUMAN_LABEL_REQUIRED` |
| `expert_verify` | C. Semantic | `expert_audio_brief.py:1315,1329` via helper at `:215` | 3.1 Flash Lite / unspecified, strict | final source-bound semantic verification | no model ladder/silent downgrade; failure stops that Expert audio | Critical | 5/77 Semantic Gold; `HUMAN_LABEL_REQUIRED` |
| `fast_verify` | C. Semantic | `semantic_verifier.py:79`, future gate in `audio_brief.py:623` | 3.1 Flash Lite / unspecified, strict | Fast script semantic verification | gate is disabled; if enabled, failure is local and explicit | Critical if activated | 5/77 Semantic Gold; `HUMAN_LABEL_REQUIRED` |
| `fast_semantic_repair` | D. Narrative | `audio_brief.py:282` | 3.5 Flash Lite / unspecified | repair a failed Fast semantic claim | deterministic evidence audit and semantic re-check | Critical if Fast gate activated | Insufficient Semantic/repair Gold; `HUMAN_LABEL_REQUIRED` |

### Generative call sites outside the central registry

The code-first inventory also found four active or reachable `call_json` paths that
predate `llm_policy`. They currently omit `thinking_level`, so their observed
production baseline is still unspecified, but they are not falsely represented as
centrally governed profiles:

| Call label | Call site | Effective model / thinking | Purpose and fallback | Gold / state |
| --- | --- | --- | --- | --- |
| `dedup` (legacy cluster path) | `dedup.py:143` | main 3.1 / unspecified | semantic grouping; failure keeps singleton groups | Identity Gold is relevant, but this batching contract needs replay; `IMPLEMENTED_BUT_NOT_ACTIVATED` |
| `scorer` | `scorer.py:90` | main 3.1 / unspecified | score research clusters; failure skips the LLM filter | no ranking Gold; `HUMAN_LABEL_REQUIRED` |
| `synthesize` | `synthesize.py:156` | synthesis 3.5 / unspecified | build research cards; failure returns no synthesized cards | no synthesis Gold; `HUMAN_LABEL_REQUIRED` |
| `synthesize` self-check | `synthesize.py:221` | main 3.1 / unspecified | identify unsupported card fields; verifier failure conservatively keeps fields | no independent semantic Gold for this contract; `HUMAN_LABEL_REQUIRED` |

No-op centralization of these paths was intentionally not mixed into this validation
branch: without task Gold it would not justify a reasoning activation, and changing
call routing is unnecessary for the current Human Gold/evaluation result. The gap is
recorded for a later behavior-neutral policy migration. The `google.genai` client in
`news_bot.py:881` is an embeddings path, not a generative reasoning profile, and is
outside this task by explicit scope.

## Gold separation and activation boundary

- Identity Gold is used only for event-relation decisions. It is not a truth set for
  Curation, Semantic verification, translation, planning, or narrative quality.
- Curation has 40 audited candidates but only one human answer. The remaining 39
  include event boundary, scope, stage, date, and causality dimensions.
- Semantic has 77 audited candidates but only five human answers. The remaining 72
  cover aligned claims, controlled perturbations, unsupported inference, six domains,
  and all ten error-focus categories.
- Narrative/synthesis/extract profiles have no adequate task-specific Human Gold or
  preference contract. Their evaluation infrastructure can be extended after a human
  rubric is defined; reasoning remains unspecified meanwhile.
- The current Identity evaluator is a task-level, single-pair contract. It is evidence
  for the 3.1 Identity class, but it is not by itself a replay of every batching/parser
  detail in each production call site. No profile is activated until the complete run
  and a profile-appropriate regression both support the decision.

## Cache and API-policy findings

- `generation_policy_fingerprint()` includes model, effective requested thinking,
  sampling mode, and prompt version. `issue_review`, `keei_match`, and
  `issue_insight` use it with soft-stale bounded refresh instead of deleting caches.
- Observed `thought_tokens = 0` for unspecified calls is telemetry, not an API rule
  equating unspecified with OFF.
- Explicit reasoning configuration errors are not silently downgraded by the
  evaluation harness. Strict verifier profiles use one atomic model/reasoning policy.
- Existing audio model ladders are production failure policy for non-verifier writing
  tasks; they are not used to disguise an evaluation config failure.
