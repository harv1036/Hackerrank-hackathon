"""Core audit logic: link resources to a user, decide their state at audit_at, apply the policy, emit steps."""
import json
from collections import defaultdict

from .evidence import prefix_of

# category -> (compliant state, non-compliant state, finding op, remediation op, verification op)
POLICY = {
    'USER_PROFILE':        ('ABSENT', 'PRESENT', 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'SEARCH_INDEX_ENTRY':  ('ABSENT', 'PRESENT', 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'USER_EXPORT':         ('ABSENT', 'PRESENT', 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'SUPPORT_ATTACHMENT':  ('ABSENT', 'PRESENT', 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'MARKETING_PROFILE':   ('ABSENT', 'PRESENT', 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'SECONDARY_IDENTIFIER': ('ABSENT', 'PRESENT', 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'CACHE_ENTRY':         ('ABSENT', 'PRESENT', 'REPORT_REMAINING_RECORD', 'DELETE_RECORD', 'VERIFY_RECORD_ABSENT'),
    'ANALYTICS_PROFILE':   ('ANONYMIZED', 'IDENTIFIABLE', 'REPORT_EXPOSED_PII', 'ANONYMIZE_RECORD', 'VERIFY_RECORD_ANONYMIZED'),
    'SUPPORT_MESSAGE':     ('REDACTED', 'IDENTIFIABLE', 'REPORT_EXPOSED_PII', 'REDACT_RECORD', 'VERIFY_RECORD_REDACTED'),
    'ACCOUNT_TOMBSTONE':   ('REDACTED', 'IDENTIFIABLE', 'REPORT_EXPOSED_PII', 'REDACT_RECORD', 'VERIFY_RECORD_REDACTED'),
    'DATA_FLOW':           ('DISABLED', 'ACTIVE', 'REPORT_ACTIVE_DATA_FLOW', 'DISABLE_DATA_FLOW', 'VERIFY_DATA_FLOW_DISABLED'),
    'BACKUP_ARCHIVE':      ('BLOCKED', 'ACTIVE', 'REPORT_RESTORE_RISK', 'BLOCK_RESTORE', 'VERIFY_RESTORE_BLOCKED'),
}
RETAINED = {'SECURITY_AUDIT_LOG', 'LEGAL_HOLD_RECORD'}

FINDING_STATE = {'REPORT_REMAINING_RECORD': 'PRESENT', 'REPORT_EXPOSED_PII': 'PRESENT', 'REPORT_ACTIVE_DATA_FLOW': 'ACTIVE', 'REPORT_RESTORE_RISK': 'ACTIVE'}
REM_STATE = {'DISABLE_DATA_FLOW': 'DISABLED', 'BLOCK_RESTORE': 'BLOCKED', 'DELETE_RECORD': 'ABSENT', 'REDACT_RECORD': 'REDACTED', 'ANONYMIZE_RECORD': 'ANONYMIZED'}
VER_STATE = {'VERIFY_RECORD_ABSENT': 'ABSENT', 'VERIFY_RECORD_PRESENT': 'PRESENT', 'VERIFY_RECORD_REDACTED': 'REDACTED',
             'VERIFY_RECORD_ANONYMIZED': 'ANONYMIZED', 'VERIFY_DATA_FLOW_DISABLED': 'DISABLED', 'VERIFY_RESTORE_BLOCKED': 'BLOCKED'}
OP_ORDER = {op: i for i, op in enumerate([
    'REPORT_REMAINING_RECORD', 'REPORT_EXPOSED_PII', 'REPORT_ACTIVE_DATA_FLOW', 'REPORT_RESTORE_RISK',
    'DISABLE_DATA_FLOW', 'BLOCK_RESTORE', 'DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD',
    'VERIFY_RECORD_ABSENT', 'VERIFY_RECORD_PRESENT', 'VERIFY_RECORD_REDACTED', 'VERIFY_RECORD_ANONYMIZED',
    'VERIFY_DATA_FLOW_DISABLED', 'VERIFY_RESTORE_BLOCKED'])}
PHASE_OF = {}
for _op in OP_ORDER:
    PHASE_OF[_op] = 'FINDING' if _op.startswith('REPORT') else ('VERIFICATION' if _op.startswith('VERIFY') else 'REMEDIATION')
SYSTEM_OF_CAT = {
    'USER_PROFILE': 'account_service', 'SEARCH_INDEX_ENTRY': 'search_service', 'USER_EXPORT': 'export_service',
    'SUPPORT_ATTACHMENT': 'support_platform', 'MARKETING_PROFILE': 'marketing_platform', 'SECONDARY_IDENTIFIER': 'support_platform',
    'ANALYTICS_PROFILE': 'analytics_platform', 'SUPPORT_MESSAGE': 'support_platform', 'ACCOUNT_TOMBSTONE': 'identity_service',
    'DATA_FLOW': 'integration_service', 'BACKUP_ARCHIVE': 'backup_service', 'SECURITY_AUDIT_LOG': 'security_service',
    'LEGAL_HOLD_RECORD': 'legal_service',
}


PREFIX_SYSTEM = {
    'account': 'account_service', 'search': 'search_service', 'support': 'support_platform', 'analytics': 'analytics_platform',
    'data_flow': 'integration_service', 'backup': 'backup_service', 'export': 'export_service', 'audit_record': 'security_service',
    'tombstone': 'identity_service', 'legal_hold': 'legal_service', 'marketing': 'marketing_platform', 'object': 'object_storage',
    'cache': 'cache_service', 'identifier': 'support_platform',
}


def system_of(rid, fallback):
    """A resource id's prefix names its owning system; a snapshot's data_store only names the container."""
    return PREFIX_SYSTEM.get(rid.rsplit('_', 1)[0], fallback)


class Resource:
    def __init__(self, rid, cat, system):
        self.rid, self.cat, self.system = rid, cat, system
        self.obs = []          # observations inside the audit window
        self.evidence = None   # (file_id, state) deciding the state
        self.state = None
        self.link = 'key'      # how the resource was tied to the user


def latest_per_store(obs_list):
    best = {}
    for o in obs_list:
        k = o.store
        cur = best.get(k)
        if cur is None or (o.rec_time, o.file_time) > (cur.rec_time, cur.file_time):
            best[k] = o
    return list(best.values())


def decide(res, audit_at):
    """Resource state at audit_at from the latest observation in every store."""
    cat = res.cat
    latest = latest_per_store([o for o in res.obs if o.rec_time <= audit_at and o.file_time <= audit_at])
    if not latest:
        return None, None
    if cat in POLICY:
        good, bad, *_ = POLICY[cat]
        bads = [o for o in latest if o.state == bad]
        if bads:
            ev = max(bads, key=lambda o: (o.file_time, o.rec_time))
            return bad, ev
        ev = max(latest, key=lambda o: (o.file_time, o.rec_time))
        return good, ev
    ev = max(latest, key=lambda o: (o.file_time, o.rec_time))
    return ev.state, ev


class Solver:
    def __init__(self, obs, scopes, manifest, book, links=None):
        self.obs = obs
        self.scopes = scopes
        self.manifest = manifest
        self.book = book
        self.by_rid = defaultdict(list)
        for o in obs:
            self.by_rid[o.rid].append(o)
        self.by_key = defaultdict(set)
        for o in obs:
            for k in o.keys:
                self.by_key[k].add(o.rid)
        for rid, ks in (links or {}).items():      # data-catalog identity links also tie a resource to a person
            for k in ks:
                self.by_key[k].add(rid)

    # ------------------------------------------------------------------ linking
    def link_resources(self, ident, case, extra=()):
        keys = ident.keys
        start = case['requested_at']
        found = {}
        for k in keys:
            for rid in self.by_key.get(k, ()):
                found[rid] = 'key'
        # a search document points at its source account
        for rid in list(found):
            for o in self.by_rid[rid]:
                src = o.attrs.get('source_resource_id')
                if src and src not in found:
                    found[src] = 'source'
        for rid in extra:
            found.setdefault(rid, 'activity')
        resources = {}
        for rid, how in found.items():
            obs = [o for o in self.by_rid.get(rid, []) if o.file_time >= start]
            if not obs:
                if how == 'activity' and rid.startswith('account_'):
                    res = Resource(rid, 'USER_PROFILE', 'account_service')
                    res.link = how
                    resources[rid] = res
                continue
            cat = obs[0].cat
            res = Resource(rid, cat, system_of(rid, obs[0].system))
            res.link = how
            res.obs = obs
            resources[rid] = res
        return resources

    # ------------------------------------------------------------------ steps
    def direct_links(self, case):
        ident = self.book.resolve(case['email'], case['audit_at'])
        if ident is None:
            return None, {}
        return ident, self.link_resources(ident, case)

    def audit(self, case, extra=()):
        ident = self.book.resolve(case['email'], case['audit_at'])
        if ident is None:
            return 'INCOMPLETE', [], {}
        resources = self.link_resources(ident, case, extra)
        audit_at = case['audit_at']
        findings, remed, verif = [], [], []
        for rid, res in sorted(resources.items()):
            state, ev = decide(res, audit_at)
            if state is None:
                # linked, but never observed inside the audit window: an account the worker deleted is
                # absent, a retained record is still present; other categories give no ground to act on
                if res.cat == 'USER_PROFILE':
                    state = POLICY['USER_PROFILE'][0]
                elif res.cat in RETAINED:
                    state = 'PRESENT'
                else:
                    continue
            res.state, res.evidence = state, ev
            cat = res.cat
            if cat in POLICY:
                good, bad, fop, rop, vop = POLICY[cat]
                eid = ev.file_id if ev else ''
                if state == bad:
                    findings.append((fop, res.system, rid, eid))
                    remed.append((rop, res.system, rid, ''))
                    verif.append((vop, res.system, rid, ''))
                else:
                    verif.append((vop, res.system, rid, eid))
            elif cat in RETAINED:
                verif.append(('VERIFY_RECORD_PRESENT', res.system, rid, ev.file_id if ev else ''))
        if findings:
            # proposed post-remediation checks are future work: no evidence is cited
            verif = [(op, sy, rid, '') for op, sy, rid, _ in verif]
        if not findings:
            verif = self._prove_absence(verif, resources, audit_at, case['requested_at'])
        incomplete = bool(findings)
        steps = self.build_steps(findings, remed, verif, incomplete)
        return ('INCOMPLETE' if incomplete else 'COMPLETE'), steps, resources

    def _prove_absence(self, verif, resources, audit_at, requested_at):
        """A deleted account with no readback of its own is proved absent by a snapshot whose scope covers the
        account service: prefer a file that already backs the case's other resources, else the latest one."""
        used = [e for _, _, _, e in verif if e]
        covering = sorted({fid for fid, store, sysid, cap in self.scopes
                           if sysid == 'account_service' and cap and cap <= audit_at and self.manifest.get(fid, {}).get('captured_at', '') <= audit_at
                           and self.manifest.get(fid, {}).get('captured_at', '') >= requested_at})
        out = []
        for op, sy, rid, eid in verif:
            if op == 'VERIFY_RECORD_ABSENT' and not eid and sy == 'account_service' and covering:
                inter = [f for f in covering if f in used]
                pick = (inter or [max(covering, key=lambda f: self.manifest[f]['captured_at'])])
                eid = max(pick, key=lambda f: self.manifest[f]['captured_at'])
            out.append((op, sy, rid, eid))
        return out

    @staticmethod
    def build_steps(findings, remed, verif, incomplete):
        rows = []
        groups = [findings, remed, verif] if incomplete else [[], [], verif]
        for g in groups:
            g = sorted(g, key=lambda t: (OP_ORDER[t[0]], t[1], t[2]))
            for op, sysid, rid, eid in g:
                rows.append((op, sysid, rid, eid))
        steps = []
        for i, (op, sysid, rid, eid) in enumerate(rows, 1):
            phase = PHASE_OF[op]
            state = FINDING_STATE.get(op) or REM_STATE.get(op) or VER_STATE.get(op)
            if not incomplete:
                pass
            steps.append({'step_no': i, 'phase': phase, 'system_id': sysid, 'resource_id': rid,
                          'operation': op, 'state': state, 'evidence_id': eid})
        return steps
