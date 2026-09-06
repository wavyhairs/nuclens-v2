# Identity Gold local labeler

This is a development-only, localhost-only tool. It does not import Gemini,
perform external fetches, enter the production web bundle, or run an evaluation.

## Start

From the repository root on Windows:

```powershell
python tools/gold_labeler.py
```

The server binds only to `127.0.0.1` and opens:

```text
http://127.0.0.1:8765
```

Use `--no-browser` if the browser should not open automatically, or `--port 0`
to let the OS choose a free loopback port.

## Label

- `M`, `S`, `A`: choose MERGE, SEPARATE, or AMBIGUOUS.
- `1`–`9`: choose a reason. A normal reason click saves immediately.
- `Enter`: save the current selection and move next.
- `←`, `→`: previous/next pair.
- Auto-next is on by default and can be disabled.
- The default filter is First 60. All 150 and Unlabeled only are also available.

Every mutation is flushed to a sibling temporary file and atomically replaced at:

```text
.eval/gold-labels/identity_labels.json
```

That ignored sidecar supports restart/resume without changing the tracked candidate
fixture. The next launch begins at the first unlabeled pair in the first 60.

Cached/model verdicts are absent from the initial browser payload. They are fetched
only after clicking `참고정보 보기`.

## Validate and export

Validate the current sidecar and canonical fixtures without exporting:

```powershell
python tools/gold_labeler.py --validate
```

Explicitly apply sidecar labels to the canonical Identity fixture, then run the same
Gold audit used by `tools/validate_reasoning_gold.py`:

```powershell
python tools/gold_labeler.py --export
```

The browser's `Export / Validate` button performs the same explicit operation.
Export sets `human_label`, `reason_code`, `human_notes`, and
`label_status: HUMAN_LABELLED`; unlabeled cases stay `HUMAN_LABEL_REQUIRED`.

After the first 60 are exported and validation passes, plan the evaluation with zero
calls:

```powershell
$env:GEMINI_API_KEY=''
python tools/llm_eval.py --task IDENTITY_REVIEW --fixtures tests/fixtures/gemini_reasoning/identity_candidates.json --config unspecified --config level:medium --config level:high --repeat 3 --out .eval/identity --max-new-calls 0
```

Live evaluation remains a separate, explicit action.
