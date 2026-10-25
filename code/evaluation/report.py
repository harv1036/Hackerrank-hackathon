#!/usr/bin/env python3
"""Regenerate evaluation/evaluation_report.md and evaluation/usage_report.md.

    python3 code/evaluation/report.py          # after: python3 code/main.py
"""
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import main as scorer                               # noqa: E402  (evaluation/main.py)
sys.modules.pop('main', None)
sys.path.insert(0, os.path.dirname(HERE))
import importlib.util                               # noqa: E402

spec = importlib.util.spec_from_file_location('solver_main', os.path.join(os.path.dirname(HERE), 'main.py'))
solver_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(solver_main)

DATASET = os.path.join(ROOT, 'dataset')


def pct(x):
    return f'{100 * x:.1f}%'


def metrics_table(res):
    rows = [('Case-level deletion-status accuracy', res['status_accuracy']),
            ('Step precision (all 6 fields)', res['step_precision']), ('Step recall', res['step_recall']), ('Step F1', res['step_f1']),
            ('Core step F1 (ignoring evidence_id)', res['core_step_f1']),
            ('Field-level accuracy (matched steps)', res['field_accuracy']),
            ('Sequence accuracy (exact ordered steps)', res['sequence_accuracy']),
            ('Exact-case accuracy (sequence + every evidence_id)', res['exact_case_accuracy'])]
    out = ['| Metric | Score |', '|---|---|'] + [f'| {k} | {pct(v)} |' for k, v in rows]
    pf = res['per_field_accuracy']
    out += ['', 'Per field: ' + ', '.join(f'`{k}` {pct(v)}' for k, v in pf.items())]
    return '\n'.join(out)


