#!/usr/bin/env python3
"""Optional: re-extract the facts of the screenshots and PDFs with Gemini (vision).

    export GEMINI_API_KEY=...        # free-tier key from Google AI Studio
    python3 code/extract.py          # writes code/audit/data/extracted_facts.json

The repository already ships extracted_facts.json (the facts of the 11 screenshots and 4 PDFs, read and checked
by hand), so this step is only needed to reproduce or refresh them.  Results are cached per file in
code/cache/llm_cache.json, so a second run makes no calls.
"""
import base64
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from audit.llm import GeminiClient, PROMPTS            # noqa: E402
from audit.solve import PREFIX_SYSTEM                  # noqa: E402

MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.pdf': 'application/pdf'}


def main():
    llm = GeminiClient.from_env()
    if llm is None:
        sys.exit('GEMINI_API_KEY is not set; keeping the shipped extracted_facts.json')
    dataset = os.path.join(ROOT, 'dataset')
    prompt = open(os.path.join(PROMPTS, 'extract_screenshot.md'), encoding='utf-8').read()
    out = []
    for m in csv.DictReader(open(os.path.join(dataset, 'evidence_files.csv'), encoding='utf-8')):
        ext = os.path.splitext(m['file_path'])[1].lower()
        if ext not in MIME:
            continue
        with open(os.path.join(dataset, m['file_path']), 'rb') as fh:
            data = base64.b64encode(fh.read()).decode('ascii')
        text = llm.generate(llm.vision_model, prompt, parts=[{'inline_data': {'mime_type': MIME[ext], 'data': data}}], max_tokens=400)
        found = re.search(r'\{.*\}', text, re.S)
        if not found:
            continue
        j = json.loads(found.group(0))
        rid = j.get('resource_id', '')
        out.append({'file_id': m['file_id'], 'rid': rid, 'system': PREFIX_SYSTEM.get(rid.rsplit('_', 1)[0], ''),
                    'cat': j.get('category'), 'state': j.get('state'), 'keys': j.get('identifiers', []),
                    'rec_time': j.get('observed_at') or m['captured_at'], 'attrs': {}, 'note': 'gemini vision'})
    with open(os.path.join(HERE, 'audit', 'data', 'extracted_facts.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    llm.save()
    print(json.dumps(llm.usage, indent=1))


if __name__ == '__main__':
    main()
