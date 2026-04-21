#!/usr/bin/env python3
"""Strip below-confidence-threshold footnote titles from Phase 5 reviews.

Counterfactual validator for Phase 5.2: produces phase5_2-reviews/ from
phase5-reviews/ by truncating the `(N additional findings below confidence
threshold: title1; title2; ...)` footer down to `(N additional findings
below confidence threshold)`. Simulates the SKILL.md change without a new
claude -p dispatch, so the judge pipeline can be re-run for $15 to isolate
the pure footnote-leak effect.

Usage:
  python3 bench/crb/strip-footnote-titles.py
"""
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / 'bench' / 'crb' / 'phase5-reviews'
DST = REPO / 'bench' / 'crb' / 'phase5_2-reviews'
DST.mkdir(exist_ok=True)

PATTERN = re.compile(r'\((\d+) additional findings below confidence threshold(?::\s*([^)]*))?\)', re.DOTALL)

n_total = 0
n_stripped = 0
titles_removed = 0
for md in SRC.glob('*.md'):
    txt = md.read_text(encoding='utf-8')
    original = txt
    def replacer(m):
        global titles_removed
        if m.group(2):
            titles_removed += len([t for t in m.group(2).split(';') if t.strip()])
        return f'({m.group(1)} additional findings below confidence threshold)'
    new_txt = PATTERN.sub(replacer, txt)
    if new_txt != original:
        n_stripped += 1
    (DST / md.name).write_text(new_txt, encoding='utf-8')
    n_total += 1

print(f'Reviews processed   : {n_total}')
print(f'Reviews modified    : {n_stripped}')
print(f'Footnote titles removed: {titles_removed}')
print(f'Output dir          : {DST}')
