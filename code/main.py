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
EXTRACTED = os.path.join(HERE, 'audit', 'data', 'extracted_facts.json')

from audit.evidence import load_all          # noqa: E402
from audit.identity import IdentityBook      # noqa: E402
from audit.solve import Solver               # noqa: E402
from audit.attribute import Attributor       # noqa: E402
from audit.classify import classify          # noqa: E402
from audit.llm import GeminiClient           # noqa: E402


CATALOG_LINK = re.compile(r'belongs only to (\S+@\S+) and (user_\d+)|to (\S+@\S+) and (user_\d+)')


def catalog_links(dataset):
    """rid -> identifiers from the data catalog's LINK_RESOURCE_IDENTITY activity rows."""
    links = {}
    with open(os.path.join(dataset, 'system_activity.csv'), encoding='utf-8') as fh:
        for a in csv.DictReader(fh):
            if a['event_type'] != 'LINK_RESOURCE_IDENTITY':
                continue
            m = CATALOG_LINK.search(a['details'])
            if m:
                g = [x for x in m.groups() if x]
                links[a['resource_id']] = {x.lower() for x in g}
    return links


def run(dataset, requests_path, out_path, context_requests=None, llm=None):
    with open(EXTRACTED, encoding='utf-8') as fh:
        extracted = json.load(fh)
    obs, scopes, manifest = load_all(dataset, extracted)
    book = IdentityBook(dataset)
    solver = Solver(obs, scopes, manifest, book, catalog_links(dataset))
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
        if classify(case['request_text'], llm)[0] == 'NOT_DELETE':
            rows.append((case['case_id'], 'NOT_APPLICABLE', '[]'))
            continue
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
    llm = GeminiClient.from_env()
    rows = run(a.dataset, req, a.out, llm=llm)
    print(f'wrote {len(rows)} rows to {a.out}')
    usage = {'model_calls': {}, 'note': 'GEMINI_API_KEY not set: deterministic rules only' if llm is None else 'Gemini free tier'}
    if llm is not None:
        llm.save()
        usage['model_calls'] = llm.usage
    with open(os.path.join(HERE, 'evaluation', 'usage_run.json'), 'w', encoding='utf-8') as fh:
        json.dump(usage, fh, indent=1)


if __name__ == '__main__':
    main()
