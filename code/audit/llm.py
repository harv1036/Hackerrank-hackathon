"""Optional Gemini client (free tier).  Everything here is skipped unless GEMINI_API_KEY is set.

* answers are cached on disk (code/cache/llm_cache.json) so a re-run costs nothing and is deterministic;
* every real call is counted (calls, input tokens, output tokens) for evaluation/usage_report.md.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(HERE, 'cache', 'llm_cache.json')
PROMPTS = os.path.join(HERE, 'prompts')

# USD per 1M tokens (input, output) on the paid tier, used only to show what the free-tier run would cost
PRICES = {'gemini-2.5-flash-lite': (0.10, 0.40), 'gemini-2.5-flash': (0.30, 2.50)}


def load_dotenv():
    """Read GEMINI_API_KEY from an uncommitted .env next to dataset/ when it is not already in the environment."""
    if os.environ.get('GEMINI_API_KEY'):
        return
    path = os.path.join(os.path.dirname(HERE), '.env')
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                k, _, v = line.strip().partition('=')
                if k == 'GEMINI_API_KEY' and v:
                    os.environ[k] = v.strip().strip('"\'')
    except OSError:
        pass


class GeminiClient:
    def __init__(self, api_key, classify_model='gemini-3.1-flash-lite', vision_model='gemini-3.1-flash-lite'):
        self.key = api_key
        self.classify_model = classify_model
        self.vision_model = vision_model
        self.usage = {}      # model -> {'calls', 'input_tokens', 'output_tokens', 'cache_hits'}
        try:
            with open(CACHE_PATH, encoding='utf-8') as fh:
                self.cache = json.load(fh)
        except (OSError, ValueError):
            self.cache = {}

    @staticmethod
    def from_env():
        load_dotenv()
        key = os.environ.get('GEMINI_API_KEY')
        if not key:
            return None
        return GeminiClient(key, os.environ.get('GEMINI_CLASSIFY_MODEL', 'gemini-3.1-flash-lite'),
                            os.environ.get('GEMINI_VISION_MODEL', 'gemini-3.1-flash-lite'))

    def _u(self, model):
        return self.usage.setdefault(model, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'cache_hits': 0})

    def generate(self, model, prompt, parts=None, max_tokens=64):
        ck = hashlib.sha256((model + '\x00' + prompt + '\x00' + json.dumps(parts or [], sort_keys=True)).encode('utf-8')).hexdigest()
        u = self._u(model)
        if ck in self.cache:
            u['cache_hits'] += 1
            return self.cache[ck]
        body = {'contents': [{'parts': [{'text': prompt}] + (parts or [])}],
                'generationConfig': {'temperature': 0, 'maxOutputTokens': max_tokens}}
        req = urllib.request.Request(
            f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'x-goog-api-key': self.key})
        out = None
        for attempt in range(6):               # rate limits (429) and overload (5xx) are usually short-lived
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.load(resp)
                break
            except urllib.error.HTTPError as err:
                if err.code not in (429, 500, 502, 503, 504) or attempt == 5:
                    raise
                time.sleep(min(10 * 2 ** attempt, 120))
        text = ''.join(p.get('text', '') for p in out['candidates'][0]['content']['parts']).strip()
        meta = out.get('usageMetadata', {})
        u['calls'] += 1
        u['input_tokens'] += meta.get('promptTokenCount', 0)
        u['output_tokens'] += meta.get('candidatesTokenCount', 0)
        self.cache[ck] = text
        return text

    def classify_request(self, text):
        with open(os.path.join(PROMPTS, 'classify_request.md'), encoding='utf-8') as fh:
            prompt = fh.read().replace('{text}', text)
        try:
            ans = self.generate(self.classify_model, prompt, max_tokens=8).upper()
        except Exception:                      # network or quota problems must never break the run
            return None
        return 'NOT_DELETE' if 'NOT' in ans else ('DELETE' if 'DELETE' in ans else None)

    def save(self):
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, 'w', encoding='utf-8') as fh:
            json.dump(self.cache, fh, indent=1, sort_keys=True)
