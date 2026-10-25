"""Tolerant parser that turns the many log syntaxes in dataset/evidence/logs into flat dicts."""
import json
import re

KV = re.compile(r'([A-Za-z_][\w.\-]*)=("([^"]*)"|[^\s"]+)')
BRACKET = re.compile(r'^\[(?P<level>[A-Z]+)\]\s+\[(?P<service>[^\]]+)\]\s+\[(?P<trace>[^\]]+)\]\s+(?P<event>[\w.\-]+):\s*(?P<rest>.*)$')
SYSLOG = re.compile(r'^<\d+>1\s+(?P<node>\S+)\s+(?P<service>\S+)\s+(?P<trace>\S+)\s+-\s+-\s+(?P<rest>.*)$')


def _kv(text):
    out = {}
    for m in KV.finditer(text):
        v = m.group(3) if m.group(3) is not None else m.group(2)
        out[m.group(1)] = v
    return out


def parse_line(line):
    """Return a dict with at least 'ts' and (when recognisable) 'event'; None for blank lines."""
    line = line.rstrip('\n')
    if not line.strip():
        return None
    ts, _, rest = line.partition(' ')
    rec = {'ts': ts}
    rest = rest.strip()
    if rest.startswith('{'):
        try:
            obj = json.loads(rest)
            rec.update({str(k): ('' if v is None else str(v)) for k, v in obj.items()})
            if 'severity' in rec:
                rec['level'] = rec.pop('severity')
            return rec
        except ValueError:
            pass
    if rest.startswith('|') or ' | ' in rest[:80]:
        parts = [p.strip() for p in rest.split(' | ')]
        if len(parts) >= 5:
            rec.update(level=parts[0], service=parts[1], trace_id=parts[2], event=parts[3])
            rec.update(_kv(' | '.join(parts[4:])))
            return rec
    m = BRACKET.match(rest)
    if m:
        rec.update(level=m['level'], service=m['service'], trace_id=m['trace'], event=m['event'])
        rec.update(_kv(m['rest']))
        return rec
    m = SYSLOG.match(rest)
    if m:
        rec.update(service=m['service'], trace_id=m['trace'], node=m['node'])
        rec.update(_kv(m['rest']))
        return rec
    # cloud-runtime style: message="k=\"v\" ..." wrapped inside key=value pairs
    outer = _kv(rest.replace('\\"', "\u0001"))
    if 'message' in outer and "\u0001" in outer['message']:
        inner = _kv(outer['message'].replace("\u0001", '"'))
        outer.pop('message')
        rec.update({k: v.replace("\u0001", '"') for k, v in outer.items()})
        rec.update(inner)
        if 'function' in rec and 'service' not in rec:
            rec['service'] = rec['function']
        if 'requestId' in rec and 'trace_id' not in rec:
            rec['trace_id'] = rec['requestId']
        return rec
    rec.update(_kv(rest))
    return rec


def parse_file(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        for n, line in enumerate(fh, 1):
            r = parse_line(line)
            if r is not None:
                r['_line'] = n
                yield r
