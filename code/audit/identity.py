"""Resolve a request email to a stable user and every identifier that user has ever had."""
import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Identity:
    user_id: str
    emails: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    name: str = ''

    @property
    def keys(self):
        """Every lower-cased identifier that can expose this user in evidence."""
        return {self.user_id.lower()} | {e.lower() for e in self.emails} | {p.lower() for p in self.phones} | {h.lower() for h in self.hashes}


class IdentityBook:
    def __init__(self, dataset_dir):
        self.current = {}
        self.history = defaultdict(list)
        for r in csv.DictReader(open(os.path.join(dataset_dir, 'user_identities.csv'), encoding='utf-8')):
            self.current[r['user_id']] = r
        for a in csv.DictReader(open(os.path.join(dataset_dir, 'user_audit_log.csv'), encoding='utf-8')):
            self.history[a['user_id']].append(a)
        self.by_email = defaultdict(set)
        for uid, r in self.current.items():
            self.by_email[r['email'].lower()].add(uid)
        for uid, rows in self.history.items():
            for a in rows:
                if a['field_name'] == 'email':
                    self.by_email[a['old_value'].lower()].add(uid)
                    self.by_email[a['new_value'].lower()].add(uid)

    def resolve(self, email, audit_at=None, requested_at=None):
        email = (email or '').strip().lower()
        uids = self.by_email.get(email)
        if not uids:
            return None
        if len(uids) > 1:
            # Prefer the user whose identity history most recently owned the requested
            # address at the time of the request.  Fall back to audit time only when
            # the request time is unavailable.  Never choose by user_id alone.
            ref = requested_at or audit_at
            ranked = []
            for candidate in uids:
                last = None
                current_email = self.current[candidate].get('email', '').strip().lower()
                if current_email == email:
                    last = self.current[candidate].get('updated_at') or self.current[candidate].get('created_at')
                for a in sorted(self.history.get(candidate, []), key=lambda x: x['event_time']):
                    if a['field_name'] != 'email':
                        continue
                    if ref and a['event_time'] > ref:
                        break
                    if a['old_value'].strip().lower() == email or a['new_value'].strip().lower() == email:
                        last = a['event_time']
                if last:
                    ranked.append((last, candidate))
            if ranked:
                ranked.sort(reverse=True)
                if len(ranked) == 1 or ranked[0][0] != ranked[1][0]:
                    uid = ranked[0][1]
                else:
                    return None
            else:
                return None
        else:
            uid = next(iter(uids))
        cur = self.current[uid]
        ident = Identity(uid, name=cur.get('name', ''))
        ident.emails.add(cur['email'])
        if cur.get('phone'):
            ident.phones.add(cur['phone'])
        if cur.get('email_hash'):
            ident.hashes.add(cur['email_hash'])
        for a in self.history.get(uid, []):
            # a change after audit_at has not happened yet: keep its old value, skip its new value
            future = bool(audit_at) and a['event_time'] > audit_at
            vals = [a['old_value']] if future else [a['old_value'], a['new_value']]
            tgt = {'email': ident.emails, 'phone': ident.phones, 'email_hash': ident.hashes}.get(a['field_name'])
            if tgt is not None:
                tgt.update(v for v in vals if v)
        ident.emails.add(email.strip())
        return ident
