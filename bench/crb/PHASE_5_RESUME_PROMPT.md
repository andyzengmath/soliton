# Phase 5 resume prompt — Soliton CRB, post-diagnostic (2026-04-20)

Drop this fenced block into a fresh Claude Code session to continue from where the previous session ended. Self-contained: required reading, three pick-one goals, operational rules, anti-goals, and budgets.

---

```text
Resume session: Soliton Phase 5 — or alternate direction post-diagnostic.

Current state (verify with `git log --oneline -5`):
  - Repo: andyzengmath/soliton. main HEAD around commit 931656c (docs(bench): CRB diagnostic memo).
  - 0 open PRs.
  - Prior session summary:
    * Phase 4b shipped (PR #26, d7ddfd0) — lib/hallucination-ast/ Python package passes Khati
      2026 corpus at F1=0.968 standalone.
    * Phase 4c (50-PR CRB run against 4a+4b) — F1=0.261, close per pre-reg.
    * Phase 4c.1 (4a alone isolation) — F1=0.278, neutral vs Phase 3.5.
    * Phase 3.5.1 (per-language nitpicks gate) — F1=0.243, close. SKILL.md edit
      reverted (not on main).
    * Phase 4 agent integrations REVERTED (PR #28, d85e2af) — agents/hallucination.md
      and agents/correctness.md back to pre-PR-#24 state. skills/pr-review/cross-file-retrieval.md
      deleted. lib/hallucination-ast/ package retained on-tree.
    * Diagnostic memo at bench/crb/DIAGNOSTIC.md identifies over-emission (9.5 cands/PR
      vs leader's 2.9-6.4) as the structural F1 ceiling.
  - Cumulative ~$420 spent across 3 CRB runs since Phase 3.5, no net F1 improvement.
  - Phase 3.5's F1=0.277 remains Soliton's CRB number of record.

Required reading at session start, IN ORDER (auto-memory loads MEMORY.md first, which
indexes everything below):

  1. MEMORY.md — session-context summary of Phase 3/3.5/4c/4c.1/3.5.1 + revert decisions.
  2. bench/crb/DIAGNOSTIC.md — the memo. Read FULLY. The Phase 5 proposal + ship criteria
     are pre-registered here.
  3. bench/crb/RESULTS.md — skim Phase 3.5, Phase 4c, Phase 4c.1, Phase 3.5.1 sections
     for full numbers + per-language breakdowns.
  4. skills/pr-review/SKILL.md — current orchestrator (Phase 3.5 state). Specifically
     Step 5 (Synthesis) and Step 6 Format A (Markdown render) — that's where the Top-K
     filter would hook if Goal A is picked.
  5. agents/synthesizer.md — the synthesizer's current dedup + sort-by-severity-then-
     confidence pass. Top-K filter lives here conceptually but SKILL.md owns the
     render decision.

Then pick ONE of three goals. Do NOT attempt multiple in the same session — each one
is ~3-5 hours of wall clock plus $0-$150.

=============================================================================
GOAL A · Phase 5 "Top-K filter" experiment (~$140, ~1 hr wall clock)
=============================================================================

Hypothesis: capping post-synthesis findings at K=3-5 per PR will close ~50% of the
F1 gap to claude-code (0.330) since the diagnostic shows Soliton emits 3x the leader's
candidate volume at every percentile.

Concrete deliverables:

  A1. Add top-K filter logic to skills/pr-review/SKILL.md Step 5 output path, OR to
      agents/synthesizer.md Step 3 filtering. Minimum code footprint wins:
        - single conditional block (no prose rationale inline — Phase 3.5.1 showed
          ~40 lines of v2.1 rationale prose in SKILL.md Format A REGRESSED non-TS
          F1 by biasing agents toward more-verbose emission)
        - config key `max_findings_per_pr` default 5, overridable via
          .claude/soliton.local.md frontmatter
        - filter logic: retain ALL criticals (cap 3), then top (K - |criticals|)
          improvements by confidence descending, drop the rest to JSON-only output

  A2. Scripts mirroring the pattern from Phase 4c.1 / 3.5.1 (see bench/crb/
      dispatch-phase4c1.sh and bench/crb/run-phase4c1-pipeline.sh):
        - bench/crb/dispatch-phase5.sh  (writes to bench/crb/phase5-reviews/)
        - bench/crb/run-phase5-pipeline.sh  (same Azure OpenAI gpt-5.2 judge as 3.5)
      Add phase5-reviews/ to .gitignore (see existing phase4c/phase4c1/phase3_5_1 entries).

  A3. Smoke test ONE PR with max K=5 to confirm:
        - Nitpicks still dropped from markdown (Phase 3.5 behavior retained)
        - Critical findings preserved
        - Improvement findings capped at K - |criticals|
      Use calcom/cal.com#10967 (TS) or getsentry/sentry#93824 (Python) as the smoke.

  A4. Full 50-PR dispatch with CONCURRENCY=3, MAX_BUDGET_USD=10 per review. Monitor via
      the dispatch script's line count. Expected wall clock: 30-45 min.

  A5. Judge pipeline run (~3 min, ~$15 Azure OpenAI).

  A6. Write up bench/crb/RESULTS.md § Phase 5 with the same structure as Phase 3.5
      and later: headline metrics, per-language breakdown vs Phase 3.5 baseline, ship/
      hold/close verdict, cost tally, reproduction. Include per-K ablation if the
      first K value lands in the hold band.

  A7. If SHIP (F1 >= 0.30 AND no lang reg > 0.03): open PR, ask user to merge, update
      MEMORY.md to note Phase 5 as new Soliton CRB number of record.
      If HOLD (0.28-0.30): close PR but commit the writeup via a docs PR (matches the
      Phase 4c.1 / 3.5.1 pattern). Propose Phase 5.1 with different K.
      If CLOSE (<0.28): close PR, commit writeup via docs PR, note that over-emission
      theory is partially wrong; next direction is per-agent quality, not volume.

Phase 5 pre-registered ship criteria (from DIAGNOSTIC.md):
  ship   : aggregate F1 >= 0.30 AND TS F1 >= 0.25 AND no lang reg > 0.03
  hold   : 0.28 <= F1 <= 0.30 — ship if per-language allows
  close  : F1 < 0.28 → verbosity theory wrong; next direction is agent-level quality

Known failure modes to watch for:
  - Many of the top 9.5 candidates may be TPs the judge splits across multiple
    goldens → cutting to K=5 loses recall faster than it trims FPs
  - Different PRs legitimately need different K (large diffs may have 7+ real issues)
  - Severity assignment might be miscalibrated → "top K by sev+conf" still pushes
    FPs past TPs

If Phase 5 closes, DO NOT retry with a larger K blindly. The per-PR-adaptive K
experiment or judge-calibrated reranker is the principled follow-up — propose those
and wait for user direction.

=============================================================================
GOAL B · Submit Phase 3.5 to Martian CRB leaderboard (~$0, ~1 hr wall clock)
=============================================================================

Bank the 0.277 number. Move to non-benchmark ROADMAP work.

  B1. Read bench/crb/README.md § "Phase 4 — upstream submission" for the leaderboard
      submission checklist from the prior session's plan.
  B2. Follow withmartian/code-review-benchmark's "Adding a new tool" process
      (README of that repo). Typically:
        - Fork the benchmark repo
        - Add soliton to the evaluated-tools table in their README
        - Include the F1=0.277 number under GPT-5.2 judge, the review methodology,
          and a pointer to our bench/crb/RESULTS.md § Phase 3.5 for full numbers
  B3. Open the upstream PR, return the URL to the operator.
  B4. Update MEMORY.md: Phase 3.5 submitted to Martian leaderboard on <date>, URL.

Benefits: locks in the number competitively; frees up Soliton work for non-CRB
features (ROADMAP items beyond CRB — the operator's next initiative per idea-stage/).

Caveat: once submitted, the number is public. Only do this if the operator is ready
to accept the 0.277 publicly.

=============================================================================
GOAL C · Investigate without running (~$0, ~2 hr wall clock)
=============================================================================

Deeper diagnostic than DIAGNOSTIC.md. Two strands:

  C1. Per-agent F1 attribution (blocked in last session by CRB step2 rewriting
      candidate text). Solve this by enriching Soliton's rendered findings with
      trailing metadata tags (e.g. <!-- agent=correctness cat=correctness conf=90 -->)
      that step2 can preserve. Then re-run a Phase 3.5-equivalent 50-PR run and
      produce per-agent TP/FP tables. ~$140 for the re-run.

  C2. Manual review-quality audit. Sample 10 Phase 3.5 reviews + their golden
      comments + the claude-code / qodo reviews of the same PRs from the CRB repo.
      Read line-by-line. Score each Soliton finding against its apparent golden
      counterpart: "did we describe the SAME issue claude-code described?", "did
      we find something claude-code missed?", "did we flag noise?". Produces a
      qualitative pattern list that may reveal a non-quantitative failure mode
      (e.g., Soliton's descriptions are too long and the judge can't match them
      to goldens even when semantically identical).

  C3. Combine C1 + C2 into a new memo bench/crb/DIAGNOSTIC_V2.md with a
      concrete ranked list of 3 next experiments by expected F1 lift.

C is the highest-leverage option if the operator is willing to spend 2 hours
reading without running an experiment.

=============================================================================
Operational rules (carry over from prior session)
=============================================================================

  - Confirm before any gh pr merge on PRs you open.
  - Confirm before any spend > $5. Phase 5 (~$140) and Goal C1 (~$140) both
    cross this; the user has pre-authorized these IF the prior diagnostic
    memo's proposal is followed.
  - Use the git workflow from prior sessions: branch from main, commit
    thematically, push, open PR, await user confirmation to merge.
  - gh pr edit may be blocked on this repo by a GitHub Projects-classic API bug.
    If you hit it, post a comment on the PR with corrections rather than
    retrying.

=============================================================================
What NOT to do (memory-backed)
=============================================================================

  - Do NOT retry v2.2 description compression or v2.3 synthesizer dedup (Phase 3.6,
    3.7 — both falsified as negative experiments).
  - Do NOT rewire agents/hallucination.md §2.5 or §2 NOT_FOUND_IN_TREE handoff
    without isolation evidence (Phase 4c + 4c.1 together falsified the hypothesis
    that the L5 + 4b combination helps aggregate F1).
  - Do NOT blindly bring back nitpicks globally (Phase 3.5.1 showed the TS gate
    mechanism worked but the rationale prose regressed non-TS). If Goal A picks
    per-language K, keep code comments TERSE.
  - Do NOT run new experiments without a pre-registered ship/hold/close criterion.
    Past three runs stayed disciplined on this; keep the pattern.
  - Do NOT run CRB pipeline again unless Goal A or C1 is actively in motion.
    Each run is ~$140; the current session's operator was thoughtful about
    keeping total spend bounded.

=============================================================================
What I want in writing back early in the new session (after reading the 5 docs
above)
=============================================================================

  - Which Goal (A/B/C) the session will pursue.
  - For Goal A: the exact top-K filter design (file to edit, one-line summary of
    the conditional). Verify NO inline rationale prose (Phase 3.5.1 lesson).
  - For Goal B: status of CRB leaderboard's "Adding a new tool" process + any
    prerequisites the prior session missed.
  - For Goal C: which strand (C1 hardening, C2 manual audit, or both).
  - Any ambiguity in this prompt that needs clarification before diverging.

If the operator picks something other than A/B/C (e.g., "skip CRB work entirely,
move to ROADMAP item X"), honor that and read the relevant ROADMAP.md section
instead of DIAGNOSTIC.md.
```

---

**File lives at:** `bench/crb/PHASE_5_RESUME_PROMPT.md` (committed on main).

**Use:** paste the fenced block above into a fresh Claude Code session. Auto-memory will load `MEMORY.md` and the rest of the required-reading list is pulled on session start.
