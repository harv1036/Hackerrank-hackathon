"""Turn every evidence file into normalised observations.

An observation says: "at time T, in store S of system X, resource R was in state Q and
the record exposed these identifiers". The solver never looks at raw files again.
"""
import csv
import glob
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field

from .logparse import parse_file

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
HASH_RE = re.compile(r'\b[0-9a-f]{64}\b')
USER_RE = re.compile(r'\buser_\d+\b')

PREFIX_CATEGORY = {
    'account': ('account_service', 'USER_PROFILE'),
    'search': ('search_service', 'SEARCH_INDEX_ENTRY'),
    'analytics': ('analytics_platform', 'ANALYTICS_PROFILE'),
    'support': ('support_platform', 'SUPPORT_MESSAGE'),
    'export': ('export_service', 'USER_EXPORT'),
    'data_flow': ('integration_service', 'DATA_FLOW'),
    'backup': ('backup_service', 'BACKUP_ARCHIVE'),
    'audit_record': ('security_service', 'SECURITY_AUDIT_LOG'),
    'tombstone': ('identity_service', 'ACCOUNT_TOMBSTONE'),
    'legal_hold': ('legal_service', 'LEGAL_HOLD_RECORD'),
    'marketing': ('marketing_platform', 'MARKETING_PROFILE'),
    'object': ('support_platform', 'SUPPORT_ATTACHMENT'),
}


def prefix_of(rid):
    m = re.match(r'^(.*?)_\d+$', rid or '')
    return m.group(1) if m else None


@dataclass
class Obs:
    file_id: str
    file_time: str
    rec_time: str
    system: str
    rid: str
    cat: str
    state: str                     # PRESENT ABSENT IDENTIFIABLE ANONYMIZED REDACTED ACTIVE DISABLED BLOCKED FOUND
    keys: frozenset = frozenset()  # lower-cased emails, user refs, hashes, phones
    store: str = ''
    attrs: dict = field(default_factory=dict)


def norm(v):
    if v is None:
        return None
    v = str(v).strip()
    if v == '' or v.lower() in ('null', 'none', 'n/a'):
        return None
    return v


def mk_keys(*vals):
    out = set()
    for v in vals:
        v = norm(v)
        if not v:
            continue
        out.add(v.lower())
    return frozenset(out)


def keys_in_text(text):
    out = set()
    if not text:
        return out
    out.update(e.lower() for e in EMAIL_RE.findall(text))
    out.update(USER_RE.findall(text))
    out.update(HASH_RE.findall(text))
    return out