def main():
    req = os.path.join(DATASET, 'sample_deletion_requests.csv')
    pred = '/tmp/report_sample_pred.csv'
    ctx = list(csv.DictReader(open(os.path.join(DATASET, 'deletion_requests.csv'), encoding='utf-8')))
    solver_main.run(DATASET, req, pred, context_requests=ctx)
    gold = scorer.load(os.path.join(DATASET, 'sample_output.csv'))
    predm = scorer.load(pred)
    allres, diffs = scorer.score(predm, gold)
    hand = {k: v for k, v in gold.items() if int(k.split('_')[1]) <= 10 and k not in ('case_03', 'case_09')}
    gen = {k: v for k, v in gold.items() if k not in hand}
    hres, _ = scorer.score({k: predm[k] for k in hand}, hand)
    gres, gdiffs = scorer.score({k: predm[k] for k in gen}, gen)
    cc = subprocess.run([sys.executable, os.path.join(HERE, 'custom_cases.py'), '--markdown'], capture_output=True, text=True)
    table = cc.stdout.split('\n\n', 1)[1] if '\n\n' in cc.stdout else cc.stdout
    full = list(csv.DictReader(open(os.path.join(ROOT, 'output.csv'), encoding='utf-8')))
    dist = {}
    for r in full:
        dist[r['deletion_status']] = dist.get(r['deletion_status'], 0) + 1

    md = f"""# Evaluation report

Scored with `python3 code/evaluation/main.py` (scorer) on the 32 public samples (`dataset/sample_output.csv`).
The samples were used while developing the solver, so these numbers are *development* scores, not a held-out
estimate. The 518 evaluation cases have no public answers.

## 1. Results on the 32 public samples

{metrics_table(allres)}

The samples come from two generators. Cases 03, 09 and 11-32 ({len(gen)} cases) use random resource ids and are
the same kind as the 518 evaluation cases; cases 01-02, 04-08 and 10 ({len(hand)} cases) are hand-built stories
whose account ids (`account_001`, ...) never occur in the evaluation requests.

| Subset | Cases | Status acc. | Step F1 | Sequence acc. |
|---|---|---|---|---|
| Evaluation-like (random ids) | {len(gen)} | {pct(gres['status_accuracy'])} | {pct(gres['step_f1'])} | {pct(gres['sequence_accuracy'])} |
| Hand-built stories | {len(hand)} | {pct(hres['status_accuracy'])} | {pct(hres['step_f1'])} | {pct(hres['sequence_accuracy'])} |

## 2. Final run on the 518 requests

`output.csv`: {len(full)} rows, status split {', '.join(f'{k} {v}' for k, v in sorted(dist.items()))}.
Format checks (518 unique sorted case ids, valid JSON arrays, consecutive `step_no`, phase order
FINDING→REMEDIATION→VERIFICATION, empty `evidence_id` on remediation and on every verification of an INCOMPLETE
case, `[]` for NOT_APPLICABLE) all pass. One deliberate exception: in 2 COMPLETE cases a retained audit record appears
only in a bundle exported after `audit_at`, which may not be cited, so its verification step carries an empty `evidence_id`.

## 3. Additional evaluation cases (created for this project)

`python3 code/evaluation/custom_cases.py` builds tiny synthetic evidence sets and runs the real solver.

{table}

## 4. Failures found and improvements made

| Failure seen on the samples | Cause | Fix |
|---|---|---|
| Resources missing for log evidence | A request/response pair spans two log lines (trace id) in six different syntaxes | Trace-aware parser, one observation per completed call |
| Case 26 wrongly INCOMPLETE | A stale bundle said PRESENT, a later export returned 404 in another store | One shared `service` store per system, so the latest readback supersedes; SQL replicas stay separate |
| False finding from a March log | Evidence captured before the request describes an earlier life of the account | Ignore observations captured before `requested_at` |
| Every citation pointed to a bundle exported after `audit_at` | Latest-file rule ignored the capture time | Only files captured and observations recorded at or before `audit_at` can decide or be cited |
| Deleted accounts / anonymised profiles not found | They carry no identifier | Attribute them from the deletion worker's activity rows: fixed stage offsets before `reported_at`, result class follows the reported status, cross-case elimination, and case-order pairing when several cases share one report time |
| Verification of INCOMPLETE cases cited evidence | Proposed future checks are not backed by evidence | Empty `evidence_id` for all steps after the findings |
| Odd `system_id` values (`backup_restore_service`...) | Snapshot `data_store` names the container, not the resource | Owning system derived from the resource-id prefix |
| Case 07 missed (attachment only in a PDF) | PDFs and screenshots are not text | Facts of the 11 screenshots and 4 PDFs extracted once (`code/audit/data/extracted_facts.json`) and linked through the data-catalog `LINK_RESOURCE_IDENTITY` rows |
| Non-deletion requests | `request_text` can be an access, correction or marketing request | Multilingual rule classifier (+ optional Gemini fallback) → NOT_APPLICABLE with `[]` |
| Sample case 23 attributed the wrong account | A failed deletion-worker account event sitting next to an opposite-result event belongs to a neighbouring workflow, but case order paired it anyway | Such an event (within 3 s of an opposite-result account event) is no longer a candidate; sample step F1 0.968 → 0.973, sequence accuracy 0.688 → 0.719, 2 of 518 output rows changed |
| One historical e-mail shared by two users resolved to the lowest `user_id` | Sorted-id tie-break is arbitrary | Pick the user whose e-mail history most recently owned the address at `requested_at`; if tied, resolve no identity (custom_8) |

Remaining known gaps on the samples: the hand-built stories (account ids that exist only in the sample answers and
in nothing the solver can read) and two retained audit records (cases 18 and 20) that appear in no participant file.
Accounts shared between cases with identical report times are attributed by case order, which is a heuristic.

## 5. Live Gemini vision extraction trial (not used for `output.csv`)

`code/extract.py` was run live with `gemini-3.1-flash-lite` on the 11 screenshots and 4 PDFs (15 calls). The model's
readings are kept in `code/evaluation/gemini_extracted_facts_trial.json`; the shipped, hand-checked
`code/audit/data/extracted_facts.json` stays in use. Reasons:

| Check | Shipped (hand-checked) facts | Gemini facts |
|---|---|---|
| 32 public samples: step F1 | 0.973 | 0.942 |
| 32 public samples: exact-case accuracy | 0.719 | 0.562 |
| Rows of the 518-row `output.csv` that change | - | 9 (6 flip INCOMPLETE to COMPLETE) |

What went wrong in the model's output: resource ids misread or replaced by other identifiers on 5 files (for example
an account id read as a `user_` id, an export id read as a reference number, a PDF file name used as the object id);
state and category labels that do not match the dataset's vocabulary (`IDENTIFIABLE`, `ACTIVE` instead of `PRESENT`,
`REDACTED`); and several capture or observation times shifted. A wrong state turned five remaining records into
"nothing remains", which would wrongly mark those cases COMPLETE. Conclusion: a small vision model is good at reading
text but not at mapping it onto the dataset's resource-id and state conventions without a stricter prompt and
validation, so extraction stays a one-off, human-checked step. Next improvement: validate each extracted id against the
`PREFIX_SYSTEM` prefixes and reject answers that do not match.
"""
    with open(os.path.join(HERE, 'evaluation_report.md'), 'w', encoding='utf-8') as fh:
        fh.write(md)

    usage = json.load(open(os.path.join(HERE, 'usage_run.json')))
    calls = usage.get('model_calls', {})
    tot_c = sum(v['calls'] for v in calls.values())
    tot_i = sum(v['input_tokens'] for v in calls.values())
    tot_o = sum(v['output_tokens'] for v in calls.values())
    n = len(full)
    lines = ['| Provider | Model | Calls | Input tokens | Output tokens | Est. cost (USD) |', '|---|---|---|---|---|---|']
    if calls:
        prices = {'gemini-2.5-flash-lite': (0.10, 0.40), 'gemini-2.5-flash': (0.30, 2.50)}
        total = 0.0
        for m, v in calls.items():
            pi, po = prices.get(m, (0, 0))
            cost = v['input_tokens'] / 1e6 * pi + v['output_tokens'] / 1e6 * po
            total += cost
            lines.append(f"| Google | {m} | {v['calls']} | {v['input_tokens']} | {v['output_tokens']} | {cost:.4f} (free tier: 0) |")
        lines.append(f'| **Total** | | {tot_c} | {tot_i} | {tot_o} | {total:.4f} |')
    else:
        lines.append('| - | none | 0 | 0 | 0 | 0.00 |')
        lines.append('| **Total** | | 0 | 0 | 0 | 0.00 |')
    trial = json.load(open(os.path.join(HERE, 'gemini_trial_usage.json')))
    tc, ti, to = trial['calls'], trial['input_tokens'], trial['output_tokens']
    ai, ao = round(ti / tc), round(to / tc)
    um = f"""# Usage report

Final full-dataset run: `python3 code/main.py` → `output.csv` ({n} requests).

{chr(10).join(lines)}

* Total tokens: {tot_i + tot_o}; average per request: {(tot_i + tot_o) / n:.2f}; estimated cost per request: {'0.0000' if not calls else f'{total / n:.6f}'} USD.
* Run mode: {usage.get('note')}.

## What a model is used for

The investigation itself (identity resolution, evidence parsing, state reconstruction, policy application, step
ordering) is deterministic code so the answer is reproducible. Two optional model hooks exist, both on Google's
Gemini free tier and both off unless `GEMINI_API_KEY` is set:

1. A small Gemini model classifies a request as DELETE / NOT_DELETE, only when the rule classifier is unsure. In
   this dataset the rules were confident for all {n} requests, so no call was needed.
2. A Gemini vision model reads screenshots and PDFs (`python3 code/extract.py`, 15 files). It can replace the
   shipped hand-checked `extracted_facts.json`.

The final run that produced `output.csv` therefore made **0 model calls**: the rule classifier decided every
request and the shipped, hand-checked extraction file supplied the PDF/screenshot facts.

## Live Gemini extraction trial (separate from the final run)

On 2026-10-25 `code/extract.py` was run live. `gemini-3.5-flash` was refused with HTTP 429 (free-tier quota) and
`gemini-2.5-flash` returned 404, so `gemini-3.1-flash-lite` was used.

| Provider | Model | Calls | Input tokens | Output tokens | Billed cost (USD) |
|---|---|---|---|---|---|
| Google | gemini-3.1-flash-lite | {tc} | {ti} | {to} | 0.00 (free tier) |

* Average per file: {ai} input and {ao} output tokens. Cost is 0 on the free tier; no paid price list is assumed.
* Its output scored worse than the shipped facts on the public samples (see evaluation_report.md section 5), so it
  was **not** used for `output.csv`. The trial's raw numbers are in `code/evaluation/gemini_trial_usage.json`.
* `GEMINI_CLASSIFY_MODEL` and `GEMINI_VISION_MODEL` select the models; both default to `gemini-3.1-flash-lite`.
"""
    with open(os.path.join(HERE, 'usage_report.md'), 'w', encoding='utf-8') as fh:
        fh.write(um)
    print('wrote evaluation_report.md and usage_report.md')


if __name__ == '__main__':
    main()
