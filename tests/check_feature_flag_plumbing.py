#!/usr/bin/env python3
"""Feature-flag plumbing regression check.

Closes NEW-1 from the 2026-05-01 second-pass strategic audit. PRs #107 and
#108 demonstrated that adding a new feature flag requires changes in BOTH:

  - Step 2 in `skills/pr-review/SKILL.md` (YAML -> `config.*` mapping)
  - At least one downstream consumer in SKILL.md (Step 4.1 dispatch
    decision OR Step 4.1 step 6 / Step 4.2 prompt pass-through)

If either is missing, the flag is dead code at runtime: PR #107 caught a
case where Step 2 was missing (config field undefined); PR #108 caught a
case where Step 2 was correct but Step 4.2 never passed `config` to the
agent (so the agent could not read the flag). Both bugs would have wasted
the Phase 6b $140 CRB measurement run.

This script asserts both halves are wired for every flag declared in
`templates/soliton.local.md`. CI-friendly: exit 0 on PASS, exit 1 on FAIL.

Usage:
    python tests/check_feature_flag_plumbing.py

Output: one line per flag with PASS / FAIL + reason.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "templates" / "soliton.local.md"
SKILL_MD = REPO_ROOT / "skills" / "pr-review" / "SKILL.md"


def extract_flag_keys() -> list[str]:
    """Return sorted unique `agents.<name>.enabled` keys from the template.

    Matches both the commented YAML config block and the prose documentation
    bullets (e.g., `**agents.silent_failure.enabled**: ...`).
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    matches = re.findall(r"agents\.(\w+)\.enabled", text)
    return sorted(set(matches))


def check_plumbing(flag: str, skill_md_text: str) -> tuple[bool, str]:
    """Check both Step 2 mapping and downstream consumption.

    Returns (ok, reason).
    """
    # Step 2 mapping pattern: `agents.<name>.enabled -> config.agents.<name>.enabled`.
    # NOTE: this is a content-based grep, not position-anchored. If a future
    # contributor adds prose elsewhere in SKILL.md that contains both
    # "agents.<flag>.enabled" and "config.agents.<flag>.enabled" on the same
    # line connected by an arrow ("->"), the check will pass even though the
    # actual Step 2 mapping might be missing. The current SKILL.md structure
    # makes such a false-positive unlikely (the only Step 2 mapping lines live
    # in the v2 feature-flag fields list), but the limitation is acknowledged.
    step2_pattern = (
        re.escape(f"agents.{flag}.enabled")
        + r".*->.*"
        + re.escape(f"config.agents.{flag}.enabled")
    )
    has_step2 = bool(re.search(step2_pattern, skill_md_text))

    # Downstream consumption: count references to either:
    #   - config.agents.<flag>.enabled (orchestrator reads from config)
    #   - <flag>_enabled (resolved annotation passed to agent prompt, e.g. from Step 4.1 step 6)
    # \b word boundaries on the annotation regex prevent false-positive
    # substring matches when one flag name is a suffix of another (e.g.,
    # avoiding `file_enabled` matching as a substring of `cross_file_enabled`).
    config_refs = len(
        re.findall(re.escape(f"config.agents.{flag}.enabled"), skill_md_text)
    )
    annotation_refs = len(
        re.findall(rf"\b{re.escape(flag)}_enabled\b", skill_md_text)
    )

    # Step 2 mapping line itself contains config.agents.<flag>.enabled.
    # Require at least one ADDITIONAL config reference OR any annotation reference.
    has_downstream = (config_refs >= 2) or (annotation_refs >= 1)

    if has_step2 and has_downstream:
        return True, (
            f"Step 2 mapped + downstream consumer found "
            f"(config refs={config_refs}, annotation refs={annotation_refs})"
        )
    if has_step2 and not has_downstream:
        return False, (
            "Step 2 mapped but NO downstream consumer in SKILL.md — flag is "
            "parsed into config but never read. Add either a Step 4.1 "
            "content-triggered dispatch decision OR a Step 4.1 step 6 / "
            "Step 4.2 prompt pass-through."
        )
    if not has_step2 and has_downstream:
        return False, (
            "Downstream reference(s) exist but Step 2 mapping missing — "
            "`config.agents."
            + flag
            + ".enabled` will be undefined at runtime. Add the Step 2 mapping "
            "line under SKILL.md '**Nested v2 feature-flag fields**' (mirroring "
            "the silent_failure / comment_accuracy / cross_file_retrieval_java "
            "entries)."
        )
    return False, (
        "No Step 2 mapping AND no downstream reference in SKILL.md — flag is "
        "fully dead. The template advertises an opt-in path that the "
        "orchestrator never honors."
    )


def find_orphan_skill_md_flags(template_flags: list[str], skill_md_text: str) -> list[str]:
    """Return flag names referenced in SKILL.md but absent from the template.

    Catches the inverse failure mode of `check_plumbing`: SKILL.md wires a
    `config.agents.<name>.enabled` consumer or maps a `<name>_enabled`
    annotation, but `templates/soliton.local.md` doesn't expose the flag to
    integrators. Reported as WARN (informational), not FAIL — orphaned wiring
    is a discoverability defect, not a runtime bug.
    """
    referenced = set(re.findall(r"config\.agents\.(\w+)\.enabled", skill_md_text))
    template_set = set(template_flags)
    return sorted(referenced - template_set)


def main() -> int:
    if not TEMPLATE.is_file():
        print(f"error: template not found at {TEMPLATE}", file=sys.stderr)
        return 2
    if not SKILL_MD.is_file():
        print(f"error: SKILL.md not found at {SKILL_MD}", file=sys.stderr)
        return 2

    flags = extract_flag_keys()
    if not flags:
        print(
            "warn: no agents.*.enabled keys found in templates/soliton.local.md",
            file=sys.stderr,
        )
        return 0

    skill_md_text = SKILL_MD.read_text(encoding="utf-8")

    print(
        f"# Feature-flag plumbing check ({len(flags)} flag(s) declared "
        f"in templates/soliton.local.md)"
    )
    failures = 0
    for flag in flags:
        ok, reason = check_plumbing(flag, skill_md_text)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] agents.{flag}.enabled: {reason}")
        if not ok:
            failures += 1

    # Reverse scan: surface any flags wired in SKILL.md but missing from the
    # template (orphaned wiring → discoverability defect, WARN-only). Closes
    # the third MEDIUM follow-up from the code-reviewer pass on PR #113.
    orphans = find_orphan_skill_md_flags(flags, skill_md_text)
    if orphans:
        print()
        print(
            f"# Orphaned wiring check ({len(orphans)} flag(s) in SKILL.md but "
            f"NOT in templates/soliton.local.md)"
        )
        for orphan in orphans:
            print(
                f"  [WARN] agents.{orphan}.enabled: referenced in SKILL.md "
                f"(\\`config.agents.{orphan}.enabled\\`) but not exposed to "
                f"integrators via the template. Add a stanza under the v2 "
                f"feature-flag block in templates/soliton.local.md so "
                f"integrators can opt in."
            )

    print()
    if failures:
        print(f"FAILED ({failures} of {len(flags)} flag(s) lack required plumbing)")
        print(
            "Without these, the corresponding feature is dead code at runtime. "
            "See PRs #107, #108 for the historical regressions this check guards "
            "against."
        )
        return 1
    if orphans:
        print(
            f"OK (with {len(orphans)} orphaned-wiring warning(s)) — all "
            f"{len(flags)} template flag(s) have both Step 2 mapping and "
            f"downstream consumer"
        )
        return 0
    print(f"OK — all {len(flags)} feature flag(s) have both Step 2 mapping and downstream consumer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