# --------------------------------------------------------------------------- record mapping
def record_obs(fid, ftime, rtime, system, collection, rec, store):
    """Map one structured record to an Obs (or None if unrecognised)."""
    rid = rec.get('resource_id')
    if not rid:
        return None
    pfx = prefix_of(rid)
    sysid, cat = PREFIX_CATEGORY.get(pfx, (system, None))
    system = system or sysid
    attrs = {}
    keys = set()
    state = 'PRESENT'
    c = collection or ''
    if cat is None:
        return None
    if cat == 'USER_PROFILE':
        keys = mk_keys(rec.get('customer_reference'), rec.get('user_id'), rec.get('email'), rec.get('phone'), rec.get('email_hash'))
        status = (rec.get('account_status') or '').upper()
        state = 'PRESENT'
        attrs['account_status'] = status
    elif cat == 'SEARCH_INDEX_ENTRY':
        doc = rec.get('document') or rec
        keys = mk_keys(doc.get('customer_reference'), doc.get('user_id'), doc.get('email'), doc.get('phone'), doc.get('email_hash'),
                       rec.get('customer_reference'), rec.get('email'), rec.get('email_hash'), rec.get('phone'))
        if rec.get('source_resource_id'):
            attrs['source_resource_id'] = rec['source_resource_id']
        state = 'PRESENT'
    elif cat == 'ANALYTICS_PROFILE':
        keys = mk_keys(rec.get('user_id'), rec.get('customer_reference'), rec.get('email'), rec.get('phone'), rec.get('email_hash'))
        state = 'IDENTIFIABLE' if keys else 'ANONYMIZED'
    elif cat in ('SUPPORT_MESSAGE', 'SUPPORT_ATTACHMENT'):
        if c == 'ticket_attachments' or cat == 'SUPPORT_ATTACHMENT' or 'file_name' in rec:
            cat = 'SUPPORT_ATTACHMENT'
            keys = mk_keys(rec.get('uploaded_by'), rec.get('subject_reference'), rec.get('customer_reference'))
            attrs['file_name'] = rec.get('file_name')
            state = 'PRESENT'
        else:
            text = rec.get('message_text') or rec.get('message_excerpt') or ''
            direct = mk_keys(rec.get('requester_email'), rec.get('requester_phone'), rec.get('requester_name'))
            textkeys = keys_in_text(text) - {k for k in keys_in_text(text) if k.startswith('user_')}
            keys = mk_keys(rec.get('subject_reference'), rec.get('customer_reference'), rec.get('requester_email'), rec.get('requester_phone')) | frozenset(textkeys)
            exposed = bool(direct) or bool(textkeys)
            state = 'IDENTIFIABLE' if exposed else 'REDACTED'
    elif cat == 'USER_EXPORT':
        keys = mk_keys(rec.get('owner_email'), rec.get('owner_phone'), rec.get('customer_reference'), rec.get('subject_reference'))
        avail = rec.get('download_available')
        attrs['download_available'] = avail
        state = 'PRESENT'
    elif cat == 'DATA_FLOW':
        keys = mk_keys(rec.get('customer_email'), rec.get('customer_reference'), rec.get('subject_selector'), rec.get('subject_reference'))
        en = rec.get('enabled')
        en = bool(int(en)) if isinstance(en, (int, str)) and str(en).isdigit() else bool(en)
        attrs['enabled'] = en
        attrs['connector_name'] = rec.get('connector_name')
        state = 'ACTIVE' if en else 'DISABLED'
    elif cat == 'BACKUP_ARCHIVE':
        keys = mk_keys(rec.get('customer_email'), rec.get('customer_reference'), rec.get('subject_reference'))
        ra = rec.get('restore_allowed')
        ra = bool(int(ra)) if isinstance(ra, (int, str)) and str(ra).isdigit() else bool(ra)
        attrs['restore_allowed'] = ra
        state = 'ACTIVE' if ra else 'BLOCKED'
    elif cat == 'SECURITY_AUDIT_LOG':
        keys = mk_keys(rec.get('subject_reference'), rec.get('customer_reference'))
        attrs['retention_until'] = rec.get('retention_until')
        state = 'FOUND'
    elif cat == 'ACCOUNT_TOMBSTONE':
        keys = mk_keys(rec.get('subject_reference'), rec.get('customer_reference'), rec.get('user_id'),
                       rec.get('email'), rec.get('phone'), rec.get('email_hash'))
        direct = mk_keys(rec.get('name'), rec.get('email'), rec.get('phone'), rec.get('email_hash'), rec.get('full_name'))
        attrs['direct'] = bool(direct)
        state = 'IDENTIFIABLE' if direct else 'REDACTED'
    elif cat == 'LEGAL_HOLD_RECORD':
        keys = mk_keys(rec.get('subject_reference'), rec.get('customer_reference'))
        attrs['retained_until'] = rec.get('retained_until')
        state = 'FOUND'
    else:
        keys = mk_keys(rec.get('subject_reference'), rec.get('customer_reference'), rec.get('email'))
    return Obs(fid, ftime, rtime or ftime, system, rid, cat, state, keys, store, attrs)


COLLECTION_SYSTEM = {
    'customer_profiles': 'account_service', 'customer_search_documents': 'search_service',
    'customer_analytics_reports': 'analytics_platform', 'support_conversations': 'support_platform',
    'ticket_attachments': 'support_platform', 'customer_export_jobs': 'export_service',
    'connector_configurations': 'integration_service', 'backup_catalog': 'backup_service',
    'security_audit_events': 'security_service', 'deleted_customer_references': 'identity_service',
    'retained_order_records': 'legal_service',
}

TYPED_TABLES = {
    'customer_profiles': ('account_service', 'customer_profiles'),
    'customer_profiles_primary': ('account_service', 'customer_profiles'),
    'customer_profiles_failover_replica': ('account_service', 'customer_profiles'),
    'customer_search_documents': ('search_service', 'customer_search_documents'),
    'search_documents_primary': ('search_service', 'customer_search_documents'),
    'customer_analytics_profiles': ('analytics_platform', 'customer_analytics_reports'),
    'support_records': ('support_platform', 'support_conversations'),
    'customer_exports': ('export_service', 'customer_export_jobs'),
    'connector_subscriptions': ('integration_service', 'connector_configurations'),
    'backup_restore_entries': ('backup_service', 'backup_catalog'),
    'security_audit_records': ('security_service', 'security_audit_events'),
}


