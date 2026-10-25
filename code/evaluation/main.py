#!/usr/bin/env python3
"""Score predictions against a gold CSV (default: dataset/sample_output.csv).

Metrics
  status accuracy   : deletion_status equals gold
  step precision/recall/F1 : a step counts when all of (phase, system_id, resource_id, operation, state, evidence_id) match
  core step F1      : same, ignoring evidence_id (isolates "found the right resources and operations")
  field accuracy    : over gold steps whose (phase, resource_id, operation) exists in the prediction, share of fields equal
  sequence accuracy : share of cases whose ordered (phase, system, resource, operation, state) list equals gold exactly
  exact case        : sequence matches AND every evidence_id matches
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

FIELDS = ['phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id']


def load(path):
    return {r['case_id']: r for r in csv.DictReader(open(path, encoding='utf-8'))}


def tup(s, fields):
    return tuple(s.get(f, '') for f in fields)


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def score(pred, gold, verbose=False):
    n = len(gold)
    st_ok = seq_ok = exact_ok = 0
    tp = fp = fn = 0
    ctp = cfp = cfn = 0
    field_hit = field_tot = 0
    per_field = {f: [0, 0] for f in FIELDS}
    diffs = []
    for cid, g in sorted(gold.items()):
        p = pred.get(cid)
        gs = json.loads(g['steps_json'])
        ps = json.loads(p['steps_json']) if p else []
        if p and p['deletion_status'] == g['deletion_status']:
            st_ok += 1
        gset = {tup(s, FIELDS) for s in gs}
        pset = {tup(s, FIELDS) for s in ps}
        tp += len(gset & pset); fp += len(pset - gset); fn += len(gset - pset)
        gc = {tup(s, FIELDS[:5]) for s in gs}
        pc = {tup(s, FIELDS[:5]) for s in ps}
        ctp += len(gc & pc); cfp += len(pc - gc); cfn += len(gc - pc)
        pidx = {(s['phase'], s['resource_id'], s['operation']): s for s in ps}
        for s in gs:
            m = pidx.get((s['phase'], s['resource_id'], s['operation']))
            if m:
                for f in FIELDS:
                    per_field[f][1] += 1
                    per_field[f][0] += int(m.get(f) == s.get(f))
                    field_tot += 1
                    field_hit += int(m.get(f) == s.get(f))
        gseq = [tup(s, FIELDS[:5]) for s in gs]
        pseq = [tup(s, FIELDS[:5]) for s in ps]
        if gseq == pseq and p and p['deletion_status'] == g['deletion_status']:
            seq_ok += 1
            if [s.get('evidence_id') for s in gs] == [s.get('evidence_id') for s in ps]:
                exact_ok += 1
        if gset != pset or (p and p['deletion_status'] != g['deletion_status']):
            diffs.append((cid, g['deletion_status'], p['deletion_status'] if p else None, sorted(gset - pset), sorted(pset - gset)))
    sp, sr, sf = prf(tp, fp, fn)
    cp, cr, cf = prf(ctp, cfp, cfn)
    res = {
        'cases': n,
        'status_accuracy': st_ok / n,
        'step_precision': sp, 'step_recall': sr, 'step_f1': sf,
        'core_step_precision': cp, 'core_step_recall': cr, 'core_step_f1': cf,
        'field_accuracy': field_hit / field_tot if field_tot else 0.0,
        'per_field_accuracy': {f: (a / b if b else 0.0) for f, (a, b) in per_field.items()},
        'sequence_accuracy': seq_ok / n,
        'exact_case_accuracy': exact_ok / n,
    }
    return res, diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pred', default=None, help='predictions csv; if omitted the solver is run on the sample requests')
    ap.add_argument('--gold', default=os.path.join(ROOT, 'dataset', 'sample_output.csv'))
    ap.add_argument('--requests', default=os.path.join(ROOT, 'dataset', 'sample_deletion_requests.csv'))
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    pred_path = a.pred
    if not pred_path:
        from main import run
        pred_path = os.path.join('/tmp', 'sample_pred.csv') if os.access('/tmp', os.W_OK) else 'sample_pred.csv'
        ctx = list(csv.DictReader(open(os.path.join(ROOT, 'dataset', 'deletion_requests.csv'), encoding='utf-8')))
        run(os.path.join(ROOT, 'dataset'), a.requests, pred_path, context_requests=ctx)
    res, diffs = score(load(pred_path), load(a.gold))
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for k, v in res.items():
            if k == 'per_field_accuracy':
                print(k, {f: round(x, 3) for f, x in v.items()})
            elif isinstance(v, float):
                print(f'{k:24s} {v:.3f}')
            else:
                print(f'{k:24s} {v}')
    if a.verbose:
        for cid, gst, pst, miss, extra in diffs:
            print(f'\n{cid} gold={gst} pred={pst}')
            for m in miss:
                print('   MISSING', m)
            for e in extra:
                print('   EXTRA  ', e)


if __name__ == '__main__':
    main()
