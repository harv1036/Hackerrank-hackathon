#!/usr/bin/env python3
"""Hand-built evaluation cases (beyond the public samples).

Each case builds a tiny synthetic evidence set in memory, runs the real solver and compares the answer with
the outcome the policy requires.  Run: python3 code/evaluation/custom_cases.py [--markdown]
"""
import csv
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from audit.classify import classify          # noqa: E402
from audit.evidence import Obs                # noqa: E402
from audit.identity import Identity           # noqa: E402
from audit.solve import Solver                # noqa: E402

CASE = {'case_id': 'custom', 'email': 'ana@example.org', 'requested_at': '2026-01-10T09:00:00Z',
        'reported_status': 'COMPLETE', 'reported_at': '2026-01-11T09:00:00Z', 'audit_at': '2026-01-20T12:00:00Z',
        'request_text': 'Please delete my account.'}
MANIFEST = {f'ev{i}': {'captured_at': t} for i, t in
            [(1, '2026-01-15T10:00:00Z'), (2, '2026-01-19T10:00:00Z'), (3, '2026-01-25T10:00:00Z')]}


class Book:
    def __init__(self, ident):
        self.ident = ident

    def resolve(self, email, audit_at=None):
        return self.ident if email.lower() in {e.lower() for e in self.ident.emails} else None


def ident(*emails):
    i = Identity('user_900', name='Ana')
    i.emails.update(emails)
    return i


def O(fid, ftime, rec, rid, cat, state, keys=('ana@example.org',), store='service', **attrs):
    return Obs(fid, ftime, rec, 'x', rid, cat, state, frozenset(k.lower() for k in keys), store, attrs)


def run(obs, who=None, case=None):
    solver = Solver(obs, [], MANIFEST, Book(who or ident('ana@example.org')))
    status, steps, _ = solver.audit(case or CASE)
    return status, [(s['phase'], s['resource_id'], s['operation'], s['evidence_id']) for s in steps]


def case_historical_email():
    old, new = 'ana.old@example.org', 'ana@example.org'
    who = ident(old, new)
    obs = [O('ev1', '2026-01-15T10:00:00Z', '2026-01-15T10:00:00Z', 'search_11', 'SEARCH_INDEX_ENTRY', 'PRESENT', keys=(old,))]
    c = dict(CASE, email=old)
    status, steps = run(obs, who, c)
    ok = status == 'INCOMPLETE' and ('FINDING', 'search_11', 'REPORT_REMAINING_RECORD', 'ev1') in steps
    return 'Request e-mail is a historical address; the stale search entry still carries it', ok, status


def case_replica_drift():
    obs = [O('ev1', '2026-01-15T10:00:00Z', '2026-01-15T10:00:00Z', 'search_12', 'SEARCH_INDEX_ENTRY', 'ABSENT', store='sql:primary'),
           O('ev2', '2026-01-19T10:00:00Z', '2026-01-19T10:00:00Z', 'search_12', 'SEARCH_INDEX_ENTRY', 'PRESENT', store='sql:failover')]
    status, steps = run(obs)
    ok = status == 'INCOMPLETE' and ('FINDING', 'search_12', 'REPORT_REMAINING_RECORD', 'ev2') in steps
    return 'Primary says gone, failover replica still holds the entry (replication drift)', ok, status


def case_late_evidence_ignored():
    obs = [O('ev1', '2026-01-15T10:00:00Z', '2026-01-15T10:00:00Z', 'search_13', 'SEARCH_INDEX_ENTRY', 'ABSENT'),
           O('ev3', '2026-01-25T10:00:00Z', '2026-01-25T10:00:00Z', 'search_13', 'SEARCH_INDEX_ENTRY', 'PRESENT')]
    status, steps = run(obs)
    ok = status == 'COMPLETE' and steps == [('VERIFICATION', 'search_13', 'VERIFY_RECORD_ABSENT', 'ev1')]
    return 'A later export (after audit_at) re-lists the entry; it must not change the answer', ok, status


