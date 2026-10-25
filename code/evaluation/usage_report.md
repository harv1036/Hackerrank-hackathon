# Usage report

Final full-dataset run: `python3 code/main.py` → `output.csv` (518 requests).

| Provider | Model | Calls | Input tokens | Output tokens | Est. cost (USD) |
|---|---|---|---|---|---|
| - | none | 0 | 0 | 0 | 0.00 |
| **Total** | | 0 | 0 | 0 | 0.00 |

* Total tokens: 0; average per request: 0.00; estimated cost per request: 0.0000 USD.
* Run mode: GEMINI_API_KEY not set: deterministic rules only.

## What a model is used for

The investigation itself (identity resolution, evidence parsing, state reconstruction, policy application, step
ordering) is deterministic code so the answer is reproducible. Two optional model hooks exist, both on Google's
Gemini free tier and both off unless `GEMINI_API_KEY` is set:

1. `gemini-2.5-flash-lite` classifies a request as DELETE / NOT_DELETE, only when the rule classifier is unsure
   (in this dataset exactly 1 of 550 request texts; at most ~150 input tokens and 2 output tokens per call, so
   the whole dataset costs well under 1,000 tokens).
2. `gemini-2.5-flash` (vision) reads screenshots and PDFs (`python3 code/extract.py`, 15 files, roughly 1,500
   input tokens per image page), replacing the shipped hand-checked `extracted_facts.json`.

The workspace used to build this solution could not reach `generativelanguage.googleapis.com`, so the recorded
final run made **0 model calls**: the rule classifier decided every request and the shipped extraction file
supplied the PDF/screenshot facts. The hooks, prompts (`code/prompts/`), caching and token accounting are in place;
re-running with a key records real counts in `evaluation/usage_run.json` and regenerates this file with
`python3 code/evaluation/report.py`. Prices listed are Gemini paid-tier list prices used only for the estimate;
on the free tier the cost is 0.
