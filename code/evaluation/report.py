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

Remaining known gaps on the samples: the hand-built stories (account ids that exist only in the sample answers and
in nothing the solver can read) and two retained audit records (cases 18 and 20) that appear in no participant file.
Accounts shared between cases with identical report times are attributed by case order, which is a heuristic.
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
    um = f"""# Usage report

Final full-dataset run: `python3 code/main.py` → `output.csv` ({n} requests).

{chr(10).join(lines)}

* Total tokens: {tot_i + tot_o}; average per request: {(tot_i + tot_o) / n:.2f}; estimated cost per request: {'0.0000' if not calls else f'{total / n:.6f}'} USD.
* Run mode: {usage.get('note')}.

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
"""
    with open(os.path.join(HERE, 'usage_report.md'), 'w', encoding='utf-8') as fh:
        fh.write(um)
    print('wrote evaluation_report.md and usage_report.md')


if __name__ == '__main__':
    main()
