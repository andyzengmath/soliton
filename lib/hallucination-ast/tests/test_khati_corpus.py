"""Shipping gate: reproduce Khati 2026 precision / recall numbers on their
200-sample corpus.

Run `python scripts/fetch_khati_corpus.py` once before this test can run;
the corpus is not vendored because upstream has no LICENSE file.

Gate criteria (from Phase 4b resume prompt):
    precision >= 0.95  AND  recall >= 0.80

Khati paper baseline (target, not floor):
    precision = 1.000  recall = 0.876  F1 = 0.934
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest


CORPUS_PATH = Path(__file__).parent / "fixtures" / "khati-2026" / "dataset.csv"

PRECISION_FLOOR = 0.95
RECALL_FLOOR = 0.80


@pytest.fixture(scope="module")
def corpus():
    if not CORPUS_PATH.is_file():
        pytest.skip(
            f"Khati corpus missing at {CORPUS_PATH}. Run: "
            f"python lib/hallucination-ast/scripts/fetch_khati_corpus.py"
        )
    with CORPUS_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _is_clean_sample(row) -> bool:
    return str(row.get("reason", "")).strip().lower() == "no hallucination"


def _was_flagged(report) -> bool:
    return any(f.severity == "critical" for f in report.findings)


def test_khati_corpus_meets_shipping_gate(corpus, capsys):
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    kb = SitePackagesKB()
    tp = fp = fn = tn = 0
    misclassifications: list[tuple[str, str, str]] = []  # (tag, id, reason)

    for row in corpus:
        snippet = row["hallucination"]
        row_id = row.get("id", "?")
        clean = _is_clean_sample(row)

        report = check_source(snippet, f"khati_{row_id}.py", kb)
        flagged = _was_flagged(report)

        if clean and flagged:
            fp += 1
            misclassifications.append(("FP", row_id, row.get("reason", "")))
        elif clean and not flagged:
            tn += 1
        elif (not clean) and flagged:
            tp += 1
        else:
            fn += 1
            misclassifications.append(("FN", row_id, row.get("reason", "")))

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # Print results — capsys.disabled ensures output survives -q / -s.
    with capsys.disabled():
        print()
        print("Khati 2026 corpus results")
        print("-" * 40)
        print(f"  Total samples:     {len(corpus)}")
        print(f"  True  Positives:   {tp}")
        print(f"  False Positives:   {fp}")
        print(f"  True  Negatives:   {tn}")
        print(f"  False Negatives:   {fn}")
        print(f"  Precision:         {precision:.3f}  (floor {PRECISION_FLOOR}, paper 1.000)")
        print(f"  Recall:            {recall:.3f}  (floor {RECALL_FLOOR}, paper 0.876)")
        print(f"  F1:                {f1:.3f}  (paper 0.934)")
        if misclassifications:
            print()
            tag_counts: dict[str, int] = {}
            for tag, _, _ in misclassifications:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            print(f"  Misclassifications: {tag_counts}")
            print(f"  First 10:")
            for tag, rid, reason in misclassifications[:10]:
                print(f"    {tag}  id={rid:>3}  reason={reason}")

    assert precision >= PRECISION_FLOOR, (
        f"Precision {precision:.3f} below floor {PRECISION_FLOOR}"
    )
    assert recall >= RECALL_FLOOR, (
        f"Recall {recall:.3f} below floor {RECALL_FLOOR}"
    )
