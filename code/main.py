#!/usr/bin/env python3
"""Part 1 - evidence ingestion.  Usage: python3 code/main.py [--dataset DIR]

Loads every evidence file into normalised observations and prints what was found.
"""
import argparse
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from audit.evidence import load_all          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.join(ROOT, 'dataset'))
    a = ap.parse_args()
    obs, scopes, manifest = load_all(a.dataset)
    print(f'{len(manifest)} evidence files -> {len(obs)} observations, {len(scopes)} snapshot scopes')
    for (cat, state), n in sorted(collections.Counter((o.cat, o.state) for o in obs).items()):
        print(f'  {cat:22s} {state:13s} {n}')


if __name__ == '__main__':
    main()
