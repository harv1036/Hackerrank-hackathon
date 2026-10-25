#!/usr/bin/env python3
"""Ghost Data Still Exists - deletion audit agent entry point.

Usage:  python3 code/main.py [--requests PATH] [--out PATH] [--dataset DIR]
Reads dataset/deletion_requests.csv and writes output.csv at the repository root.
"""
import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from audit.evidence import load_all          # noqa: E402
from audit.identity import IdentityBook      # noqa: E402
from audit.solve import Solver               # noqa: E402

from audit.attribute import Attributor       # noqa: E402


def run(dataset, requests_path, out_path, context_requests=None, llm=None):
    obs, scopes, manifest = load_all(dataset)
    book = IdentityBook(dataset)
    solver = Solver(obs, scopes, manifest, book)
    cases = list(csv.DictReader(open(requests_path, encoding='utf-8')))
    idents, claimed = {}, {}
    pool = list(cases) + [c for c in (context_requests or []) if c['case_id'] not in {k['case_id'] for k in cases}]
    for case in pool:
        ident, res = solver.direct_links(case)
        if ident is not None:
            idents[case['case_id']] = ident
            claimed[case['case_id']] = set(res)
    prelim = {c['case_id'] for c in pool if c['case_id'] in idents and solver.audit(c)[0] == 'COMPLETE'}
    extra = Attributor(dataset, solver).attribute(pool, idents, claimed, complete_cases=prelim)
    rows = []
    for case in cases:
        status, steps, _ = solver.audit(case, extra.get(case['case_id'], ()))
        rows.append((case['case_id'], status, json.dumps(steps, separators=(',', ':'), ensure_ascii=False)))
    rows.sort(key=lambda r: r[0])
    with open(out_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['case_id', 'deletion_status', 'steps_json'])
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.join(ROOT, 'dataset'))
    ap.add_argument('--requests', default=None)
    ap.add_argument('--out', default=os.path.join(ROOT, 'output.csv'))
    a = ap.parse_args()
    req = a.requests or os.path.join(a.dataset, 'deletion_requests.csv')
    rows = run(a.dataset, req, a.out)
    print(f'wrote {len(rows)} rows to {a.out}')


if __name__ == '__main__':
    main()
