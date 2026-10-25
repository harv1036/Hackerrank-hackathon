"""Attribute keyless resources to cases from the global activity stream.

Some resources (a deleted account, an already anonymised analytics profile) carry no identifier that ties
them to a person.  The only link is the deletion worker's activity event: it fires at a fixed stage offset
before the case's ``reported_at`` and its result follows the workflow's reported status.  Candidates from
that window are narrowed by (1) removing resources claimed by identifiers of other users, (2) preferring
resources whose evidence falls inside the case's audit window, and (3) cross-case elimination.
"""
import csv
import os
from collections import defaultdict
from datetime import datetime, timedelta

READBACK_HOURS = 3.2
RESULT_OF_STATUS = {'COMPLETE': 'SUCCESS', 'NOT_STARTED': 'FAILED', 'PARTIALLY_COMPLETE': 'PARTIAL'}

# (resource prefix, event type) -> (earliest, latest) minutes relative to reported_at for a SUCCESS event
STAGE_WINDOWS = {
    ('account', 'DELETE_RECORD'): (-76.0, -43.0),
    ('analytics', 'ANONYMIZE_RECORD'): (-31.0, -26.0),
}
# the account delete mirrors the workflow's claim; the later policy actions always succeed when they fall in their stage window
RESULT_FOLLOWS_STATUS = {('account', 'DELETE_RECORD')}


def parse(ts):
    return datetime.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S')


def prefix(rid):
    return rid.rsplit('_', 1)[0]


class Attributor:
    def __init__(self, dataset_dir, solver):
        self.solver = solver
        self.events = defaultdict(list)
        with open(os.path.join(dataset_dir, 'system_activity.csv'), encoding='utf-8') as fh:
            for a in csv.DictReader(fh):
                self.events[a['resource_id']].append(a)

    def event_time(self, rid, etype):
        ts = [a['event_time'] for a in self.events.get(rid, []) if a['event_type'] == etype and a['source_system'] == 'deletion_worker']
        return min(ts) if ts else ''

    def _candidates(self, case, pre, etype, keyset, claimed):
        rep = parse(case['reported_at'])
        lo, hi = STAGE_WINDOWS[(pre, etype)]
        want = RESULT_OF_STATUS.get(case['reported_status']) if (pre, etype) in RESULT_FOLLOWS_STATUS else 'SUCCESS'
        out = []
        for rid, evs in self.events.items():
            if prefix(rid) != pre or rid in claimed:
                continue
            for a in evs:
                if a['event_type'] != etype or a['source_system'] != 'deletion_worker':
                    continue
                off = (parse(a['event_time']) - rep) / timedelta(minutes=1)
                if lo <= off <= hi and a['reported_result'] == want:
                    out.append(rid)
                    break
        if pre == 'analytics':
            # an anonymised profile is only part of a case when evidence inside the audit window shows it
            # (a case's readbacks are taken in the hours just before its audit time)
            floor = (parse(case['audit_at']) - timedelta(hours=READBACK_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ')
            out = [r for r in out if any(case['requested_at'] <= o.file_time <= case['audit_at'] and floor <= o.rec_time <= case['audit_at']
                                         for o in self.solver.by_rid.get(r, []))]
        # drop resources whose evidence names somebody else
        res = []
        for rid in out:
            foreign = False
            for o in self.solver.by_rid.get(rid, []):
                if o.keys and not (set(o.keys) & keyset):
                    foreign = True
                    break
            if not foreign:
                res.append(rid)
        return res

    def attribute(self, cases, idents, claimed, complete_cases=None):
        """cases: list of case dicts; idents: case_id -> Identity; claimed: rids linked by identifiers."""
        assigned = defaultdict(set)
        for pre, etype in STAGE_WINDOWS:
            cand = {}
            for c in cases:
                ident = idents.get(c['case_id'])
                if ident is None:
                    continue
                have = any(r.startswith(pre + '_') for r in claimed.get(c['case_id'], ()))
                if have:
                    continue
                if pre == 'analytics' and complete_cases is not None and c['case_id'] not in complete_cases:
                    continue          # an already-anonymised profile only appears in cases whose deletion checks out
                cs = self._candidates(c, pre, etype, ident.keys, set().union(*claimed.values()) if claimed else set())
                cand[c['case_id']] = cs
            self._resolve(cand, assigned, ordered=(pre == 'analytics'), etime=lambda r, e=etype: self.event_time(r, e))
        return assigned

    def _prefer_in_window(self, case, rids):
        if len(rids) <= 1:
            return rids
        lo = case['requested_at']
        hi = case['audit_at']
        inwin = [r for r in rids if any(lo <= o.rec_time <= hi and o.file_time <= hi for o in self.solver.by_rid.get(r, []))]
        return inwin or rids

    @staticmethod
    def _resolve(cand, assigned, ordered=False, etime=None):
        """Assign each case at most one resource.

        1. a resource wanted by one case only goes to that case (and leaves everyone else's list);
        2. cases sharing a report time and an identical candidate set were processed in case order, so when there
           are exactly as many candidates as cases they pair off in event-time order;
        3. otherwise, for a mandatory resource (an account), guess among at most two candidates.
        An optional resource (ordered=True, analytics) is skipped when there are fewer candidates than cases.
        """
        cand = {k: list(v) for k, v in cand.items() if v}
        done = {}
        changed = True
        while changed:
            changed = False
            wanted = defaultdict(list)
            for cid, rs in cand.items():
                if cid not in done:
                    for r in rs:
                        wanted[r].append(cid)
            for r, cids in wanted.items():
                if len(cids) == 1 and cids[0] not in done:
                    cid = cids[0]
                    # the case may also want another resource that is wanted by nobody else: then it is not decided
                    if len([x for x in cand[cid] if x not in done.values()]) == 1 or not ordered:
                        done[cid] = r
                        for other, rs in cand.items():
                            if other != cid and r in rs:
                                rs.remove(r)
                        changed = True
                        break
        groups = defaultdict(list)
        for cid, rs in cand.items():
            if cid not in done and rs:
                groups[frozenset(rs)].append(cid)
        for rs, cids in groups.items():
            if len(cids) == len(rs):
                key = etime if etime else sorted
                for cid, r in zip(sorted(cids), sorted(rs, key=key)):
                    done[cid] = r
            elif not ordered and len(rs) <= 2:
                for cid in sorted(cids):
                    free = [r for r in sorted(rs) if r not in done.values()]
                    if free:
                        done[cid] = free[0]
        for cid, r in done.items():
            assigned[cid].add(r)
