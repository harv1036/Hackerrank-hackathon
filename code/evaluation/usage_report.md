# Usage report

Final full-dataset run: `python3 code/main.py` → `output.csv` (518 requests).

| Provider | Model | Calls | Input tokens | Output tokens | Est. cost (USD) |
|---|---|---|---|---|---|
| - | none | 0 | 0 | 0 | 0.00 |
| **Total** | | 0 | 0 | 0 | 0.00 |

* Total tokens: 0; average per request: 0.00; estimated cost per request: 0.0000 USD.
* Run mode: Gemini free tier.

## What a model is used for

The investigation itself (identity resolution, evidence parsing, state reconstruction, policy application, step
ordering) is deterministic code so the answer is reproducible. Two optional model hooks exist, both on Google's
Gemini free tier and both off unless `GEMINI_API_KEY` is set:

1. A small Gemini model classifies a request as DELETE / NOT_DELETE, only when the rule classifier is unsure. In
   this dataset the rules were confident for all 518 requests, so no call was needed.
2. A Gemini vision model reads screenshots and PDFs (`python3 code/extract.py`, 15 files). It can replace the
   shipped hand-checked `extracted_facts.json`.

The final run that produced `output.csv` therefore made **0 model calls**: the rule classifier decided every
request and the shipped, hand-checked extraction file supplied the PDF/screenshot facts.

## Live Gemini extraction trial (separate from the final run)

On 2026-10-25 `code/extract.py` was run live. `gemini-3.5-flash` was refused with HTTP 429 (free-tier quota) and
`gemini-2.5-flash` returned 404, so `gemini-3.1-flash-lite` was used.

| Provider | Model | Calls | Input tokens | Output tokens | Billed cost (USD) |
|---|---|---|---|---|---|
| Google | gemini-3.1-flash-lite | 15 | 19238 | 1402 | 0.00 (free tier) |

* Average per file: 1283 input and 93 output tokens. Cost is 0 on the free tier; no paid price list is assumed.
* Its output scored worse than the shipped facts on the public samples (see evaluation_report.md section 5), so it
  was **not** used for `output.csv`. The trial's raw numbers are in `code/evaluation/gemini_trial_usage.json`.
* `GEMINI_CLASSIFY_MODEL` and `GEMINI_VISION_MODEL` select the models; both default to `gemini-3.1-flash-lite`.