# --------------------------------------------------------------------------- loaders
def load_sql(fid, ftime, path, out, scopes):
    text = open(path, encoding='utf-8', errors='replace').read()
    cap = re.search(r'Captured at:\s*(\S+)', text)
    ftime = cap.group(1) if cap else ftime
    con = sqlite3.connect(':memory:')
    con.executescript(text)
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    scope = {}
    if 'snapshot_scope' in tables:
        for r in con.execute('select origin_system, captured_at from snapshot_scope'):
            scope[r['origin_system']] = r['captured_at']
    for t in tables:
        if t == 'snapshot_scope':
            continue
        cols = [r[1] for r in con.execute(f'pragma table_info({t})')]
        store = f'sql:{t}'
        if 'record_json' in cols:
            for r in con.execute(f'select * from {t}'):
                rec = json.loads(r['record_json'])
                rec.setdefault('resource_id', r['resource_id'])
                sysid = r['origin_system']
                rt = scope.get(sysid, ftime)
                o = record_obs(fid, ftime, rt, sysid, r['collection_name'], rec, store)
                if o:
                    out.append(o)
            for sysid, coll in {(r['origin_system'], r['collection_name']) for r in con.execute(f'select origin_system, collection_name from {t}')}:
                scopes.append((fid, store, sysid, scope.get(sysid, ftime)))
            # systems that are in scope but have no rows still count as covered
            for sysid, st in scope.items():
                scopes.append((fid, store, sysid, st))
        elif t in TYPED_TABLES:
            sysid, coll = TYPED_TABLES[t]
            for r in con.execute(f'select * from {t}'):
                rec = dict(r)
                rec = {k: rec[k] for k in rec}
                if 'email_hash' not in rec:
                    rec['email_hash'] = None
                if 'user_id' in rec and 'customer_reference' not in rec:
                    rec['customer_reference'] = rec['user_id']
                if t == 'customer_search_documents':
                    rec['document'] = {'display_name': rec.get('display_name'), 'email': rec.get('email'),
                                       'customer_reference': rec.get('customer_reference'), 'email_hash': rec.get('email_hash'),
                                       'phone': rec.get('phone')}
                if t == 'support_records':
                    rec['message_text'] = rec.get('message_excerpt')
                if t == 'connector_subscriptions':
                    rec['enabled'] = rec.get('enabled')
                rt = scope.get(sysid) or rec.get('updated_at') or rec.get('indexed_at') or ftime
                o = record_obs(fid, ftime, rt, sysid, coll, rec, store)
                if o:
                    out.append(o)
            scopes.append((fid, store, sysid, scope.get(sysid, ftime)))


def load_json(fid, ftime, path, out, scopes):
    o = json.load(open(path, encoding='utf-8'))
    base = os.path.basename(path)
    store = 'service'
    if 'snapshots' in o:
        for s in o['snapshots']:
            sysid = s.get('data_store')
            for rec in s['records']:
                ob = record_obs(fid, ftime, s.get('captured_at'), sysid, s.get('collection'), rec, store)
                if ob:
                    out.append(ob)
            scopes.append((fid, store, sysid, s.get('captured_at')))
    elif 'service_exports' in o:
        for svc, groups in o['service_exports'].items():
            for coll, rows in groups.items():
                for rec in rows:
                    ob = service_export_obs(fid, ftime, svc, coll, rec, store)
                    if ob:
                        out.append(ob)
                scopes.append((fid, store, svc, ftime))
    elif 'records' in o and o['records'] and 'record_type' in o['records'][0]:
        for rec in o['records']:
            ob = ledger_obs(fid, ftime, rec.get('recorded_at'), rec['system'], rec['resource_id'], rec['record_type'], rec['result'], rec.get('subject_reference'), store)
            if ob:
                out.append(ob)
    elif 'records' in o:
        coll = o.get('collection')
        sysid = COLLECTION_SYSTEM.get(coll)
        if coll in ('customer_analytics_reports',):
            sysid = 'analytics_platform'
        if coll == 'customer_export_jobs':
            sysid = 'export_service'
        for rec in o['records']:
            ob = record_obs(fid, ftime, o.get('captured_at'), sysid, coll, rec, store)
            if ob:
                out.append(ob)
        scopes.append((fid, store, sysid, o.get('captured_at', ftime)))


