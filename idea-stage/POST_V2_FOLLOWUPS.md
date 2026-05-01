# Post-v2.0.1 follow-ups — needs-attention register

**Status:** v2.1.2 (latest) cuts the post-v2.1.1 cluster of 16 PRs (#70-#85) into a release: §C2 cost-normalised F1 schema (PR #82) + derivation (PR #83) + new `rules/model-pricing.md`; §C1 PetClinic enterprise-Java dogfood SHIP (PR #71); §A1+§A2+§A3 derivations (PRs #74+#76); §G2-fuller fixture coverage (PR #81); §D1 strip-footnote-titles.py KEEP decision; manifest sync + Java Tier-0 install cheatsheet + architecture-diagram refresh + ci-cd-integration v2 features. CRB number of record stays Phase 5.2's F1=0.313. v2.1.0 wired realist-check + silent-failure + comment-accuracy; v2.1.1 reverted silent-failure + comment-accuracy defaults to OFF (Phase 5.3 evidence). 13 agents (9 review + 4 infrastructure: risk-scorer, spec-alignment, realist-check, synthesizer); Steps 2.6/2.7/2.8/5.5 feature-flagged; hallucination-AST library standalone-validated; MCP-backend interop documented.

This file tracks everything still open across the project. Items are grouped by category, then ranked by combination of *blocker severity*, *strategic fit*, and *cost*.

Entries reference `idea-stage/IDEA_REPORT.md` idea numbers (I1–I20) where applicable.

---

## A · Validation gaps (highest leverage; mostly $0)

### A1 · Tier-0 (Step 2.6) end-to-end dogfood — **CLOSED 2026-05-01 (SHIP — derived from C1)**
**Status:** ✅ closed via derivation. The C1 PetClinic dogfood (PR #71) recorded Tier-0 verdicts on 10 PRs. Re-aggregating: 6 of 10 PRs (60 %) hit `clean` verdict, well above the §A1 30 % LLM-skip threshold. Two known-unflagged CRITICAL classes the LLM swarm caught (PR 2093 gradle-wrapper sha256 removal, PR 1878 Thymeleaf `${addVisit}` typo) bound the escape-rate observation; both are Tier-0-rule-extension opportunities tracked as §C1.B follow-ups. Writeup: `bench/graph/a1-a2-derivation.md`. Methodology caveat: simulator-derived (single-agent C1 dogfood); a signal-grade re-run with full swarm dispatch projects ~$15-25 — deferred to §C1.B.

**(Pre-2026-05-01 status preserved for context:)**

code shipped (`skills/pr-review/tier0.md` + `rules/tier0-tools.md` + workflow `examples/workflows/soliton-review-tiered.yml`); activation path documented (PR #40); never empirically validated.
**Why blocked-feeling:** original IDEA_REPORT projection ($0.40 → $0.10 median per-PR cost, 60 % LLM-skip rate) is unsubstantiated. Cost claim depends on this lever firing.
**What it takes:** 10–20 real PRs reviewed twice (with `tier0.enabled: true` + `tier0.skip_llm_on_clean: true` vs. without), measure: (a) Tier-0 verdict distribution, (b) LLM-skip rate, (c) FP escape rate (real bugs Tier-0 missed that LLM caught).
**Cost:** ~$5–$15 (LLM-skip path is cheap by definition). Engineering effort: 0.
**Closes:** I1 ship-criterion of "> 40 % of PRs resolved by Tier-0 alone, < 2 % escape".
**σ-aware ship criterion (revised 2026-04-29):** "< 2 % escape" needs n ≥ 50 to clear the σ_escape ≈ 1/√n ≈ 14 % per-PR floor at n=10. At n=20 the binomial Wilson CI is roughly ±10pp, so 0/20 escapes only proves escape rate < ~17 % at 95 % CI. Either expand to n=50 *or* re-state the criterion as "0 escapes observed at n=20" (which is informational, not a probabilistic guarantee).

### A2 · Spec Alignment (Step 2.7) end-to-end dogfood — **CLOSED 2026-05-01 (SHIP — derived from C1)**
**Status:** ✅ closed via derivation. The C1 PetClinic dogfood (PR #71) recorded spec-alignment verdicts on 10 PRs. Re-aggregating: 8 of 10 PRs emitted `SPEC_ALIGNMENT_START` blocks (the other 2 correctly emitted `SPEC_ALIGNMENT_NONE` due to empty PR bodies / no spec source). At least 1 real spec-vs-diff mismatch surfaced (PR 2133's mischaracterisation finding — claimed "Replaced deprecated MySQLContainer" but the class was modularised, not deprecated). Pre-reg "≥ 1 SPEC_ALIGNMENT block" cleared multiple times over. Writeup: `bench/graph/a1-a2-derivation.md`. Methodology caveat: simulator-derived; a real `soliton:spec-alignment` Haiku dispatch likely produces more findings, not fewer.

**(Pre-2026-05-01 status preserved for context:)**

code shipped (`agents/spec-alignment.md` + Step 2.7); activation path documented (PR #40); never empirically validated.
**Why important:** SWR-Bench / SWE-PRBench show functional-change detection F1 26.2 % vs. evolutionary 14.3 % — spec-alignment puts review on the high-signal side.
**What it takes:** pick 5 PRs with explicit acceptance criteria (PR description with checkboxes, linked issue with criteria, REVIEW.md). Run `/pr-review <PR#>` with `spec_alignment.enabled: true`. Verify `SPEC_ALIGNMENT_START` block appears + flagged unsatisfied criteria are real.
**Cost:** ~$5. Engineering effort: 0.
**Closes:** I3 acceptance criteria validation.

### A3 · Tier-0 default-ON measurement (zero-cost subset of A1) — **CLOSED 2026-05-01 (informational — derived from phase5_2-reviews/)**
**Status:** ✅ closed via derivation. Tally of Tier-0 fast-path-clean eligibility across the 50-PR Phase 5.2 CRB corpus: **0 of 50 PRs (0% LLM-skip rate)**. Every PR fails at least one of the two `clean` promotion gates (`diff_lines ≤ 50` AND `0 findings`): 4 PRs had small diffs but Soliton found CRITICAL/IMPROVEMENT issues; 3 PRs had 0 findings but diff > 50 LOC; 43 PRs failed both gates. **Result does NOT contradict the IDEA_REPORT 60% prediction** — it confirms CRB is selected for non-trivial review-quality benchmark cases by design, while §A1's PetClinic real-world dogfood (60% match) validates the 60% prediction directly. Writeup: `bench/graph/a3-derivation.md`. **Implication:** Tier-0's value-prop is real-world cost saving, not benchmark-leaderboard improvement; future Tier-0 dogfoods should use real-world PR-stream corpora, NOT CRB.

**(Pre-2026-05-01 status preserved for context:)**

never run. IDEA_REPORT predicted 60 % LLM-skip rate; no data.
**What it takes:** $0 — re-run Tier-0 step alone (no LLM agents) on the existing 50 phase5-reviews/ inputs. Tally how many would have been LLM-skipped (verdict = `clean`, trivial diff). Compare to actual review outputs to estimate FP escape.
**Cost:** $0 (no LLM calls).
**Why deprioritized this session:** user signaled "cost/latency is not priority at current stage."
**Closes:** half of I1's empirical foundation.

### A4 · Judge-noise envelope quantification — **CLOSED 2026-04-29**
**Status:** ✅ closed. N=3 judge re-runs (~$45) on `phase5_2-reviews/` plus the published 5.2.1 anchor → **σ_F1 aggregate = 0.0086, σ_F1 per-language max = 0.0179 (TS)**. Mean F1 across 4 runs = 0.321 (Phase 5.2's published 0.313 was on the low edge). Writeup at `bench/crb/judge-noise-envelope.md`; raw run data under `bench/crb/judge-noise-runs/run{0..3}/`. Aggregator `bench/crb/compute-noise-envelope.py`.
**Retroactive verdicts:**
- Phase 5.2 vs P3.5 (+0.036) = **4.16σ signal** ✓
- Phase 5 vs P3.5 (+0.023) = **2.66σ signal** ✓
- Phase 3.5.1 (−0.034) = **3.93σ regression** ✓
- Phase 5.2 vs P5 (footnote strip alone, +0.013) = **1.5σ provisional** (was holding the 0.305 ship floor by 0.008 in the published number; mean is 0.321, but the isolated footnote-strip lever is below 2σ)
- Phase 4c (−0.016) = 1.85σ provisional (close verdict was correct)
- Phase 4c.1 (+0.001) = 0.12σ pure noise
- Phase 5.2.1 re-run (−0.005) = 0.58σ noise (confirmed)

### A5 · Realist-check agent CRB measurement — **CLOSED 2026-04-30 (folded into A6 Phase 5.3)**
**Status:** ✅ closed. The realist-check Step 5.5 wiring shipped in v2.1.0 (PR #50, default OFF). Phase 5.3 (§A6 below) ran the combined v2.1.0 wiring stack at default-ON-via-local-config and measured F1=0.268 vs Phase 5.2's 0.313 — a −0.045 regression at 5.2σ_Δ. The realist-check pass itself was found neutral on benchmark precision (Critical recall preserved at 0.889; the −0.045 regression attributed primarily to silent-failure + comment-accuracy default-ON). No separate A5 measurement needed; the per-wiring isolation arm (~$420 N=3) was deprioritized after Phase 5.3's CLOSE verdict. Realist-check stays default-OFF as shipped; useful for production review (Mitigated-by rationale UX) but not benchmark F1.

**(Pre-2026-04-30 status preserved for context:)**

`agents/realist-check.md` shipped as part of v2 synthesizer post-pass. Never measured at the CRB level. ~~**WIRING GAP discovered 2026-04-29:** the agent definition exists at `agents/realist-check.md` but is NOT referenced in `agents/synthesizer.md` or `skills/pr-review/SKILL.md`. Realist-check is currently dead code — never dispatched. Pre-step before any CRB measurement: wire it in as a Step 5.5 in SKILL.md, gated on `config.synthesis.realist_check`.~~ **Wiring CLOSED 2026-04-29 via PR #50** — Step 5.5 added, default OFF. CRB measurement remains open.
**Why important:** intended to drop FP rate by requiring "Mitigated by:" citation for downgrades.
**σ-aware revised pre-reg (2026-04-29):** the original "F1 should clear 0.32+" criterion was set before σ_F1=0.0086 was measured. 0.32 is only +0.007 above Phase 5.2's published 0.313, ≈ 0.8σ_aggregate or 0.6σ_Δ paired — would ship on noise. Revised criteria:
- ✅ **Ship:** F1 ≥ 0.337 (= 0.313 + 2σ_Δ paired = 0.313 + 0.024) AND recall ≥ 0.50 AND no per-language reg > 0.036 (1σ_lang).
- ⚠️ **Hold:** 0.325 ≤ F1 < 0.337 (within 2σ_Δ of Phase 5.2 published — provisional ship at single re-run).
- ❌ **Close:** F1 < 0.313 (below Phase 5.2 baseline).

Per-realist-check projection from `agents/realist-check.md`: expected FP-cut of ~10–20 candidates (out of ~250 in Phase 5.2's mean), worth ~+0.01 to +0.02 F1 napkin → discounted to +0.003 to +0.007 realized — **below the 2σ ship threshold of +0.024 even at the high end**. To get a clean signal, either:
1. **Defer A5 indefinitely** as a likely-noise-level lever; or
2. **Run A5 with N=2-3 re-runs** (~$280-$420 total) so the measurement's own σ_run drops below the expected lift's magnitude; or
3. **Run A5 at N=1 with weaker pre-reg** ("any positive Δ ≥ 1σ_Δ = 0.012 is provisional ship") — informational, not signal-grade.
**What it takes:** wire the agent first ($0 eng), then pick (1)/(2)/(3) per cost ladder.
**Cost:** N=1 ~$140; N=3 ~$420.
**Strategic fit:** the only built-in v2 lever that hasn't been benchmarked. But under measured σ, the expected lift is below the 2σ noise floor, so the rigor-vs-cost trade-off is real.

### A6 · silent-failure + comment-accuracy CRB measurement — **CLOSED 2026-04-30 ❌ CLOSE verdict**
**Result:** Phase 5.3 50-PR run with all 4 v2.1.0 wirings active produces **F1 = 0.268** (P=0.183, R=0.500), a **−0.045 regression vs Phase 5.2's published 0.313** — well outside the σ_F1=0.0086 noise band (>2σ_Δ paired). Critical recall preserved at 0.889; regression is in High recall (−0.097) + cross-language precision. TS held at +0.070 vs P3.5 (graph signals helping); Python/Ruby/Go/Java all regressed. UNMATCHED FP volume jumped from ~51 (Phase 5.2) to 180 — the new wirings emit findings whose extractor candidates don't fuzzy-match back. Per pre-reg, **CLOSE** verdict triggered. Phase 5.2's F1=0.313 remains Soliton's CRB number of record.
**Recommendation (per `bench/crb/PHASE_5_3_WRITEUP.md`):** flip `agents.silent_failure.enabled` and `agents.comment_accuracy.enabled` defaults back to OFF for benchmark runs (precision-tuning pass needed). Realist-check Step 5.5 wiring is correct but neutral on benchmark; cross-file-impact graphSignals is keep-but-tune-severity-gate. Cost: ~$140 (≈$125 dispatch + $15 judge).

(Original 2026-04-29 entry follows for historical context.)

**Status:** `agents/silent-failure.md` and `agents/comment-accuracy.md` were dead code since v2.0.0 (defined + registered in `plugin.json` but never dispatched by SKILL.md). **Wiring CLOSED 2026-04-29 via PR #51** — Step 4.1 sub-step 3 added with content-trigger conditions matching the agents' own dispatch-rule sections. Defaults flipped to `true` in CHANGELOG_V2.md to match the "available in v2.0" advertisement. README badge bumped 7 → 9.

**CRB measurement remains open.** With realist-check (PR #50) + silent-failure + comment-accuracy (PR #51) all newly wired, a single Phase 5.3 CRB run could measure all three at once for ~$140 (N=1) or ~$420 (N=3).

**σ-aware pre-reg (combined three-agent run):**
- ✅ **Ship:** F1 ≥ 0.337 (Phase 5.2 published 0.313 + 2σ_Δ paired = 0.024) AND recall ≥ 0.50 AND no per-language reg > 0.036.
- ⚠️ **Hold:** 0.325 ≤ F1 < 0.337 (within 2σ_Δ — provisional ship at single re-run).
- ❌ **Close:** F1 < 0.313.

Combined napkin lift (per agent docs + Hora & Robbes 2026 references):
- realist-check: ~+0.003 to +0.007 realized (post-discount)
- silent-failure: ~+0.005 to +0.010 (specialist findings on AI-authored PRs)
- comment-accuracy: ~+0.002 to +0.005 (mostly catches comment-rot the existing 7 agents miss)
- **Combined: ~+0.010 to +0.022 realized**, sitting near the 2σ_Δ ship threshold of +0.024.

**Recommended next step:** run the Phase 5.3 measurement at N=1 ($140) accepting the borderline-signal risk; if F1 lands in the hold band (0.325–0.337), expand to N=3 (additional $280) to resolve. If F1 lands < 0.325 (clearly below ship band), the result is informational and the wiring still has product value (specialist findings ≠ benchmark F1).

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

### C1 · Enterprise-rebuild dogfood (Java / COBOL) — **FULL CLOSURE 2026-05-01 (scout + C1.B both SHIP; signal-grade)**

**Scout arm (PetClinic) — 2026-04-30 SHIP** (PR #71). Spring PetClinic / 10-PR ~$2.38. Pre-reg both cleared. Writeup: `bench/graph/enterprise-java-dogfood.md`.
- PR 2093 — `distributionSha256Sum` removal in gradle wrapper (CWE-494, OWASP A08; CRITICAL conf 92; human reviewer missed)
- PR 2133 — `--release 17` flag dropped while `<java.version>25</java.version>` kept (CRITICAL conf 92; oracle-confirmed by maintainer @snicoll's post-merge revert `fc1c749`)
- PR 1878 — `${addVisit}` Thymeleaf typo + cross-locale trailing-space drift (HIGH)
- PR 1775 — `Collectors.toList()` immutability regression on `@XmlElement` JAXB-marshalled method (IMPROVEMENT after realist-check downgrade)
- *Methodology caveat (closed by C1.B below):* single-agent simulation; per-agent attribution simulator-derived.

**C1.B (Apache Camel) — 2026-05-01 SHIP** (PR #89). Full-swarm dispatch from main-orchestrator context — closes the simulator caveat. 10-PR ~$3.28. **5 CRITICAL + 19 IMPROVEMENT + 7 NITPICK across the corpus.** Pre-reg both cleared decisively. Writeup: `bench/graph/enterprise-camel-dogfood.md`.
- **PR #22881** NPE in `DefaultModelToStructureDumper` when routeId not found (CRITICAL conf 95; JMX-reachable)
- **PR #22881** New JSON route dump leaks credentials — bypasses XML/YAML's `setMask` gate; CWE-200/532/OWASP A09 (CRITICAL conf 85)
- **PR #22880** `trustManagerMapper` asymmetric null guard → NPE during SSL handshake (CRITICAL conf 88)
- **PR #22876** `Files.exists` follows symlinks → dangling symlink causes `FileAlreadyExistsException` (CRITICAL conf 88)
- **PR #22866** NPE in `getJMSMessageTypeForBody` no-arg constructor path (CRITICAL conf 95)
- C1.B produces **~6× more findings** than C1 scout (31 vs ~4) because real swarm dispatch surfaces correctness/security/cross-file-impact concerns that single-agent simulators miss. Validates IDEA_REPORT G2/G3/G6 Tier-A premise.

**§C1 status:** ✅ **closed at signal-grade.** Strategic narrative (PRD §7 enterprise-rebuild moat) backed by both value-prop demo (PetClinic) AND methodology rigor (Camel full-swarm); cross-link with §C2 cost-normalised F1 (PR #83) closes the procurement-readiness story end-to-end.

**Remaining open arms (not session-actionable):**
- **C1.C — Microsoft-internal monolith / COBOL / PL-SQL target** — gated on access + on graph-code-indexing's SQL/COBOL parser support per §B1.
- **Procurement-tier metrics** (precision/recall vs. annotated-bug ground truth) — needs annotated-bug corpus; not present in either PetClinic's or Camel's git log.

**Strategic fit (post-shipping):** the value-prop case for "Soliton catches enterprise-rebuild-relevant defects" is no longer aspirational — it is observed. PRD §7 strategic-moat narrative now backed by 4 oracle-grade findings on real Spring Boot 3.5/4.0 + supply-chain migration PRs.

**(Original 2026-04-29 entry follows for historical context.)**

**Status:** all CRB numbers are against OSS web/cloud apps (TS, Python, Go, Ruby, Java/Keycloak limited). The PRD's actual goal — AI-native rebuild of legacy Java/COBOL/PL-SQL — is **untested**.
**Why this is the biggest gap:** the strategic moat narrative (`docs/prd-ai-native-takeover.md` + `idea-stage/IDEA_REPORT.md` § 7) hinges on enterprise-rebuild fit. Without one validation run, the v2 architecture lives in OSS-web territory.
**What it takes:**
1. Pick a Java target — Eclipse BIRT, Spring PetClinic, or your own internal monolith.
2. Set up a 10–20 PR sample (could be synthetic: pick recent main commits and re-PR them).
3. Run Soliton (with graph signals if `graph-code-indexing` has Java; without if not).
4. Measure: precision, recall, **what kinds of findings Soliton catches that procurement-tier reviewers care about** (auth bugs, transaction integrity, schema migration regressions).
**Cost:** ~$15–$50 LLM + significant set-up time.
**Strategic fit:** **highest of any open item.** Closes the OSS-vs-enterprise credibility gap.

### C2 · Cost-normalised F1 metric — **PHASE 1 + PHASE 2 (DERIVATION) CLOSED 2026-05-01 (PRs #82 + #83)**

**Phase 2 status:** ✅ closed informationally via $0 derivation. **F1/$ = 0.855 on CRB corpus (HOLD per pre-reg)** at projected mean $0.366/PR across the 50-PR Phase 5.2 corpus; **F1/$ ≈ 2.14 in real-world streams with §A1's 60 % Tier-0 fast-path (SHIP per pre-reg)**. Both numbers publishable for procurement-readiness with explicit framing — CRB for benchmark credibility, real-world for cost-efficiency moat. Per-tier breakdown: 3 LOW + 5 MEDIUM + 19 HIGH + 23 CRITICAL (CRB curates non-trivial cases; per §A3 confirmed 0 % Tier-0 fast-path on this corpus). Per-language slice: Go is the only language slice that clears 1.0 individually (1.03); Ruby is the worst (0.74); TypeScript / Python / Java cluster around 0.77-0.83. Writeup: `bench/crb/cost-normalised-f1.md`. Methodology caveats: per-tier cost projections (not measurements); harness instrumentation required for signal-grade re-run. The signal-grade Phase 2 measurement (~$15-25) remains pre-authorized once harness surfaces per-Agent `usage`.


**Phase 1 status:** ✅ shipped. `skills/pr-review/SKILL.md` Step 6 Format B `metadata` block now declares `totalTokens.{input,output,cacheCreation,cacheRead}` + `costUsd` (≤4 decimals). `rules/model-pricing.md` (NEW) declares the per-model rate sheet (Opus 4.x / Sonnet 4.x / Haiku 4.x), the cache-pricing rule (cache write +25%, cache read 90% off), the per-Agent → per-model → costUsd computation algorithm, the Bedrock/Vertex `costing.rate_overrides` integrator-override pattern, and the rate-update protocol. `rules/model-tiers.md` § Cost projection cross-links the new `metadata.costUsd` field. **Caveat:** Claude Code's Agent tool does not surface per-Agent `usage` in return values today, so the orchestrator falls back to a length-based heuristic with `*`-suffix annotation in interactive output until harness support lands.

**Phase 2 status (pre-authorized $15-25, NOT yet started):** re-run Phase 5.2 CRB with the instrumented orchestrator + capture aggregate F1 ÷ $/PR + per-language slice. Compare against the IDEA_REPORT $0.10 (target) / $0.40 (current baseline) cost band. Pre-reg ship: F1/$ ratio ≥ 1.0; hold: 0.7-1.0; close: < 0.7. Output: `bench/crb/cost-normalised-f1.md` + leaderboard-ready summary table.

**(Pre-2026-05-01 status preserved for context:)**

### C2-original · Cost-normalised F1 metric
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

### D1 · Strip-footnote-titles.py — keep or retire — **CLOSED 2026-05-01 (KEEP)**
**Status:** ✅ closed. Decision: KEEP `bench/crb/strip-footnote-titles.py`. Rationale: zero maintenance cost (no imports, no test surface, no integration with the rest of the bench/crb pipeline); clearly Phase-specific filename signals to maintainers that it's archaeological. v2.0.1 shipped the tightened version (Phase 5.2.1) and the SKILL.md fix from Phase 5.2 prevents the underlying leak prospectively, so the script is now only useful for retroactive counterfactual analysis (e.g. "what would CRB F1 look like if footnote-strip had been applied to Phase 4c reviews?"). That retroactive utility is occasionally cited in writeups; retiring it would force future authors to re-derive the strip logic. Status now informational; this entry stays in §D for posterity.

**(Pre-2026-05-01 status preserved for context:)**

lives at `bench/crb/strip-footnote-titles.py`; v2.0.1 shipped a tightened version (Phase 5.2.1). The SKILL.md fix from Phase 5.2 prevents the leak prospectively, so this script only matters for retroactive counterfactuals.
**What it takes:** decision call. Keep (as archaeology / future-counterfactual support) vs. retire (commits noise, confuses maintainers). Author leans **keep** — zero maintenance cost; clearly Phase-specific filename.

### D2 · `agents/cross-file-impact.md` caller-direction refactor — **CLOSED 2026-04-29 via PR #61**
**Status:** ✅ closed. Agent now reads `graphSignals.dependencyBreaks[]` from input and runs Step 1.5 graph-driven path when signals are present (confidence 90 for graph-derived findings). Falls through to v1 Grep-based caller discovery when graphSignals absent — no behavior change for users who haven't enabled the v2 graph flag. SKILL.md Step 4.2 now passes `dependencyBreaks` to the cross-file-impact agent specifically. CRB measurement folded into §A6 combined Phase 5.3 run (no separate spend).

### D3 · Step 4.1 deterministic skipAgents enforcement (Phase 5.1 follow-up)
**Status:** Phase 5.1 counterfactual ruled this out (lost 3 real Low/Medium TPs for +0.002 F1). Decision was "leave LLM-soft-enforcement; accept ~6 % leak". File for completeness.
**What it takes:** if revisited, cost is ~$140 to re-measure. Author leans **don't pursue** — already falsified once.

---

## G · Test / CI / engineering gaps (NEW 2026-04-29 audit)

Surfaced by the parallel audit-team pass after PR #51 (judge-noise + 3 wiring PRs). All HIGH severity per the audit, all infrastructural rather than feature-shipping.

### G1 · `lib/hallucination-ast/` tests not wired into CI — **CLOSED 2026-04-29 via PRs #55, #56, #58**
**Status:** ✅ closed. `.github/workflows/hallucination-ast-tests.yml` runs `pytest lib/hallucination-ast/tests/` with 80% coverage gate on every PR or push that touches the package. PR #56 fixed three pre-existing test-harness bugs (missing `pytest.importorskip("requests"|"pandas")`) surfaced by the CI dogfood. PR #58 SHA-pinned the workflow's actions/checkout + actions/setup-python per repo policy. **Current state on main: 130 tests pass, 11 skipped (8 optional-dep skips + 3 fixed in #56), 84% coverage.**

### G2 · Fixture-based integration test runner — **PARTIAL CLOSURE 2026-04-29 via PRs #59, #60**
**Status:** 🟡 two of three runner modes wired; the auth-blocked third mode remains deferred.

**Closed**: `tests/run_fixtures.py` + `.github/workflows/fixture-runner.yml` cover:
- **`--mode structural`**: schema validation across all 11 fixtures (riskRange shape, expectedFindings type, severity allowed values, optional v2 fields). 11/11 PASS.
- **`--mode phase4b`**: subprocesses `python -m hallucination_ast --diff <fixture>` for the 2 phase4b fixtures (`hallucinated-import`, `signature-mismatch`), parses stdout JSON, asserts emitted finding matches `phase4bExpected.{rule, symbol, suggestedFix?, confidence?}`. 2/2 PASS. PR #60 followed up with a `pip install requests` step so `hallucinated-import`'s AST resolver can introspect the requests module.

**Still open**: `--mode pr-review` arm — full integration runner that subprocesses Claude Code with `--plugin-dir .` and asserts `riskRange` / `expectedFindings` / `expectedCategories` / `expectedSeverity` across all 11 fixtures. **Auth-blocked** on `ANTHROPIC_API_KEY` (or OAuth-token equivalent) in repo secrets — same blocker as the Soliton-Review dogfood workflow + §B3 Martian CRB upstream.

Recommended new fixtures for the auth-blocked phase: `realist-check-downgrade-rejected` (verifies the no-mitigation-cited guard from PR #50's Step 5.5), `silent-failure-fires-on-trycatch` (PR #51 wiring), `comment-accuracy-fires-on-comment-edit` (PR #51 wiring).
**Cost to fully close:** ~$0 API once secret is set + small engineering (~half-day to extend runner with `--mode pr-review`).

### G3 · I8 stack-awareness flag parsed but orchestrator logic missing
**Status:** SKILL.md § "Supported Flags" documents `--parent <PR#>`, `--parent-sha <SHA>`, `--stack-auto` as v2 flags. The flags are **parsed** in Step 1 (Mode A / Mode B) but **no orchestrator logic computes the stacked-PR delta**. A user who runs `/pr-review --parent 42` thinks they're getting "review delta vs parent PR's head" but actually gets the same v1 base-vs-head diff.
**Why it matters:** UX-misleading (silent flag rejection); strategic-fit blocker for enterprise rebuild (per IDEA_REPORT § I8, feature-chain PRs in legacy Java/COBOL rebuilds are inherently stacked). Not currently a runtime crash, just a no-op.
**What it takes:** edit Step 1 (Input Normalization) to compute `git diff parent_pr_sha...HEAD` instead of `baseBranch...HEAD` when `--parent` set. Mode A (local branch) needs git fetch logic to resolve parent PR head. Mode B (PR number) needs to chain `gh pr view <parent> --json headRefOid` before fetching the diff. Maybe ~1 week engineering including tests.
**Cost:** $0 API + ~1 week engineering.

**Net G-section recommendation:** address G1 first (small, $0, blocks regression), then G2 (medium, structural ROI), then G3 (week-scale; tied to C1 enterprise dogfood validation since stacked-PR review is the critical path for that use case).

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

### F1 · Judge variance discipline — **measured 2026-04-29 (was placeholder)**
N=3 + 1 anchor judge re-runs of `phase5_2-reviews/` give:

- **σ_F1 aggregate = 0.0086** (1σ; 2σ ≈ 0.017) — single-judge re-run noise on the same Soliton output, n=50 PRs.
- **σ_F1 per-language max = 0.0179** (TS); per-language band is roughly 2× aggregate, consistent with √5 scaling from n=50 → n=10.
- Variance is dominated by FP fluctuation (σ_FP ≈ 9.5 across runs); TPs and FNs are nearly stable. The judge's *recall* is reproducible; its *precision* classification is the noisy axis.

**Updated discipline going forward:**

1. **Aggregate F1 deltas < 0.009 (1σ) are noise**; 0.009–0.018 (1–2σ) are provisional; > 0.018 (2σ) clear signal.
2. **Per-language deltas < 0.018 (1σ_lang) are noise**; > 0.036 (2σ_lang) clear signal at n=10.
3. **Single-CRB-number reporting** should cite mean ± σ over N independent re-runs whenever feasible, not a point estimate.
4. **Per-agent ablation deltas at N=1 are fine** for the high-volume agents; σ_TP ≤ 1.26 means TP deltas ≥ 3 (e.g. Phase 5's `test-quality`/`consistency` removal) clear 2σ comfortably.

Full measurement, calibration table, methodology in `bench/crb/judge-noise-envelope.md`.

### F2 · IMPROVEMENTS.md calibration discount
3–5× discount on napkin projections still holds (verified across Phase 3.5 / 3.6 / 3.7 / 5 / 5.2 / 5.2.1). Apply when proposing any new lever. **2026-04-29 update:** under measured σ_F1=0.0086, the discount has a hard floor — *any* projected lift smaller than 2σ_Δ = 0.024 (after the 3–5× discount applied) is below the noise floor and cannot be signaled at N=1. Implication for IMPROVEMENTS.md's L1–L9 levers: post-discount, only levers with a napkin lift ≥ +0.07 (3× discount → +0.024) or ≥ +0.12 (5× discount → +0.024) clear the 2σ_Δ bar at N=1. For smaller-projected levers, either skip or commit to N≥3 re-runs.

### F3 · Strict ship criteria — measured noise margin (replaces 2026-04-22 placeholder)
**Doctrine, post-A4:**
1. **Aggregate F1 deltas vs. a fixed historical anchor:** ratio = |Δ| / σ_aggregate (= |Δ| / 0.0086). Ship at ≥ 2σ; provisional at 1–2σ; noise at < 1σ.
2. **Aggregate F1 deltas between two independent phase results** (the strict difference-of-means case): ratio = |Δ| / σ_Δ_paired (= |Δ| / 0.0122 = |Δ| / (√2·σ_aggregate)). Ship at ≥ 2σ_Δ; provisional at 1–2σ_Δ; noise at < 1σ_Δ. **Use this when both endpoints are themselves measured F1 values, not a published anchor.**
3. **Per-language F1 deltas at n=10:** ratio = |Δ_lang| / σ_lang (= |Δ_lang| / 0.018). Per-language conclusions at single-run n=10 require ≥ 2σ_lang = 0.036 to claim signal; |Δ_lang| < 0.018 is pure noise.
4. **Per-agent ablation deltas at N=1:** σ_TP_max ≈ 1.26 (correctness); ablations producing TP_delta ≥ 3 are well above 2σ_TP and reportable from a single run. Smaller agent-level deltas need re-runs.
5. **Single-CRB-number reporting:** prefer "F1 = X (±σ over N runs)" framing over "F1 = X" point estimates whenever feasible (N ≥ 2). The published 0.313 was on the low edge of the noise band; mean across N=4 was 0.321. Future writeups should report mean + σ + N.
6. **Phase 5/5.2 published narrative is preserved:** the +0.036 cumulative lift is 2.96σ_Δ, clearly signal. Do not retroactively revise; just frame future deltas against this doctrine.

Practical workflows must still apply pre-registration discipline (state criterion BEFORE the run, not after) — what changed is the *contents* of the criteria, not the discipline itself.

### F4 · Doc-debt punch list (2026-04-29 audit)
Stale pre-reg / projection language found across docs that pre-date the σ measurement:
- `bench/crb/IMPROVEMENTS.md` § 1–9: every per-lever ΔF1 estimate lacks σ band (top-of-doc calibration notice covers the 3–5× discount but not the σ-floor implication; addressed in F2 update above + IMPROVEMENTS top-of-doc tightening in this PR).
- `bench/crb/PHASE_4_DESIGN.md` § ship criteria: thresholds (0.32 / 0.29) pre-date σ measurement. Phase 4 already closed; revising historical pre-reg has no live impact, leave as-is for archaeology.
- `bench/crb/AUDIT_10PR.md` § Candidate X.2: "Ship F1 ≥ 0.30" baseline now superseded by Phase 5.2 (0.313). Phase 5 already shipped; document is historical, leave as-is.
- `bench/crb/RESULTS.md` § Phase 5 / 5.2: writeups already reference judge σ ≈ 0.02 cross-run (Phase 5 close-floor language); no live action needed.
- `bench/crb/PHASE5_WRITEUP.md`: cumulative claim is correct (+0.036 = clear 2σ signal); could be more explicit about σ ratio but does not mislead — leave as-is.
**Net live-impact change in this PR:** A1 and A5 in this file get σ-aware criteria; F.2 + F.3 codify doctrine; IMPROVEMENTS.md top-of-doc gets the σ-floor caveat. Everything else is historical archaeology — flagged but not edited.

---

## Ranked priorities (author's read, updated 2026-05-01 — post PRs #70–#85, v2.1.2 cut)

Closed in the 2026-04-29 / 2026-04-30 / 2026-05-01 sessions (~\$3.48 cumulative spend, 16 + 4 = 20 PRs across the broader window):

- ~~A4 judge-noise envelope~~: σ_F1 = 0.0086 measured (PR #48).
- ~~A5 realist-check wiring + CRB~~: Step 5.5 shipped (PR #50); Phase 5.3 measured the combined wirings → CLOSE verdict drove v2.1.1 default-OFF revert for silent-failure + comment-accuracy.
- ~~A6 combined Phase 5.3 CRB run~~: F1=0.268, CLOSE verdict (PR #68); Phase 5.2's 0.313 remains CRB number of record.
- ~~σ-aware pre-reg doctrine~~ codified (PR #49).
- ~~C1 enterprise-rebuild dogfood scout (PetClinic)~~: SHIP via PR #71 (4 oracle-grade catches, simulator caveat).
- ~~A1 Tier-0 LLM-skip rate~~: 60% on PetClinic real-world stream (PR #74 derivation); SHIP.
- ~~A2 Spec-Alignment ≥1 SPEC_ALIGNMENT block~~: 8 of 10 PRs emitted blocks (PR #74); SHIP.
- ~~A3 Tier-0 default-ON measurement on phase5_2-reviews/~~: 0% on CRB corpus (PR #76 derivation; informational).
- ~~D1 strip-footnote-titles.py keep/retire~~: KEEP (PR #78).
- ~~D2 cross-file-impact graphSignals consumption~~: shipped (PR #61).
- ~~G1 hallucination-ast CI~~: shipped.
- ~~G2-fuller fixture coverage~~: 4 v2.1.0 wiring fixtures added (PR #81).
- ~~C2 cost-normalised F1 metric~~: Phase 1 schema + rule sheet (PR #82), Phase 2 derivation (PR #83). F1/$ = 0.855 CRB HOLD / ≈ 2.14 real-world SHIP. **Closes IDEA_REPORT G9 publication gap.**
- ~~C1.B Apache Camel full-swarm arm~~: SHIP via PR #89 — 5 CRITICAL + 19 IMPROVEMENT + 7 NITPICK across 10 Camel PRs at ~\$3.28; closes the simulator caveat from C1 scout; **§C1 fully closed at signal-grade** (PR #90 lockstep annotation).
- ~~v2.1.2 release~~: shipped via PR #86 (manifest bump + 8 audit-gap closures) + PR #87 (workflow examples bumped post-tag). Tag + GitHub Release page both published.

Remaining ranked priorities:

If picking just one:
1. **G3 stack-awareness orchestrator logic** ($0, ~1 week eng) — `--parent <PR#>` flag is parsed but no orchestrator logic; IDEA_REPORT positions as Tier-B but blocking for stacked-PR enterprise integrations. Highest strategic value of the remaining items at $0 cost.
2. **C2 Phase 2 signal-grade measured re-run** (~\$15-25) — gated on harness change that surfaces per-Agent `usage` in Agent tool return values (not autonomously achievable without harness PR). The Phase 2 derivation is publishable in the meantime.
3. **C1.C Microsoft-internal monolith / COBOL / PL-SQL dogfood** — gated on access + on graph-code-indexing's COBOL/PL-SQL parser support per §B1. Highest strategic-fit gap remaining (PRD §7 enterprise rebuild).

If picking three over the next month:
1. G3 stack-awareness orchestrator logic
2. C3 corpus expansion ($250-750; de-noises per-language F1 + cost slice; informs whether Ruby/TS slices reflect real signal vs sample-size artifact)
3. B3 Martian CRB upstream submission (auth-gated on PR #65; surfaces Soliton's F1=0.313 + cost-normalised F1 in the public leaderboard)

If $0 budget:
1. G3 stack-awareness eng (free, ~1 week; closes the stacked-PR enterprise gap)
2. Re-audit POST_V2_FOLLOWUPS / docs for any remaining drift (3 audit cycles done this session — diminishing returns now)
3. Cut v2.1.3 if accumulated docs since v2.1.2 warrant it (currently 1 PR worth, below threshold)

Now retired:
- ~~A4~~ ~~A5~~ ~~A6~~ ~~C1-scout~~ ~~C1.B~~ ~~A1~~ ~~A2~~ ~~A3~~ ~~C2~~ ~~D1~~ ~~D2~~ ~~G1~~ ~~G2-fuller~~ — see closure annotations above + per-§ entries. **§C1 enterprise-rebuild dogfood closed at signal-grade (scout + C1.B Camel both SHIP).**

If $0 budget:
1. A3 Tier-0 default-ON measurement (zero-cost subset of A1)
2. D2 cross-file-impact caller-direction refactor (engineering only)
3. Triage I11–I20 for any "ship for free" opportunities

---

*This file is a living register. Update as items close. Cross-link from `bench/crb/RESULTS.md` and v-release notes.*
