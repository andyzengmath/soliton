#!/usr/bin/env python3
"""Soliton fixture runner — partial automation closing the structural and
hallucination-ast halves of POST_V2_FOLLOWUPS §G2.

Two modes:

    --mode structural   (default; free, no API)
        Walks tests/fixtures/* and asserts each fixture directory contains
        diff.patch + expected.json. Validates expected.json against the
        schema documented in tests/run-fixtures.md. Catches malformed or
        partially-committed fixtures at PR time.

    --mode phase4b
        For each fixture whose expected.json has a `phase4bExpected` block,
        runs the hallucination-ast CLI on its diff.patch and asserts the
        emitted finding matches `rule`, `symbol`, and (when present)
        `suggestedFix`/`confidence`. Currently covers hallucinated-import
        and signature-mismatch fixtures — both Python-only.

The full /pr-review-driven integration (asserting risk ranges, finding
counts, severity bands) requires Anthropic API auth in CI; that half of
§G2 stays manual until ANTHROPIC_API_KEY (or the OAuth-token equivalent)
lands in repo secrets.

Exit code 0 when all selected assertions pass, 1 on any failure. CI-friendly
output: one line per fixture with PASS / FAIL + reason.

Usage:
    python tests/run_fixtures.py
    python tests/run_fixtures.py --mode phase4b
    python tests/run_fixtures.py --mode structural --fixtures-root tests/fixtures
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REQUIRED_TOP_FIELDS = {"riskRange", "expectedFindings", "expectedCategories", "description"}
ALLOWED_SEVERITIES = {"critical", "improvement", "nitpick", "(none)"}


def validate_structural(fixture_dir: Path) -> tuple[bool, str]:
    """Validate fixture directory has required files + expected.json schema.
    Returns (ok, reason)."""
    diff = fixture_dir / "diff.patch"
    if not diff.exists():
        return False, "missing diff.patch"
    if diff.stat().st_size == 0:
        return False, "diff.patch is empty"

    expected_path = fixture_dir / "expected.json"
    if not expected_path.exists():
        return False, "missing expected.json"

    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"expected.json invalid JSON: {e}"

    missing = REQUIRED_TOP_FIELDS - expected.keys()
    if missing:
        return False, f"expected.json missing required fields: {sorted(missing)}"

    rr = expected["riskRange"]
    if not (isinstance(rr, list) and len(rr) == 2 and all(isinstance(x, (int, float)) for x in rr)):
        return False, "riskRange must be [min, max] numeric"
    if rr[0] > rr[1]:
        return False, f"riskRange[0]={rr[0]} > riskRange[1]={rr[1]}"
    if not (0 <= rr[0] <= 100 and 0 <= rr[1] <= 100):
        return False, f"riskRange values must be in [0, 100]; got {rr}"

    if not isinstance(expected["expectedFindings"], int) or expected["expectedFindings"] < 0:
        return False, "expectedFindings must be a non-negative int"
    if not isinstance(expected["expectedCategories"], list):
        return False, "expectedCategories must be a list"

    # expectedSeverity is required when expectedFindings > 0; optional/None for
    # zero-finding fixtures (e.g. trivial-readme-fix, tier0-clean).
    if expected["expectedFindings"] > 0 and "expectedSeverity" not in expected:
        return False, "expectedSeverity required when expectedFindings > 0"
    if "expectedSeverity" in expected:
        sev = expected["expectedSeverity"]
        if isinstance(sev, str):
            if sev not in ALLOWED_SEVERITIES:
                return False, f"expectedSeverity {sev!r} not in {sorted(ALLOWED_SEVERITIES)}"
        elif sev is None:
            pass  # explicit null is allowed for fixtures with 0 findings
        else:
            return False, f"expectedSeverity must be str or null; got {type(sev).__name__}"

    # Tier-0 v2 fixtures may carry tier0Verdict, llmSwarmSkipped, blockReason,
    # confidenceThresholdBumpedTo, expectedTier0FindingCategory. These are
    # asserted by the full /pr-review runner (not in this stub); just light
    # type-check here.
    if "tier0Verdict" in expected:
        if expected["tier0Verdict"] not in {"clean", "blocked", "advisory_only", "needs_llm"}:
            return False, f"tier0Verdict {expected['tier0Verdict']!r} invalid"
    if "llmSwarmSkipped" in expected and not isinstance(expected["llmSwarmSkipped"], bool):
        return False, "llmSwarmSkipped must be bool"

    # phase4b block (validated more deeply in --mode phase4b)
    if "phase4bExpected" in expected:
        p4 = expected["phase4bExpected"]
        if not isinstance(p4, dict):
            return False, "phase4bExpected must be a dict"
        if "rule" not in p4 or "symbol" not in p4:
            return False, "phase4bExpected missing rule or symbol"

    return True, f"diff={diff.stat().st_size}B, fields={len(expected)}"


def validate_phase4b(fixture_dir: Path, repo_root: Path) -> tuple[bool, str]:
    """Run hallucination-ast CLI on the fixture diff and assert findings
    match `phase4bExpected`. Returns (ok, reason)."""
    expected = json.loads((fixture_dir / "expected.json").read_text(encoding="utf-8"))
    p4 = expected.get("phase4bExpected")
    if p4 is None:
        return True, "no phase4bExpected (skipped)"

    diff = fixture_dir / "diff.patch"
    proc = subprocess.run(
        [sys.executable, "-m", "hallucination_ast", "--diff", str(diff)],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    # CLI exits 1 by design when CRITICAL findings are emitted (fail-loud CI
    # convention). We parse stdout regardless of exit code; if stdout isn't
    # valid JSON, treat that as the actual failure.
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return False, (f"CLI exit {proc.returncode}, stdout not JSON: {e} "
                       f"(head: {proc.stdout[:120]!r}; stderr: {proc.stderr[:200]!r})")

    findings = out.get("findings", [])
    matches = [f for f in findings
               if f.get("rule") == p4["rule"] and f.get("symbol") == p4["symbol"]]
    if not matches:
        return False, (f"no finding with rule={p4['rule']!r} symbol={p4['symbol']!r}; "
                       f"got {[(f.get('rule'), f.get('symbol')) for f in findings]}")

    f = matches[0]
    if "confidence" in p4 and f.get("confidence") != p4["confidence"]:
        return False, f"confidence mismatch: expected {p4['confidence']}, got {f.get('confidence')}"
    if "suggestedFix" in p4 and f.get("suggestedFix") != p4["suggestedFix"]:
        return False, f"suggestedFix mismatch: expected {p4['suggestedFix']!r}, got {f.get('suggestedFix')!r}"

    return True, f"matched rule={p4['rule']} confidence={f.get('confidence')}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Soliton fixture runner (structural + phase4b)")
    parser.add_argument("--mode", choices=["structural", "phase4b", "all"], default="all",
                        help="structural: file/schema check; phase4b: hallucination-ast CLI; "
                             "all: both (default).")
    parser.add_argument("--fixtures-root", type=Path,
                        default=Path(__file__).parent / "fixtures",
                        help="Path to fixtures dir.")
    args = parser.parse_args()

    if not args.fixtures_root.is_dir():
        print(f"error: fixtures root not found: {args.fixtures_root}", file=sys.stderr)
        return 2

    fixtures = sorted(d for d in args.fixtures_root.iterdir() if d.is_dir())
    repo_root = args.fixtures_root.parent.parent
    failures = 0

    if args.mode in ("structural", "all"):
        print(f"# Structural validation ({len(fixtures)} fixtures)")
        for f in fixtures:
            ok, reason = validate_structural(f)
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {f.name}: {reason}")
            if not ok:
                failures += 1
        print()

    if args.mode in ("phase4b", "all"):
        phase4b_fixtures = []
        for f in fixtures:
            try:
                exp = json.loads((f / "expected.json").read_text(encoding="utf-8"))
                if "phase4bExpected" in exp:
                    phase4b_fixtures.append(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass

        print(f"# Phase 4b CLI validation ({len(phase4b_fixtures)} fixtures)")
        for f in phase4b_fixtures:
            ok, reason = validate_phase4b(f, repo_root)
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {f.name}: {reason}")
            if not ok:
                failures += 1
        print()

    if failures:
        print(f"FAILED ({failures} failures)")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