def service_export_obs(fid, ftime, svc, coll, rec, store):
    rt = rec.get('observed_at') or ftime
    rid = rec.get('resource_id')
    if svc == 'account_service':
        prof = rec.get('profile')
        if rec.get('status_code') == 404 or not prof:
            return Obs(fid, ftime, rt, svc, rid, 'USER_PROFILE', 'ABSENT', frozenset(), store, {})
        keys = mk_keys(prof.get('customer_reference'), prof.get('email'), prof.get('phone'), prof.get('email_hash'))
        return Obs(fid, ftime, rt, svc, rid, 'USER_PROFILE', 'PRESENT', keys, store, {})
    if svc == 'search_service':
        hits = (rec.get('hits') or {}).get('documents') or []
        if not hits:
            return Obs(fid, ftime, rt, svc, rid, 'SEARCH_INDEX_ENTRY', 'ABSENT', mk_keys(rec.get('query')), store, {})
        d = hits[0]
        keys = mk_keys(d.get('customer_reference'), d.get('email'), d.get('phone'), d.get('email_hash'), rec.get('query'))
        return Obs(fid, ftime, rt, svc, d.get('resource_id') or rid, 'SEARCH_INDEX_ENTRY', 'PRESENT', keys, store, {})
    if svc == 'analytics_platform':
        return record_obs(fid, ftime, rt, svc, 'customer_analytics_reports', rec, store)
    if svc == 'support_platform':
        r = dict(rec)
        r['message_text'] = rec.get('message_excerpt')
        return record_obs(fid, ftime, rt, svc, 'support_conversations', r, store)
    if svc == 'export_service':
        return record_obs(fid, ftime, rt, svc, 'customer_export_jobs', rec, store)
    if svc == 'integration_service':
        return record_obs(fid, ftime, rt, svc, 'connector_configurations', rec, store)
    if svc == 'backup_service':
        return record_obs(fid, ftime, rt, svc, 'backup_catalog', rec, store)
    if svc == 'security_service':
        return record_obs(fid, ftime, rt, svc, 'security_audit_events', rec, store)
    return None


LEDGER_STATE = {
    ('USER_PROFILE', 'FOUND'): 'PRESENT', ('USER_PROFILE', 'NOT_FOUND'): 'ABSENT',
    ('SEARCH_INDEX_ENTRY', 'FOUND'): 'PRESENT', ('SEARCH_INDEX_ENTRY', 'NOT_FOUND'): 'ABSENT',
    ('USER_EXPORT', 'FOUND'): 'PRESENT', ('USER_EXPORT', 'NOT_FOUND'): 'ABSENT',
    ('SUPPORT_ATTACHMENT', 'FOUND'): 'PRESENT', ('SUPPORT_ATTACHMENT', 'NOT_FOUND'): 'ABSENT',
    ('LEGAL_HOLD_RECORD', 'FOUND'): 'FOUND', ('SECURITY_AUDIT_LOG', 'FOUND'): 'FOUND',
}


def ledger_obs(fid, ftime, rtime, system, rid, rtype, result, subject, store):
    state = LEDGER_STATE.get((rtype, result), result)
    keys = mk_keys(subject) if subject and subject.lower() not in ('no direct identifier',) else frozenset()
    if rtype == 'ANALYTICS_PROFILE':
        state = 'ANONYMIZED' if result == 'ANONYMIZED' else 'IDENTIFIABLE'
    return Obs(fid, ftime, rtime or ftime, system, rid, rtype, state, keys, store, {})


def load_txt(fid, ftime, path, out, scopes):
    store = 'service'
    for ln in open(path, encoding='utf-8', errors='replace'):
        p = [x.strip() for x in ln.strip().lstrip('-').split('|')]
        if len(p) >= 6:
            ob = ledger_obs(fid, ftime, p[0], p[1], p[2], p[3], p[4], p[5], store)
            out.append(ob)


