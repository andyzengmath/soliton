# Phase 6 · Java-only L5 cross-file retrieval (clean re-integration)

**Design doc**, not an implementation. Drafted 2026-05-01 as the prep work for Strategic Option B from the 2026-05-01 audit. Pre-experiment scaffold so the $140 CRB re-run can launch quickly when authorized.

Phase 4c (PR #27) and Phase 4c.1 (PR #29) both CLOSED. Phase 4c.1 isolated L5 retrieval without §2.5 hallucination-AST integration → aggregate F1=0.278, **Java F1=0.329 (+0.046 vs Phase 3.5 at 2.6σ_lang)**, but Go regressed −0.112 due to the `§2 NOT_FOUND_IN_TREE` suppression rule baked into 4a (not L5 itself). Phase 6 isolates the L5 mechanism for Java only and removes the NOT_FOUND_IN_TREE suppression that drove the Go regression.

## Alignment with existing specs (no duplication)

| Existing artifact | Role | What Phase 6 takes / leaves |
|---|---|---|
| `skills/pr-review/cross-file-retrieval.md` | Reverted by Phase 4-close revert (PR #28). Not on main. | **Phase 6 reintroduces a narrower version**: Java-only patterns, no NOT_FOUND_IN_TREE suppression rule, no agent §2.5 pre-check wiring. |
| `lib/hallucination-ast/` | Standalone Khati-validated package (F1=0.968 standalone). Agent §2.5 integration was reverted by PR #28. | **Phase 6 leaves this untouched.** lib stays standalone; no §2.5 re-integration. Phase 5.3 evidence already showed §2.5 + cross-file-impact graphSignals together regressed F1 by 0.045. |
| `agents/hallucination.md` | Pure-LLM hallucination agent (post-revert state). | **Phase 6 does not modify.** §2.5 stays out. |
| `agents/correctness.md` | Pure-LLM correctness agent (post-revert state). | **Phase 6 adds an opt-in skill reference**: when diff touches Java files AND `--phase6-l5-java` flag set, agent calls the cross-file-retrieval skill. Otherwise no change. |
| Default skipAgents `['test-quality', 'consistency']` | Phase 5 attribution baseline. | **Phase 6 inherits as-is.** |

## Hypothesis

**Phase 4c.1's Java +0.046 was real signal at 2.6σ_lang** (σ_lang=0.018 at n=10). The mechanism: Java agents see the cross-file-retrieved definition of class signatures + interface contracts, which lets them flag actual cross-file Java semantic bugs (override signature drift, interface contract violations) that pure-LLM-from-diff misses.

**Go regression in Phase 4c.1 was caused by NOT_FOUND_IN_TREE suppression baked into 4a** (the rule that suppressed agent findings when the symbol couldn't be resolved cross-file). Go's cross-file extraction patterns produced higher 0-hit rates than Java/TS, suppressing valid findings. Removing this suppression lets the Go agent emit its findings normally; the L5 mechanism only ADDS context, never SUPPRESSES.

## Two-component design

### Component A · Java-only retrieval helper (narrower than Phase 4a)

New file: `skills/pr-review/cross-file-retrieval.md` (~80 lines, simpler than the 175-line Phase 4a version).

**Inputs**: diff, files list, ReviewConfig.

**Pipeline (Java only, behind opt-in flag)**:
```
1. java-symbol-extract
     Parse the diff for Java CALLEE symbols: method calls, type references,
     interface implementations, override declarations.
     Heuristic patterns:
       - <type>\\.<method>\\(  → method call
       - implements <interface>  → interface contract
       - @Override on <method>   → override signature
     Filter: builtins (java.util.*, java.lang.*), self-defined, test scope.

2. resolve-java-definitions (no fallback / no suppression)
     For each priority symbol, run:
       git grep -E "(class|interface) <sym>\\b"  OR
       git grep -E "(public|private|protected)?\\s*\\w+\\s+<sym>\\s*\\("
     If 0 hits, SKIP (do not emit suppression block; agent reasons normally).
     If ≥1 hits, Read top match ±15 lines.

3. attach-as-context
     Emit CROSS_FILE_CONTEXT block (same format as Phase 4a). Agents
     instructed to treat this as REFERENCE ONLY, not review target.
```

**Crucial difference from Phase 4a**: NO `NOT_FOUND_IN_TREE` rule. No suppression of agent findings on resolve-failure. The L5 mechanism is purely additive.

### Component B · Agent integration (Java-only, behind flag)

`agents/correctness.md` gets ONE conditional block:

```
If diff contains *.java files AND --phase6-l5-java flag is active:
  Call skills/pr-review/cross-file-retrieval.md to populate
  CROSS_FILE_CONTEXT for the Java symbols in scope.
Else: no-op (Phase 5.2 behavior).
```

No edits to `agents/hallucination.md`. No edits to `agents/cross-file-impact.md`. Java-only scoping isolates the experiment cleanly.

## Realistic F1 estimates (post-Phase-4c.1 calibration)

| Lever | Mechanism | Phase 4c.1 evidence | σ-calibrated estimate |
|---|---|---:|---:|
| Java-only L5 retrieval | Recall: catch cross-file Java semantic bugs | Java F1: +0.046 at 2.6σ_lang | **+0.009 aggregate F1** (10/50 weight × +0.046) |
| Removal of NOT_FOUND_IN_TREE | Restores Go agent findings to pure-LLM baseline | Go F1: −0.112 attributable per Phase 4c.1 isolation | **+0.022 aggregate F1** (10/50 weight × +0.112) IF the suppression was indeed the Go regression driver |
| **Stacked combined (best case)** | | | **Phase 5.2 + 0.031 = ~0.344** |
| **Stacked combined (Java-recovery only)** | | | **Phase 5.2 + 0.009 = ~0.322** |

**Discount rationale**: per-language n=10 in the CRB corpus means single-experiment Δ has σ_lang=0.018. Phase 4c.1's Java +0.046 was at 2.6σ — real but not impossible to fail to replicate. Use the mid-range projection (~0.322) as the working estimate.

**Phase 6 target F1**: 0.317 to 0.330. Modest by design — this is a recovery experiment, not a structural breakthrough.

## σ-aware ship criteria (per A4 doctrine)

σ_F1 = 0.0086 (within-run noise envelope, PR #48 calibration).
σ_Δ paired ≈ 0.0122 (between-run difference noise).

| Outcome | Aggregate F1 | Java F1 | Per-language regression > 2σ_lang (0.036) | Action |
|---|---:|---:|---|---|
| ✅ **Ship** | ≥ 0.322 | ≥ 0.318 | None | Replace Phase 5.2 (0.313) as published Soliton CRB number; merge `cross-file-retrieval.md` (Java-only) + `agents/correctness.md` Java conditional |
| ⚠️ **Hold** | 0.305–0.321 | 0.290–0.317 | Up to 1 lang | Evaluate: did Java recover? Did any other language regress? Decide per-language |
| ❌ **Close negative** | < 0.305 | < 0.290 | Any language > 2σ_lang | Document as experiment; don't merge. Update IMPROVEMENTS.md to note Java +0.046 from 4c.1 was non-replicable single-run noise |

**Why these bands**: Phase 5.2 is 0.313. SHIP requires the projected +0.009 aggregate to materialize at ≥1σ above Phase 5.2 (0.313 + σ_Δ = 0.325; round to 0.322 to match the projection). HOLD allows for noise in either direction within 1σ_Δ of Phase 5.2. CLOSE catches genuine regression below 1σ_Δ.

## Effort

| Component | Effort |
|---|---|
| Re-introduce `cross-file-retrieval.md` (Java-only, no NOT_FOUND_IN_TREE) | ~1 dev-day |
| `agents/correctness.md` conditional block | ~½ dev-day |
| Phase 6 dispatch script + 50-PR CRB run + judge re-pipeline | ~1 dev-day |
| **Total** | **~2.5 dev-days + ~$140 API spend** |

Significantly cheaper than Phase 4 (which was 1.5-2 weeks for full L5 + hallucination-AST). Phase 6 reuses the existing `bench/crb/dispatch-phase4c.sh` + `run-phase4c-pipeline.sh` (retained on main per Phase 4 close-out).

## Rollout plan

1. **This PR (scope-before-build)** — approve the Phase 6 design. Ships only `bench/crb/PHASE_6_DESIGN.md`, no code.
2. **Phase 6a — Skill + agent edits** (~1.5 dev-days): re-introduce `cross-file-retrieval.md` (Java-only, no suppression). Add `agents/correctness.md` Java conditional. Dogfood on Soliton's own Java PRs (none exist; skip).
3. **Phase 6b — CRB validation** (one ~$140 run): 50 PRs under same GPT-5.2 judge. Measure against ship criteria above. **Bounded spend — one run, no exploratory iterations.**
4. **Phase 6c — Decision** (no spend): SHIP (merge to main + update RESULTS.md), HOLD (per-component evaluate), or CLOSE (close PR + update IMPROVEMENTS.md).

## Risk register

| Risk | Mitigation |
|---|---|
| Java +0.046 at 2.6σ_lang fails to replicate (single-run noise was always possible) | Pre-registered CLOSE criteria. CLOSE on failure-to-replicate is acceptable scientific outcome — N=2 still informative. |
| Removing NOT_FOUND_IN_TREE doesn't actually recover Go (the regression had a different driver) | Phase 6 will measure Go F1 and report. If Go stays regressed, hypothesis falsified. |
| Java-only scoping introduces inconsistency with TS / Go / Ruby / Python paths | Acceptable. Scope is intentionally narrow per "subtraction wins, addition fails" pattern from prior 5 close-verdict experiments. |
| Java pattern set has bugs | Validate patterns on Soliton's own historical Java review fixtures (zero exist; skip — accept this risk). |
| Latency bloat | L5 retrieval adds ~5-10s per Java-touching PR. Phase 5.2 baseline median is 25s; manageable. |
| Budget blowout on Java PRs with 100+ symbols | Hard cap at 8 resolutions / agent (same as Phase 4a). |

## Explicit non-goals

- **No re-attempt of §2.5 hallucination-AST agent integration.** Phase 5.3 evidence shows that integration regresses F1 by 0.045. The lib stays standalone.
- **No Go / TS / Python / Ruby retrieval.** Java-only scope. If Phase 6 ships, future phases can extend per-language one at a time.
- **No NOT_FOUND_IN_TREE rule.** That rule was the falsified Phase 4a addition. Do not reintroduce.
- **No agent §2.5 pre-check.** Same as 4b reverted.
- **No description compression / synthesizer dedup widening / per-language nitpicks gate.** All falsified in Phase 3.6 / 3.7 / 3.5.1.
- **No new agents.** Existing 9-review + 4-infrastructure registry stays.
- **Not chasing leaderboard SOTA.** SOTA is 0.61-0.64 (cubic / Qodo Extended); Phase 6's goal is +0.009 aggregate, modest by design. Architectural pivot to I19 sandbox is the path to mid-pack ≥0.45; Phase 6 is the cheap-precision-recovery lane.

## Exit criteria (pre-registered, σ-aware)

After Phase 6b completes the full CRB re-run under the same GPT-5.2 judge as Phase 5.2:

| Aggregate F1 observed | Java F1 observed | Any language regression > 2σ_lang | Decision |
|---:|---:|---|---|
| ≥ 0.322 | ≥ 0.318 | No | **Ship as new published Soliton CRB number; replace Phase 5.2's 0.313** |
| 0.305–0.321 | 0.290–0.317 | Possibly | **Hold** — per-language analysis; ship Java-only if Java cleared but aggregate didn't |
| < 0.305 | < 0.290 | Likely | **Close** as documented negative-result experiment; mark Phase 4c.1 Java +0.046 as single-run non-replicable |

## Strategic context

Phase 6 closes plan-vs-shipped gap **#2 (hallucination-AST orphaned from intended consumer)** indirectly — by validating whether Java-only L5 retrieval recovers the Phase 4c.1 lift, we get evidence on whether a similar narrower per-language re-integration path exists for §2.5 hallucination-AST. SHIP outcome unblocks future per-language §2.5 wiring; CLOSE outcome confirms the lib stays standalone.

Phase 6 does NOT close gap #1 (graph signals spec-only — that needs sibling repo binary), gap #3 (Evidence Chain — multi-week feature work), gap #4 (cost target accuracy — closed by PR #101), or gap #5 (3 dormant agents marketing — closed by PR #100).

## What this doc is NOT

- An implementation. This is scope-before-build.
- A commitment to specific F1 numbers. The +0.009 aggregate is a projection from Phase 4c.1's Java +0.046 at 2.6σ_lang; failure-to-replicate is a real possibility.
- A pre-authorization to spend $140. Authorization needs explicit user go-ahead via "ship Phase 6" or equivalent.

---

*Filed under: Soliton / bench / phase design. Prep work for Strategic Option B from the 2026-05-01 audit. To launch: explicit user authorization → execute Phase 6a (~1.5 dev-days) → Phase 6b ($140 single run) → Phase 6c decision.*
