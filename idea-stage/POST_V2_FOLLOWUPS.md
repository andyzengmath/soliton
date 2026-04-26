# Post-v2.0.1 follow-ups — needs-attention register

**Status:** v2.0.1 shipped 2026-04-22 with Phase 5.2 CRB number F1=0.313, 13 agents, Step 2.6/2.7/2.8 feature-flagged, hallucination-AST library standalone-validated, MCP-backend interop documented.

This file tracks everything still open across the project. Items are grouped by category, then ranked by combination of *blocker severity*, *strategic fit*, and *cost*.

Entries reference `idea-stage/IDEA_REPORT.md` idea numbers (I1–I20) where applicable.

---

## A · Validation gaps (highest leverage; mostly $0)

### A1 · Tier-0 (Step 2.6) end-to-end dogfood
**Status:** code shipped (`skills/pr-review/tier0.md` + `rules/tier0-tools.md` + workflow `examples/workflows/soliton-review-tiered.yml`); activation path documented (PR #40); never empirically validated.
**Why blocked-feeling:** original IDEA_REPORT projection ($0.40 → $0.10 median per-PR cost, 60 % LLM-skip rate) is unsubstantiated. Cost claim depends on this lever firing.
**What it takes:** 10–20 real PRs reviewed twice (with `tier0.enabled: true` + `tier0.skip_llm_on_clean: true` vs. without), measure: (a) Tier-0 verdict distribution, (b) LLM-skip rate, (c) FP escape rate (real bugs Tier-0 missed that LLM caught).
**Cost:** ~$5–$15 (LLM-skip path is cheap by definition). Engineering effort: 0.
**Closes:** I1 ship-criterion of "> 40 % of PRs resolved by Tier-0 alone, < 2 % escape".

### A2 · Spec Alignment (Step 2.7) end-to-end dogfood
**Status:** code shipped (`agents/spec-alignment.md` + Step 2.7); activation path documented (PR #40); never empirically validated.
**Why important:** SWR-Bench / SWE-PRBench show functional-change detection F1 26.2 % vs. evolutionary 14.3 % — spec-alignment puts review on the high-signal side.
**What it takes:** pick 5 PRs with explicit acceptance criteria (PR description with checkboxes, linked issue with criteria, REVIEW.md). Run `/pr-review <PR#>` with `spec_alignment.enabled: true`. Verify `SPEC_ALIGNMENT_START` block appears + flagged unsatisfied criteria are real.
**Cost:** ~$5. Engineering effort: 0.
**Closes:** I3 acceptance criteria validation.

### A3 · Tier-0 default-ON measurement (zero-cost subset of A1)
**Status:** never run. IDEA_REPORT predicted 60 % LLM-skip rate; no data.
**What it takes:** $0 — re-run Tier-0 step alone (no LLM agents) on the existing 50 phase5-reviews/ inputs. Tally how many would have been LLM-skipped (verdict = `clean`, trivial diff). Compare to actual review outputs to estimate FP escape.
**Cost:** $0 (no LLM calls).
**Why deprioritized this session:** user signaled "cost/latency is not priority at current stage."
**Closes:** half of I1's empirical foundation.

### A4 · Judge-noise envelope quantification
**Status:** known finding from Phase 5.2.1 (Ruby +0.022 → −0.008 swing on identical Soliton reviews). Single data point. No formal noise envelope.
**Why it matters:** every per-language conclusion at n=10 is currently noise-compatible. Affects Phase 4c regressions, Phase 5 TS/Python lifts, Phase 5.2 Ruby gain.
**What it takes:** rerun the judge pipeline on the existing phase5_2-reviews/ 3–5 times. Measure σ per-language and aggregate. Establish a credible "swing < 2σ" rule before declaring per-language signals.
**Cost:** ~$45–$75 (3–5 × $15 judge runs).
**Strategic fit:** retroactively calibrates every prior phase's conclusions. High value, contained scope.

### A5 · Realist-check agent CRB measurement
**Status:** `agents/realist-check.md` shipped as part of v2 synthesizer post-pass. Never measured at the CRB level.
**Why important:** intended to drop FP rate by requiring "Mitigated by:" citation for downgrades. If it works, F1 should clear 0.32+.
**What it takes:** $140 50-PR run with `realist-check` enabled in synthesizer. Measure F1 vs Phase 5.2.
**Cost:** $140.
**Strategic fit:** the only built-in v2 lever that hasn't been benchmarked.

---

## B · Sibling project + ecosystem dependencies

### B1 · `graph-code-indexing` maturation
**Status:** sibling repo at `../Logical_inference/graph-code-indexing` provides the 8 edge types Soliton's full-mode Step 2.8 expects. Gaps remaining (per `rules/graph-query-patterns.md` § Dependency table):
- ❌ `graph-cli` binary packaging (Soliton's Mode B contract)
- ❌ Java parser (Gap B4) — blocker for enterprise rebuild
- ❌ SQL analyzer (Gap B4) — blocker for COBOL/legacy datasets
- ❌ PPR centrality (Gap A1)
- ❌ Co-change edges (Gap A6)
- ❌ Feature partition (Leiden + semantic, Gap B8)

**Owned by:** `../Logical_inference/graph-code-indexing` repo, not Soliton.
**Soliton-side workaround:** PR #39 partial-mode via `code-review-graph` covers 2/7 queries today; sufficient to dogfood Step 2.8 mechanism but not full-feature.
**Strategic fit:** B1 unlocks the I2 graph-aware-review moat Soliton's research narrative depends on. Loosely-coupled with Soliton's release cycle but tightly-coupled with strategy.

### B2 · MCP client shim for `code-review-graph`
**Status:** partial-mode (PR #39) supports `info` + `dependencyBreaks` via CLI subcommands; the other 5 Soliton queries (`blastRadius`, `taintPaths`, `coChangeHits`, `affectedFeatures`, `criticalityScore`) are MCP-only on `code-review-graph`'s 28-tool surface.
**Why open:** without this, even `code-review-graph` can only deliver 2/7 signals. Full-coverage from a non-sibling backend needs an MCP stdio client inside Soliton.
**What it takes:** ~1 day engineering. Python wrapper that forks `code-review-graph serve` (stdio MCP), speaks JSON-RPC, exposes the 28 tools as Soliton's expected `graph-cli` subcommands. Lives at `bench/graph/mcp-shim.py` or similar.
**Cost:** $0 API; engineering only.
**Closes:** B1 partial coverage gap. Decouples Soliton's full-mode Step 2.8 from sibling-repo timeline.

### B3 · Martian CRB upstream submission
**Status:** documented as multi-day infra work in `bench/crb/README.md` § Phase 4. Blocked on `claude-code-action` Console-auth for CI dogfood (memory: `reference_claude_code_action_auth.md`).
**What it takes:** (a) unblock Console auth for `claude-code-action`; (b) fork 50 benchmark PRs into a GitHub org where Soliton is installed; (c) patch `step1_download_prs.py` (`_NON_BOT_TOOLS += "soliton"`) and `step0_fork_prs.py` (skip `disable_actions`, inject `soliton-review-bench.yml`); (d) run pipeline; (e) open upstream PR adding soliton row to leaderboard table.
**Cost:** $0 API but multi-day human-time.
**Strategic fit:** turns Soliton's 0.313 self-reported number into a leaderboard-canonical number. Procurement-relevant.

### B4 · `.cursor-plugin/plugin.json` distribution channel
**Status:** v2.0.1 manifest exists; never confirmed Cursor consumes it. Bumping is harmless but may be cargo-culted.
**What it takes:** one quick test — install Soliton via Cursor's plugin path and see if `2.0.1` shows. If not, decide whether to keep the file (as docs / future) or remove (as archaeology).
**Cost:** $0.

---

## C · Empirical credibility gaps

### C1 · Enterprise-rebuild dogfood (Java / COBOL)
**Status:** all CRB numbers are against OSS web/cloud apps (TS, Python, Go, Ruby, Java/Keycloak limited). The PRD's actual goal — AI-native rebuild of legacy Java/COBOL/PL-SQL — is **untested**.
**Why this is the biggest gap:** the strategic moat narrative (`docs/prd-ai-native-takeover.md` + `idea-stage/IDEA_REPORT.md` § 7) hinges on enterprise-rebuild fit. Without one validation run, the v2 architecture lives in OSS-web territory.
**What it takes:**
1. Pick a Java target — Eclipse BIRT, Spring PetClinic, or your own internal monolith.
2. Set up a 10–20 PR sample (could be synthetic: pick recent main commits and re-PR them).
3. Run Soliton (with graph signals if `graph-code-indexing` has Java; without if not).
4. Measure: precision, recall, **what kinds of findings Soliton catches that procurement-tier reviewers care about** (auth bugs, transaction integrity, schema migration regressions).
**Cost:** ~$15–$50 LLM + significant set-up time.
**Strategic fit:** **highest of any open item.** Closes the OSS-vs-enterprise credibility gap.

### C2 · Cost-normalised F1 metric
**Status:** claimed in IDEA_REPORT § 8 ("$/PR-reviewed; Soliton's cost-normalised F1 wins") but no formal denominator data published. Competitor per-PR API cost not in CRB leaderboard.
**What it takes:** instrument Soliton's `--output-format json` to emit token + dollar metadata per review. Run on 50 PRs, publish F1 ÷ ($/PR) alongside raw F1. Request Martian add `cost_per_pr` column to dashboard.
**Cost:** ~$15–$50 (one CRB run with token counters).
**Strategic fit:** turns Soliton's "cheap by design" pitch into a measured number. Direct procurement positioning.

### C3 · Per-language sample size — corpus expansion
**Status:** n=10 per language. Phase 5.2.1 demonstrates per-language signals are noise-dominated at this size.
**What it takes:** expand corpus to 20–30 PRs per language by sampling from the online benchmark or other OSS sources. Re-run all phases (Phase 3.5, Phase 5.2.1) with larger n. Total: 100–150 PRs × ~$2.50 = $250–$375 per phase.
**Cost:** $250–$750.
**Strategic fit:** would settle every "Java +0.05 vs. Java -0.04" debate from the existing single-judge data.

---

## D · Engineering refinements

### D1 · Strip-footnote-titles.py — keep or retire
**Status:** lives at `bench/crb/strip-footnote-titles.py`; v2.0.1 shipped a tightened version (Phase 5.2.1). The SKILL.md fix from Phase 5.2 prevents the leak prospectively, so this script only matters for retroactive counterfactuals.
**What it takes:** decision call. Keep (as archaeology / future-counterfactual support) vs. retire (commits noise, confuses maintainers). Author leans **keep** — zero maintenance cost; clearly Phase-specific filename.

### D2 · `agents/cross-file-impact.md` caller-direction refactor (deferred from Phase 4a)
**Status:** noted in MEMORY.md as "deferred from Phase 4a MVP". The cross-file-impact agent currently looks at downstream effects of changes; a true caller-direction view (what calls the changed thing?) is what `dependencyBreaks` should drive.
**What it takes:** edit `agents/cross-file-impact.md` to consume `graphSignals.dependencyBreaks` for caller-direction; keep its existing forward-direction logic. ~2 hours engineering.
**Cost:** $0.

### D3 · Step 4.1 deterministic skipAgents enforcement (Phase 5.1 follow-up)
**Status:** Phase 5.1 counterfactual ruled this out (lost 3 real Low/Medium TPs for +0.002 F1). Decision was "leave LLM-soft-enforcement; accept ~6 % leak". File for completeness.
**What it takes:** if revisited, cost is ~$140 to re-measure. Author leans **don't pursue** — already falsified once.

---

## E · Secondary ideas (Phase 6+)

These are I10–I20 from `idea-stage/IDEA_REPORT.md` § 5. None started. All explicitly Phase 2+.

| ID | Name | Reason for queue position |
|---|---|---|
| I10 | Tri-model cross-check (`--crossmodel`) | Phase 2 explicitly; uniquely valuable for risk-averse buyers |
| I11 | Pre-merge-checks DSL (CodeRabbit-style) | Niche; ship if customer requests |
| I12 | Hunk-grouping + tri-state severity UX | UX polish; ship after volume settles |
| I13 | Inline PR comments (vs single block) | UX polish; downstream of action mode |
| I14 | Pre-existing-bug severity (purple tier) | Cosmetic |
| I15 | Prior-PR comment mining | Low ROI without state |
| I16 | Learnings loop in `.omc/state/` | Requires state infra |
| I17 | LSP / ast-grep tool access | Engineering uplift; opportunistic |
| I18 | BugBot multi-pass + voting | Cost-bounded to CRITICAL tier; defer until volume |
| I19 | Execution-sandbox verify-fix | Phase 2/3 — major engineering |
| I20 | License-check dimension | Niche; ship via plug-in |

---

## F · Process notes

### F1 · Judge variance discipline
Going forward, **per-language conclusions at n=10 should require either σ-aware framing OR re-runs across multiple judges**. Phase 5.2.1's Ruby swing established the empirical noise floor; future writeups should cite ±0.02–0.03 noise band per language at this corpus size.

### F2 · IMPROVEMENTS.md calibration discount
3–5× discount on napkin projections still holds (verified across Phase 3.5 / 3.6 / 3.7 / 5 / 5.2 / 5.2.1). Apply when proposing any new lever.

### F3 · Strict ship criteria — noise margin
Pre-registered ship criteria like "F1 ≥ 0.30" should explicitly say "subject to ±σ judge-noise" once F2's σ is quantified. Current Phase 5 / 5.2 uses strict-floor + practical-rounding interpretation; that's a workaround, not a doctrine.

---

## Ranked priorities (author's read, 2026-04-22)

If picking just one:
1. **C1 enterprise-rebuild dogfood** — closes the strategic moat gap; nothing else does.
2. **A1 Tier-0 dogfood** — substantiates the cost-efficiency story which is half the v2 pitch.
3. **A4 judge-noise envelope** — calibrates everything else; one-time cost.

If picking three over the next month:
1. C1 enterprise dogfood
2. A1 + A2 + A5 — three v2 mechanisms with shipped code but zero validation
3. A4 judge-noise quantification

If $0 budget:
1. A3 Tier-0 default-ON measurement (zero-cost subset of A1)
2. D2 cross-file-impact caller-direction refactor (engineering only)
3. Triage I11–I20 for any "ship for free" opportunities

---

*This file is a living register. Update as items close. Cross-link from `bench/crb/RESULTS.md` and v-release notes.*
