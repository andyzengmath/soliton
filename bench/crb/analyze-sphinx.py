#!/usr/bin/env python3
"""bench/crb/analyze-sphinx.py

Analyze Sphinx actionability re-judge results (per
bench/crb/sphinx-actionability-spec.md). Reads the sibling-repo
evaluations_sphinx.json produced by run-sphinx-actionability.sh and reports:

  - actionable_TP_rate (TP-actionable / TP_rated)
  - per-severity actionability breakdown
  - per-PR actionability sample (one bullet per PR)
  - interpretation against pre-registered bands

Output is plain text suitable for pasting into bench/crb/RESULTS.md.

Usage:
  python3 bench/crb/analyze-sphinx.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_ARCHIVE = Path(__file__).resolve().parent / "sphinx-evaluations-phase5_2.json"
SIBLING_LIVE = REPO_ROOT.parent / "code-review-benchmark" / "offline" / "results" / "azure_gpt-5.2" / "evaluations_sphinx.json"
# Prefer the in-repo archive (reproducible from Soliton alone). Fall back to the
# sibling-repo live results when re-running the pipeline before archiving.
DEFAULT_EVALS_PATH = LOCAL_ARCHIVE if LOCAL_ARCHIVE.exists() else SIBLING_LIVE


def interpret(rate: float) -> tuple[str, str]:
    """Return (band, action) per the pre-registered spec interpretation table."""
    if rate >= 0.70:
        return ("HIGH", "cite alongside F1 in publishable narrative")
    if rate >= 0.50:
        return ("MIXED", "concrete-prompt experiment is a candidate (~$140 CRB run)")
    return ("LOW", "re-evaluate F1 meaningfulness; may need stronger judge filter")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evals",
        type=Path,
        default=DEFAULT_EVALS_PATH,
        help=f"Path to evaluations_sphinx.json (default: {DEFAULT_EVALS_PATH})",
    )
    parser.add_argument("--tool", default="soliton", help="Tool to analyze (default: soliton)")
    args = parser.parse_args()

    if not args.evals.exists():
        print(f"error: {args.evals} not found", file=sys.stderr)
        print("       run bash bench/crb/run-sphinx-actionability.sh first", file=sys.stderr)
        return 1

    with open(args.evals) as f:
        evals = json.load(f)

    actionability = Counter()
    severity_act: dict[str, Counter] = {}
    per_pr_samples: list[tuple[str, str, str, str]] = []  # (pr_url, severity, rating, reason)

    aggregate_tp = aggregate_fp = aggregate_fn = 0

    for pr_url, tools in evals.items():
        for tool, result in tools.items():
            if tool != args.tool or result.get("skipped"):
                continue
            aggregate_tp += result.get("tp", 0)
            aggregate_fp += result.get("fp", 0)
            aggregate_fn += result.get("fn", 0)
            for tp in result.get("true_positives", []):
                rating = tp.get("actionability") or "unrated"
                severity = tp.get("severity") or "unknown"
                actionability[rating] += 1
                severity_act.setdefault(severity, Counter())[rating] += 1
                per_pr_samples.append(
                    (
                        pr_url,
                        severity,
                        rating,
                        (tp.get("actionability_reason") or "").replace("\n", " ").strip(),
                    )
                )

    if not per_pr_samples:
        print("error: no Soliton TPs found in evaluations file", file=sys.stderr)
        return 1

    rated = (
        actionability.get("actionable", 0)
        + actionability.get("non_actionable", 0)
        + actionability.get("uncertain", 0)
    )
    rate = actionability.get("actionable", 0) / rated if rated > 0 else 0.0
    band, action = interpret(rate)

    precision = aggregate_tp / (aggregate_tp + aggregate_fp) if (aggregate_tp + aggregate_fp) else 0
    recall = aggregate_tp / (aggregate_tp + aggregate_fn) if (aggregate_tp + aggregate_fn) else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    # Header block
    print("=" * 70)
    print("Sphinx actionability — Phase 5.2 corpus, gpt-5.2 judge")
    print("=" * 70)
    print(f"F1 (this run, sphinx prompt): {f1:.3f}  (P={precision:.3f}, R={recall:.3f})")
    print(f"Total TPs                   : {aggregate_tp}")
    print(f"  actionable                : {actionability.get('actionable', 0)}")
    print(f"  non_actionable            : {actionability.get('non_actionable', 0)}")
    print(f"  uncertain                 : {actionability.get('uncertain', 0)}")
    print(f"  unrated (judge omission)  : {actionability.get('unrated', 0)}")
    print()
    print(f"actionable_TP_rate          : {rate:.1%}  ->  {band}  ->  {action}")
    print()

    # Per-severity table
    print("Per-severity breakdown:")
    print(f"  {'severity':<14} {'TP':>4} {'act':>4} {'non':>4} {'unc':>4} {'rate':>6}")
    for severity in sorted(severity_act.keys()):
        c = severity_act[severity]
        total = sum(c.values())
        sev_rated = c.get("actionable", 0) + c.get("non_actionable", 0) + c.get("uncertain", 0)
        sev_rate = c.get("actionable", 0) / sev_rated if sev_rated > 0 else 0
        print(
            f"  {severity:<14} {total:>4} "
            f"{c.get('actionable', 0):>4} {c.get('non_actionable', 0):>4} "
            f"{c.get('uncertain', 0):>4} {sev_rate:>6.1%}"
        )
    print()

    # Per-PR samples (first 10 non-actionable, for FP-quality inspection)
    non_actionable_samples = [
        (pr, sev, reason) for pr, sev, rating, reason in per_pr_samples if rating == "non_actionable"
    ][:10]
    if non_actionable_samples:
        print(f"Sample non-actionable TPs (first {len(non_actionable_samples)}):")
        for pr, sev, reason in non_actionable_samples:
            pr_short = pr.split("/")[-3] + "#" + pr.split("/")[-1] if "/" in pr else pr
            print(f"  - [{sev}] {pr_short}: {reason[:120]}")
        print()

    # Interpretation footer
    print("Pre-registered interpretation bands (per spec):")
    print("  rate >= 70%      -> HIGH actionability  (cite alongside F1)")
    print("  50% <= rate < 70% -> MIXED              (concrete-prompt expt candidate)")
    print("  rate < 50%       -> LOW                 (re-evaluate F1 meaningfulness)")
    print()
    print(f"Verdict: {band} -> {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
