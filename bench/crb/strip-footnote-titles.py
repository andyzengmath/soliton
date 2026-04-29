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

# Phase 5.2.2: handles inner parens in footnote trailers. Phase 5.2.1's [^)]*
# stopped at the first inner ) char, leaving footnotes like
#   (N additional findings below confidence threshold ... (conf 65); ...; (conf 70))
# half-stripped. Switching to [^\n]* with the closing \) anchored to end-of-line
# lets the greedy match backtrack to the LAST ) on the line, which is the outer
# closing paren of the footnote.
#
# Handles all observed variants:
#   (N additional findings below confidence threshold)                            -> unchanged
#   (N additional findings below confidence threshold: t1; t2)                   -> strip
#   (N additional findings below confidence threshold 85 suppressed: t1; t2)     -> strip
#   (N additional findings below confidence threshold of 85: t1; t2)             -> strip
#   (N additional findings below confidence threshold — t1 — suppressed at 85)   -> strip
#   (N additional findings below confidence threshold — t1; t2)                  -> strip
#   (N additional findings below confidence threshold ... (conf 65); ... (conf 70))  -> strip (NEW in 5.2.2)
#   (N additional findings below confidence threshold: t [conf 78, correctness])  -> strip (NEW in 5.2.2)
PATTERN = re.compile(
    r'\((\d+) additional findings below confidence threshold'
    r'(?P<trailer>[^\n]*)'
    r'\)',
)

n_total = 0
n_stripped = 0
titles_removed = 0
footnotes_stripped = 0
for md in SRC.glob('*.md'):
    txt = md.read_text(encoding='utf-8')
    original = txt
    def replacer(m):
        global titles_removed, footnotes_stripped
        trailer = m.group('trailer')
        if trailer.strip():
            # Count semicolons as an estimator of title count; em-dash or
            # freeform trailers count as >=1 stripped title.
            semicolon_count = trailer.count(';')
            titles_removed += max(semicolon_count + 1, 1) if any(c in trailer for c in ':—-') else 0
            footnotes_stripped += 1
        return f'({m.group(1)} additional findings below confidence threshold)'
    new_txt = PATTERN.sub(replacer, txt)
    if new_txt != original:
        n_stripped += 1
    (DST / md.name).write_text(new_txt, encoding='utf-8')
    n_total += 1

print(f'Reviews processed      : {n_total}')
print(f'Reviews modified       : {n_stripped}')
print(f'Footnotes stripped     : {footnotes_stripped}')
print(f'Titles removed (estimate): {titles_removed}')
print(f'Output dir             : {DST}')
