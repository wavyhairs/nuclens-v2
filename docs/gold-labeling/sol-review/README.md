# GPT-5.6 Sol High provisional review handoff

These packages are inputs for an **independent GPT-5.6 Sol High** judgment pass.
The repository has no checked-in direct Sol API/CLI integration; the current pass
was run through an exact-model orchestration agent, not a substitute model. The
packages remain reusable for later reruns. Sol output is AI-assisted provisional
metadata, never Human Gold.

## Judge instruction

Give the model one task input JSONL and its matching output JSON Schema. Judge each
row independently from the supplied source/evidence. Do not use Nuclens Gemini cache
or any prior Gemini verdict. Return UTF-8 JSONL with exactly one schema-valid object
per input `case_id`; do not add Markdown fences or commentary.

For Curation, check event combination/splitting, chronology, reactor unit, stage,
attribution, unsupported causality, and stronger-than-source wording. For Semantic,
verify relations—not merely individual facts—including causality, chronology,
same-event/unit/decision identity, attribution, and unsupported event linkage.

Files:

- `curation-input.jsonl` → `curation-output.schema.json`
- `semantic-input.jsonl` → `semantic-output.schema.json`

Import completed Sol JSONL without touching canonical fixtures:

```powershell
python tools/sol_provisional_gold.py --task curation --import-provisional path\to\curation-sol-output.jsonl
python tools/sol_provisional_gold.py --task semantic --import-provisional path\to\semantic-sol-output.jsonl
```

The resulting ignored files are:

- `.eval/gold-labels/curation.sol_provisional.json`
- `.eval/gold-labels/semantic.sol_provisional.json`

Every imported row is forced to `human_reviewed: false`, assigned a review priority,
and remains outside canonical Gold until a person approves or changes it in the
loopback review UI. Ambiguous, low-confidence, causal, chronological, event-link,
and event-boundary cases are shown first.

```powershell
python tools/review_gold_labeler.py --task curation
python tools/review_gold_labeler.py --task semantic
```

Use A to approve the displayed provisional judgment, C to edit the final canonical
contract, and U to leave it unapproved for deeper review. Arrow keys navigate and
Undo reverses the last review action. Approval is a human action: only then is the
Human sidecar row marked `human_reviewed: true`. `Export / Validate` copies only those
human-reviewed rows into the canonical fixture. The first recommended subset is
computed after import (base 24 Curation / 36 Semantic, expanded to cover all hard
priority and observed label/error/confidence classes).