def load_log(fid, ftime, path, out, scopes):
    store = 'service'
    recs = list(parse_file(path))
    # index "started" lines by trace so keys can be attached to the matching "completed" line
    started = {}
    for r in recs:
        tr = r.get('trace_id') or r.get('requestId')
        ev = r.get('event', '')
        if ev.endswith('_started') and tr:
            started[(tr, r.get('resource_id') or '')] = r
            started.setdefault((tr, ''), r)
    for r in recs:
        ev = r.get('event', '')
        ts = r.get('ts')
        tr = r.get('trace_id') or r.get('requestId')
        rid = r.get('resource_id')
        if ev == 'record_lookup':
            out.append(ledger_obs(fid, ftime, ts, r['system'], rid, r['record_type'], r['result'], r.get('subject_reference'), store))
            continue
        st = started.get((tr, rid or '')) or started.get((tr, '')) or {}
        lk = norm(st.get('lookup_key') or st.get('match_key') or st.get('subject_key') or st.get('owner_key')
                  or st.get('scan_key') or st.get('subject_ref'))
        lk = lk.lower() if lk else None
        extra = {lk} if lk else set()
        if ev == 'profile_lookup_completed' and rid:
            present = str(r.get('http_status')) == '200' and str(r.get('rows', '1')) != '0'
            keys = mk_keys(r.get('customer_reference'), r.get('email'), r.get('phone'), r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'account_service', rid, 'USER_PROFILE', 'PRESENT' if present else 'ABSENT', frozenset(keys), store, {}))
        elif ev == 'index_query_completed' and rid:
            hit = (r.get('result') == 'hit') and str(r.get('total_hits', '1')) != '0'
            keys = mk_keys(r.get('email'), r.get('customer_reference'), r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'search_service', rid, 'SEARCH_INDEX_ENTRY', 'PRESENT' if hit else 'ABSENT', frozenset(keys), store, {}))
        elif ev == 'analytics_profile_read_completed' and rid:
            keys = mk_keys(r.get('customer_reference'), r.get('email'), r.get('phone'), r.get('email_hash'))
            out.append(Obs(fid, ftime, ts, 'analytics_platform', rid, 'ANALYTICS_PROFILE', 'IDENTIFIABLE' if keys else 'ANONYMIZED', frozenset(keys | extra), store, {}))
        elif ev == 'analytics_profile_read' and rid:
            keys = mk_keys(r.get('email'), r.get('phone'))
            ident = r.get('direct_identifiers', '')
            state = 'IDENTIFIABLE' if (keys or str(ident) not in ('', '0', 'none', 'false', 'null')) else 'ANONYMIZED'
            out.append(Obs(fid, ftime, ts, 'analytics_platform', rid, 'ANALYTICS_PROFILE', state, frozenset(keys | extra), store, {}))
        elif ev == 'support_ticket_read_completed' and rid:
            direct = mk_keys(r.get('requester_name'), r.get('requester_email'), r.get('requester_phone'))
            keys = mk_keys(r.get('customer_reference'), r.get('requester_email'), r.get('requester_phone')) | extra
            out.append(Obs(fid, ftime, ts, 'support_platform', rid, 'SUPPORT_MESSAGE', 'IDENTIFIABLE' if direct else 'REDACTED', frozenset(keys), store, {}))
        elif ev == 'message_pii_scan' and rid:
            keys = mk_keys(r.get('matched_value')) | extra
            out.append(Obs(fid, ftime, ts, 'support_platform', rid, 'SUPPORT_MESSAGE', 'IDENTIFIABLE', frozenset(keys), store, {}))
        elif ev == 'message_read' and rid:
            text = r.get('message_text', '')
            tk = {k for k in keys_in_text(text) if not k.startswith('user_')}
            keys = mk_keys(r.get('subject_ref')) | frozenset(tk) | extra
            out.append(Obs(fid, ftime, ts, 'support_platform', rid, 'SUPPORT_MESSAGE', 'IDENTIFIABLE' if tk else 'REDACTED', frozenset(keys), store, {}))
        elif ev == 'attachment_pii_scan' and rid:
            keys = mk_keys(r.get('matched_value')) | extra
            out.append(Obs(fid, ftime, ts, 'support_platform', rid, 'SUPPORT_ATTACHMENT', 'PRESENT', frozenset(keys), store, {'file_name': r.get('file')}))
        elif ev in ('connector_config_read_completed', 'connector_config_loaded') and rid:
            en = str(r.get('enabled', '')).lower() == 'true'
            keys = mk_keys(r.get('customer_email'), r.get('customer_reference'), r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'integration_service', rid, 'DATA_FLOW', 'ACTIVE' if en else 'DISABLED', frozenset(keys), store,
                           {'enabled': en, 'connector_name': r.get('connector_name'), 'scheduler_registered': r.get('scheduler_registered')}))
        elif ev == 'connector_heartbeat' and rid:
            en = str(r.get('enabled', '')).lower() == 'true'
            keys = mk_keys(r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'integration_service', rid, 'DATA_FLOW', 'ACTIVE' if en else 'DISABLED', frozenset(keys), store, {'enabled': en}))
        elif ev == 'restore_policy_read_completed' and rid:
            ra = str(r.get('restore_allowed', '')).lower() == 'true'
            keys = mk_keys(r.get('customer_email'), r.get('customer_reference')) | extra
            out.append(Obs(fid, ftime, ts, 'backup_service', rid, 'BACKUP_ARCHIVE', 'ACTIVE' if ra else 'BLOCKED', frozenset(keys), store, {'restore_allowed': ra}))
        elif ev == 'restore_candidate_evaluated' and rid:
            elig = str(r.get('eligible', '')).lower() == 'true'
            keys = mk_keys(r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'backup_service', rid, 'BACKUP_ARCHIVE', 'ACTIVE' if elig else 'BLOCKED', frozenset(keys), store,
                           {'eligible': elig, 'deletion_block': r.get('deletion_block')}))
        elif ev == 'restore_authorization_completed' and rid:
            dec = str(r.get('decision', '')).lower()
            allowed = dec in ('allow', 'allowed', 'approved', 'permit', 'permitted')
            out.append(Obs(fid, ftime, ts, 'backup_service', rid, 'BACKUP_ARCHIVE', 'ACTIVE' if allowed else 'BLOCKED', frozenset(extra), store,
                           {'decision': dec, 'policy': r.get('policy')}))
        elif ev == 'audit_record_read_completed' and rid:
            keys = mk_keys(r.get('subject_reference'), r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'security_service', rid, 'SECURITY_AUDIT_LOG', 'FOUND', frozenset(keys), store,
                           {'retention_until': r.get('retention_until')}))
        elif ev in ('export_metadata_read_completed',) and rid:
            avail = str(r.get('available', '')).lower() == 'true'
            keys = mk_keys(r.get('owner_email')) | extra
            out.append(Obs(fid, ftime, ts, 'export_service', rid, 'USER_EXPORT', 'PRESENT', frozenset(keys), store, {'download_available': avail}))
        elif ev == 'export_download_check_completed' and rid:
            avail = str(r.get('available', '')).lower() == 'true'
            keys = mk_keys(r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'export_service', rid, 'USER_EXPORT', 'PRESENT', frozenset(keys), store, {'download_available': avail}))
        elif ev == 'contact_query_completed' and rid:
            # legacy contact index entry in a support replica: a secondary identifier, always deleted
            present = str(r.get('rows', '1')) != '0'
            keys = mk_keys(r.get('lookup_key')) | extra
            out.append(Obs(fid, ftime, ts, 'support_platform', rid, 'SECONDARY_IDENTIFIER', 'PRESENT' if present else 'ABSENT', frozenset(keys), store, {}))
        elif ev == 'contact_lookup_completed' and rid:
            # marketing contact keyed by email hash (an old identifier): a marketing profile, always deleted
            present = str(r.get('rows', '1')) != '0'
            keys = mk_keys(r.get('lookup_key'), r.get('correlation_key')) | extra
            out.append(Obs(fid, ftime, ts, 'analytics_platform', rid, 'MARKETING_PROFILE', 'PRESENT' if present else 'ABSENT', frozenset(keys), store,
                           {'email_opt_in': r.get('email_opt_in')}))