def case_retained_audit_log():
    obs = [O('ev2', '2026-01-19T10:00:00Z', '2026-01-19T10:00:00Z', 'audit_record_5', 'SECURITY_AUDIT_LOG', 'FOUND'),
           O('ev2', '2026-01-19T10:00:00Z', '2026-01-19T10:00:00Z', 'search_14', 'SEARCH_INDEX_ENTRY', 'ABSENT')]
    status, steps = run(obs)
    ok = status == 'COMPLETE' and ('VERIFICATION', 'audit_record_5', 'VERIFY_RECORD_PRESENT', 'ev2') in steps \
        and not any(op == 'DELETE_RECORD' for _, _, op, _ in steps)
    return 'Security audit record inside its retention period is verified present, never deleted', ok, status


def case_tombstone_redaction():
    obs = [O('ev2', '2026-01-19T10:00:00Z', '2026-01-19T10:00:00Z', 'tombstone_7', 'ACCOUNT_TOMBSTONE', 'IDENTIFIABLE')]
    status, steps = run(obs)
    ok = status == 'INCOMPLETE' and ('REMEDIATION', 'tombstone_7', 'REDACT_RECORD', '') in steps \
        and ('VERIFICATION', 'tombstone_7', 'VERIFY_RECORD_REDACTED', '') in steps
    return 'Tombstone still exposes the e-mail: redact (keep the stable reference), do not delete', ok, status


def case_remediation_order():
    obs = [O('ev2', '2026-01-19T10:00:00Z', '2026-01-19T10:00:00Z', 'search_15', 'SEARCH_INDEX_ENTRY', 'PRESENT'),
           O('ev2', '2026-01-19T10:00:00Z', '2026-01-19T10:00:00Z', 'backup_16', 'BACKUP_ARCHIVE', 'ACTIVE'),
           O('ev2', '2026-01-19T10:00:00Z', '2026-01-19T10:00:00Z', 'data_flow_17', 'DATA_FLOW', 'ACTIVE')]
    status, steps = run(obs)
    rem = [op for ph, _, op, _ in steps if ph == 'REMEDIATION']
    return 'Recreation paths are shut before records are deleted', rem == ['DISABLE_DATA_FLOW', 'BLOCK_RESTORE', 'DELETE_RECORD'], rem


def case_non_deletion_request():
    texts = ['Please send me a copy of my data. Do not delete anything.', 'Peux-tu supprimer mon compte définitivement ?']
    got = [classify(t)[0] for t in texts]
    return 'Access request is NOT_APPLICABLE, a French deletion request is not', got == ['NOT_DELETE', 'DELETE'], got


def case_ambiguous_email_history():
    from audit.identity import IdentityBook
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, 'user_identities.csv'), 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['user_id', 'email', 'phone', 'email_hash', 'name'])
            w.writerow(['user_901', 'one@example.org', '', '', 'One'])
            w.writerow(['user_902', 'two@example.org', '', '', 'Two'])
        with open(os.path.join(d, 'user_audit_log.csv'), 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['user_id', 'event_time', 'field_name', 'old_value', 'new_value'])
            w.writerow(['user_901', '2026-01-05T00:00:00Z', 'email', 'shared@example.org', 'one@example.org'])
            w.writerow(['user_902', '2026-01-05T00:00:00Z', 'email', 'shared@example.org', 'two@example.org'])
        got = IdentityBook(d).resolve('shared@example.org', '2026-01-20T00:00:00Z', '2026-01-20T00:00:00Z')
    ok = got is None
    return 'An email shared by two users must not be assigned by sorted user_id', ok, got.user_id if got else None


CASES = [case_historical_email, case_replica_drift, case_late_evidence_ignored, case_retained_audit_log,
         case_tombstone_redaction, case_remediation_order, case_non_deletion_request, case_ambiguous_email_history]


def main():
    results = []
    for i, fn in enumerate(CASES, 1):
        desc, ok, got = fn()
        results.append((i, desc, ok, got))
        print(f"custom_{i}: {'PASS' if ok else 'FAIL'} - {desc} -> {got}")
    if '--markdown' in sys.argv:
        print('\n| # | Scenario | Result |\n|---|---|---|')
        for i, d, ok, got in results:
            print(f"| custom_{i} | {d} | {'pass' if ok else 'FAIL'} |")
    sys.exit(0 if all(r[2] for r in results) else 1)


if __name__ == '__main__':
    main()