def load_manifest(dataset_dir):
    rows = list(csv.DictReader(open(os.path.join(dataset_dir, 'evidence_files.csv'), encoding='utf-8')))
    return {r['file_id']: r for r in rows}


def load_all(dataset_dir, extracted=None):
    """Return (observations, scopes, manifest). `extracted` = pre-extracted facts for PDFs/images."""
    manifest = load_manifest(dataset_dir)
    out, scopes = [], []
    for fid, m in sorted(manifest.items()):
        path = os.path.join(dataset_dir, m['file_path'])
        ft = m['captured_at']
        if path.endswith('.sql'):
            load_sql(fid, ft, path, out, scopes)
        elif path.endswith('.json'):
            load_json(fid, ft, path, out, scopes)
        elif path.endswith('.txt'):
            load_txt(fid, ft, path, out, scopes)
        elif path.endswith('.log'):
            load_log(fid, ft, path, out, scopes)
    if extracted:
        for rec in extracted:
            fid = rec['file_id']
            m = manifest.get(fid)
            if not m:
                continue
            out.append(Obs(fid, m['captured_at'], rec.get('rec_time') or m['captured_at'], rec['system'], rec['rid'], rec['cat'],
                           rec['state'], mk_keys(*rec.get('keys', [])), 'service', rec.get('attrs', {})))
    return out, scopes, manifest
