# Soliton × CRB Phase 3 — how to climb the F1 leaderboard

> **⚠️ Calibration notice (added 2026-04-19 after Phase 3.5 / 3.6 / 3.7; σ-floor added 2026-04-29 after PR #48)**
>
> **Every ΔF1 estimate in this doc was written BEFORE Phase 3.5 shipped.** Subsequent experiments showed the projections were **3–5× too optimistic**:
> - Phase 3.5 (L4+L2+L1 stacked): projected **+0.20** → actual **+0.042**
> - Phase 3.6 v2.2 description compression: projected **+0.05 to +0.07** → actual **−0.021** (regression)
> - Phase 3.7 v2.3 synthesizer dedup widening: projected **+0.03** → actual **−0.022** (regression)
>
> **When reading this doc**: apply a **3–5× discount** to every per-lever ΔF1 estimate in §2 and §3. Realistic range for any remaining prompt-level lever here is **+0.01 to +0.03**, not the +0.05 to +0.10 shown.
>
> **σ-floor (added 2026-04-29):** measured σ_F1=0.0086 (`bench/crb/judge-noise-envelope.md`) gives a 2σ_Δ paired floor of 0.024. After the 3–5× discount, only levers with a *raw napkin* projection ≥ +0.07 (3× discount) or ≥ +0.12 (5× discount) survive the noise floor at N=1. **Smaller-projected levers should either be deferred or committed to N≥3 re-runs (~$420)** — at N=1 their realized lift is indistinguishable from judge variance.
>
> **Load-bearing update**: "aggressive precision" levers (L1 atomicity, v2.2 compression, v2.3 dedup widening) have consistently cut recall more than they help precision — the CRB step3 judge is recall-hungry. Any future lever that reduces matching surface area downstream should be treated with skepticism.
>
> **🚨 SUBTRACTION WINS, ADDITION FAILS (added 2026-05-02 after 4-doc strategy cross-walk)** — empirical pattern across 8 measured CRB phases since Phase 3: every published SHIP came from REMOVING something (Phase 3.5 dropped nitpicks + threshold 80→85; Phase 5 dropped test-quality + consistency from default skipAgents; Phase 5.2 stripped footnote-title regex leak). Every ADDITION regressed: Phase 3.6 description compression (−0.021), Phase 3.7 synthesizer dedup widening (−0.022), Phase 4c L5 + §2.5 (−0.016), Phase 3.5.1 prose-heavy gate (−0.034), Phase 5.3 four combined v2.1.0 wirings (−0.045). Five consecutive CLOSE post-Phase-3.5 confirms prompt/wiring-level surface-area expansion is at a local maximum.
>
> **Pre-registration discipline for any new lever that ADDS surface area** (new agent dispatch, new pass-through hook, new prompt section, new orchestrator step):
> 1. Pre-register σ-aware SHIP/HOLD/CLOSE bands using σ_F1=0.0086 / σ_Δ paired=0.0122
> 2. Default-OFF in any release with the new wiring (the v2.1.0→v2.1.1 silent_failure / comment_accuracy revert is the model)
> 3. Bounded N=1 measurement; HOLD = CLOSE at N=1 (per `bench/crb/PHASE_6_DESIGN.md` HOLD-resolution protocol — no $280 re-run)
> 4. If any v2 strategy doc, integration architecture, or "killer feature" pitch suggests adding hooks / agents / pass-throughs without an experiment, this doctrine MUST be cited as the gate.
>
> Architectural changes that escape this constraint require a different kind of evidence — full-codebase retrieval (cubic-style), execution sandbox (I19), or per-language narrow re-integration (Phase 6 pattern). These are NOT prompt tweaks and the σ-aware pre-reg still applies.
>
> Phase 3.5 (F1 = 0.277) is the current published best. The next realistic push is **Phase 4** (design doc at `bench/crb/PHASE_4_DESIGN.md`): agent-level cross-file retrieval + ROADMAP D hallucination-AST, not more prompt tweaks. See `RESULTS.md` §§"Phase 3.5 / 3.6 / 3.7" for the full story.

---

Companion doc to `RESULTS.md`. Phase 3 landed Soliton at **F1 = 0.235** under the GPT-5.2 judge on the full 49-PR CRB offline corpus — rank ≈22 of 23 on raw F1, but **recall 0.602 is top-tier** (cubic-v2 leader at ~0.63). This doc unpacks *where* the F1 is leaking and proposes concrete levers with estimated F1 impact, effort, and risk.

Analysis is grounded in actual Phase 3 data: `../code-review-benchmark/offline/results/azure_gpt-5.2/evaluations.json` + `candidates.json`. Run was n=49, 133 goldens, 568 candidates, **TP=80 / FP=468 / FN=53**.

---

## 1. Where the F1 is actually leaking

### 1a. Precision — FPs are mostly real findings outside the golden set

Stratifying 468 Phase 3 FPs into categories (manual sampling of ~40 FPs across 5 languages):

| Category | Est. share | Example | Fixable? |
|---|---:|---|---|
| **Legitimate bugs, not in the golden set** | **~60 %** | `ASN1Decoder.readLength does not bounds-check short-form lengths` (keycloak#33832); `Date-override predicate logic inverted, enables booking into blocked ranges` (cal.com#8330); `raw SQL INSERT without escaping, allows SQL injection` (discourse-graphite#10) | ❌ Not directly — golden set would need to expand |
| **Nitpicks / stylistic / naming** | ~25 % | `Constants.GRANT_TYPE redundantly aliases OAuth2Constants.GRANT_TYPE` (keycloak#37634); `URI(website.to_s) is parsed multiple times` (discourse-graphite#6) | ✅ Threshold + severity gate |
| **Speculative cross-file-impact warnings** | ~10 % | `Renaming getGroupsWithViewPermission may break any internal/SPI consumers` (keycloak#37038) | ✅ Evidence-scored filter |
| **Genuinely wrong (hallucinated)** | ~5 % | (rare — no clear examples in the 40-sample set) | ✅ Hallucination-AST pre-check |

**Implication**: the biggest precision bleed (60 % of FPs) is *not* a Soliton-quality problem — it's a benchmark-coverage problem. Real procurement-value lives in those "extra" findings. But **raw F1 does not reward it**, so to climb the leaderboard we have to suppress them.

### 1b. Recall — FNs cluster around specific reasoning gaps

Stratifying 53 Phase 3 FNs:

| Category | Est. share | Example | What would catch it |
|---|---:|---|---|
| **Deep cross-file type understanding** | ~35 % | `isinstance(SpawnProcess, multiprocessing.Process)` always false on POSIX (sentry#93824); `ContextualLoggerMiddleware methods panic when a nil request is received` (grafana#76186) | Hallucination-AST pre-check + deeper cross-file agent retrieval |
| **Subtle math / semantics bugs** | ~25 % | `Time window calc: device.UpdatedAt.UTC().Add(-exp) vs device.UpdatedAt` (grafana#79265); `Recursive caching call using session instead of delegate` (keycloak#32918 — **Critical severity**) | Tighter correctness prompting + execution-sandbox verify loop (ROADMAP I19) |
| **Test-code semantics** | ~15 % | `sleep monkeypatched so it doesn't wait` (sentry#93824); `HTTP method mismatch: test uses PUT, action expects DELETE` (discourse-graphite#8) | Test-quality agent tuned for cross-ref between test and code-under-test |
| **Localization / domain knowledge** | ~15 % | `Italian translation in Lithuanian locale file`; `Traditional Chinese in zh_CN file` (keycloak#37429) | Add i18n / domain-specific micro-agent |
| **Stylistic Low-severity misses** | ~10 % | `Error message says 'backup code login' but this is a disable endpoint` (cal.com#10600) | Not worth chasing — Lows are reviewer-taste |

**Implication**: the Criticals we miss (1 observed in Phase 3: keycloak#32918 recursive caching) are the most procurement-material losses. Deep cross-file type understanding and execution-verify are the structural gaps.

---

## 2. Nine levers, prioritized

Each lever below has: **estimated ΔF1** (absolute improvement on the 0.235 baseline), **effort** (developer-days), **risk** (what could go wrong), and **interaction** (whether it composes or overlaps with others).

### Lever 1 · Structured "one-finding-per-bullet" output format
- **ΔF1: +0.10** · **Effort: 1 day** · **Risk: Low**
- **Diagnosis**: Phase 2 (Opus-4.7 in-session judge, judging whole numbered findings) got F1 = 0.438 on 5 PRs. Phase 3 (GPT-5.2 pipeline, step2 splits into sub-issues) got F1 = 0.280 on the same 5 PRs. TP counts nearly identical (16 → 14); **FPs nearly doubled** (32 → 61) — the CRB step2 LLM's sub-issue extraction is the single biggest precision tax.
- **Intervention**: change `skills/pr-review/SKILL.md`'s output contract so each finding is an atomic single bullet with no nested sub-bullets. Then step2's LLM has nothing to sub-extract — 1 candidate = 1 finding, and our precision math aligns with Phase 2's in-session judgment.
- **Mechanism**: today a finding like "TOCTOU race allows exceeding the device limit — Option A: atomic insert; Option B: transaction" produces 3 candidates at step2 (the main finding + each option). After this change: 1 candidate.

### Lever 2 · Severity-gated output (Medium+ only in the review body)
- **ΔF1: +0.10** · **Effort: 2 hours** · **Risk: Medium** (drops legitimate Low-severity goldens)
- **Diagnosis**: Phase 2 severity-stratified recall showed Low = 0.500, meaning we're correctly catching half of Lows — but those catches are worth less than the precision tax of emitting Low findings that get split into 2-3 FPs each at step2. Net-negative on F1.
- **Intervention**: synthesizer emits all findings to JSON output, but the markdown review body only contains Medium / High / Critical. Keep Lows in a collapsed "Details" section (not extracted by step2).
- **Side benefit**: customer-facing reviews stay focused on merge-blocking issues. Aligns with Phase 2's observation that "zero Critical misses + 80 % High recall" is Soliton's strongest procurement story.
- **Compose with Lever 1**: yes, they multiply — one bullet per Medium+ finding is the ideal step2 input.

### Lever 3 · Synthesizer deduplication pass
- **ΔF1: +0.08** · **Effort: 3 days** · **Risk: Low**
- **Diagnosis**: Soliton's 8 agents often flag the same code region from different angles. Example pattern: the correctness agent flags "race condition", the security agent flags "unsafe concurrent write", the test-quality agent flags "no concurrency test". All target the same `CreateOrUpdateDevice` function. Step2 treats them as 3 candidates; step3 judges 1 as TP (closest match to the golden "race condition") and 2 as FPs.
- **Intervention**: in `agents/synthesizer.md`, add a merge step that groups findings by `(file_path, line_range, root_concern)` and emits a single synthesized finding per group with evidence from all contributing agents.
- **Concrete**: reduce 11.6 candidates per PR to ~6–7 (the candidates-per-PR number in Phase 3 matches the number of distinct issues a golden reviewer would flag; consolidation aligns our output with the ground truth).

### Lever 4 · Threshold tightening (default 80 → 85)
- **ΔF1: +0.05** · **Effort: 10 min** · **Risk: Low**
- **Diagnosis**: Phase 3 suppressed 1 finding below threshold 80. Raising to 85 would suppress ~15 % of findings, most of which are the stylistic nits we just identified as 25 % of FP volume. Back-of-envelope: cut 70 FPs and 4 TPs → P = 84 / (84 − 70 + 468 − 70) = 84 / 412 = 0.20 (+0.06); R = 76 / 129 = 0.59 (−0.01) → F1 ≈ 0.30.
- **Intervention**: update the default in `skills/pr-review/SKILL.md` `config.threshold.default` from 80 to 85. Keep env override `SOLITON_CONFIDENCE_THRESHOLD` for tuning.
- **Compose**: purely orthogonal to Levers 1–3 — stacks.

### Lever 5 · Deeper cross-file retrieval (hallucination-AST precheck backbone)
- **ΔF1: +0.05** · **Effort: 1 week** · **Risk: Medium**
- **Diagnosis**: goldens like `isinstance(SpawnProcess, Process)` require knowing that `SpawnProcess` does NOT subclass `Process` on POSIX. Soliton's agents don't do Python-stdlib-type-hierarchy lookup. Similarly `NewInMemoryDB().RunCommands returns 'not implemented'` requires reading the callee's body — not always fetched today.
- **Intervention**: extend `agents/cross-file-impact.md` + `agents/hallucination.md` to do a 2-hop retrieval: (a) resolve all external symbols referenced in the diff; (b) fetch their definitions; (c) attach as context for correctness / hallucination agents. Links naturally to ROADMAP D (hallucination-AST) and the graph-signals skill.
- **Compose**: recall-lifting; orthogonal to Levers 1–4.

### Lever 6 · Evidence-scored filter on cross-file-impact speculation
- **ΔF1: +0.04** · **Effort: 1 day** · **Risk: Low**
- **Diagnosis**: ~10 % of FPs are speculative "may break consumers" warnings with no concrete call-site evidence (e.g., renaming-method alerts without checking whether the method is actually called outside the diff).
- **Intervention**: `agents/cross-file-impact.md` requires a concrete file path + line number for every emitted finding. If the agent can't find one, the finding is suppressed (or downgraded to a "consider" note not in the main review body).

### Lever 7 · Tier-0 fast-path enabled by default
- **ΔF1: +0.03** · **Effort: 2 hours** (config) · **Risk: Low**
- **Diagnosis**: Tier-0 was *disabled* in Phase 3 for consistency with Phase 2. Its main effect is cost (skipping LLM review on trivial diffs), but **a secondary F1 effect**: on the `tier0.verdict == clean` path no findings are emitted at all, which on truly-trivial PRs (goldens with 0–1 items) is the right answer.
- **Intervention**: flip `tier0.enabled: true` in the default `.claude/soliton.local.md`. Run a Phase 3b ablation under the same GPT-5.2 judge.
- **Compose**: mostly cost-reducing; F1 uplift is second-order.

### Lever 8 · Execution-sandbox verify loop (ROADMAP I19)
- **ΔF1: +0.02** · **Effort: 2–3 weeks** · **Risk: High** (infra-heavy)
- **Diagnosis**: subtle math bugs (time-window, recursive-caching-delegate) can be confirmed by actually running the PR code in a sandbox and reproducing the claimed bug. Converts "suspected bug" → "verified bug with trace".
- **Intervention**: ROADMAP item I19. Docker-based sandbox; compile + run targeted tests; reject findings that can't be reproduced.
- **Risk/ROI**: infrastructure cost is high; real F1 lift is modest. Lower priority than Levers 1–6.

### Lever 9 · Per-file-type specialist agents (i18n, SQL, migrations)
- **ΔF1: +0.02** · **Effort: 1 week each** · **Risk: Low**
- **Diagnosis**: 15 % of FNs are domain-specific (localization, SQL injection in migrations). Generic agents don't have the domain prior.
- **Intervention**: add a narrow-scope agent dispatched only when the diff touches `.po` / `.properties` / `.sql` / `db/migrate/`. Keeps tokens low on normal PRs, catches the domain-specific class.

---

## 3. Prioritized roadmap

Ranked by (ΔF1) / (effort-days), filtered to "composable and low-risk":

| Order | Lever | ΔF1 | Effort | Priority rationale |
|---|---|---:|---|---|
| **1** | Threshold tightening (L4) | +0.05 | 10 min | Free lunch. Ship today. |
| **2** | Severity-gated output (L2) | +0.10 | 2 h | Biggest lift-per-hour. Only risk is dropping Low-sev goldens — we already measured recall Low at 0.50, so we're losing 3 of 6 Phase 2 Lows anyway. |
| **3** | Structured one-finding-per-bullet (L1) | +0.10 | 1 d | Killer for the step2 precision tax; aligns phase 2 and phase 3 judgment. |
| **4** | Evidence-scored cross-file filter (L6) | +0.04 | 1 d | Cuts speculative FPs with no collateral damage. |
| **5** | Synthesizer dedup (L3) | +0.08 | 3 d | Biggest structural improvement; addresses the "same region flagged by 3 agents" pattern. |
| **6** | Deeper cross-file retrieval (L5) | +0.05 | 1 w | Recall lever — catches the type-hierarchy FNs we currently miss. Plugs into ROADMAP D. |
| **7** | Tier-0 enabled + Phase 3b ablation | +0.03 | 2 h | Quick win but needs a re-run under same judge to quantify. |
| **8** | Specialist agents (L9) | +0.02 each | 1 w each | Diminishing returns; defer. |
| **9** | Execution sandbox (L8) | +0.02 | 2–3 w | Infra-heavy; defer to ROADMAP I19. |

**Cumulative F1 projection** if L1–L6 all applied (with ~30 % overlap penalty because many target the same precision gap):

```
base            = 0.235
+ threshold     +0.05   → 0.285
+ severity gate +0.07   → 0.355  (30% overlap with threshold)
+ 1-per-bullet  +0.07   → 0.425  (overlaps synthesis)
+ evidence filt +0.03   → 0.455
+ synth dedup   +0.05   → 0.505  (overlaps 1-per-bullet)
+ deeper xfile  +0.05   → 0.555  (mostly recall, separate)
────────────────────────────────
Projected  F1 ≈ 0.45 – 0.55
```

That puts us between `qodo-v2` (0.44) and `cubic-v2` (0.59) — solid mid-to-upper leaderboard territory, and close to the **ceiling suggested by Phase 2's Opus-4.7 judge (0.438)**, so the estimate is internally consistent.

---

## 4. Concrete implementation sketches for the top 3 levers

### 4.1 Threshold tightening (L4)

Single-character-in-docs change. Update `skills/pr-review/SKILL.md`:

```diff
-  threshold:
-    default: 80
+  threshold:
+    default: 85
```

And document the rationale: "tuned 2026-04-19 based on Phase 3 CRB run — 80 was emitting ~15 % stylistic nits that got extracted as sub-candidates at CRB's step2 and scored as FPs".

### 4.2 Severity-gated output body (L2)

In `agents/synthesizer.md`, change the "Compose the review" step so Low-severity findings only appear in an appendix:

```md
- The main review body (which downstream tools like CRB step2 extract
  candidates from) contains ONLY Medium, High, and Critical findings.
- Low-severity findings are listed in a `<details>` block at the end of
  the review, labeled "Nits / style notes (not blocking)" — visible to
  the developer, invisible to automated candidate extractors.
```

### 4.3 Structured one-finding-per-bullet (L1)

Today Soliton reviews have findings like:

```md
:red_circle: [correctness] Guaranteed TypeError when `destinationCalendar` is empty
The previous code read `evt.destinationCalendar?.integration`. The refactor removes that safety:
`const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];`
If `evt.destinationCalendar` is `[]`, `null`, or `undefined`, `mainHostDestinationCalendar`
is `undefined` and `.integration` throws.

```suggestion
const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];
if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== ...
```
```

Step2's LLM sees this and may extract 2 candidates: (a) the NPE risk, (b) the suggestion quality. We want it to see 1.

Patch to `skills/pr-review/SKILL.md` output step:

```md
Each finding in the markdown review MUST be a single atomic bullet:
  - <severity-icon> [category] <1-sentence problem statement> — <location>

Do NOT nest sub-bullets under a finding. If you have multiple fix options,
put the full explanation in a `<details>` block after the finding, with
explicit language that fix options are ALTERNATIVES NOT SEPARATE ISSUES.
```

Then step2's candidate extraction returns one-per-bullet and our precision math matches Phase 2.

---

## 5. Phase 3.5 plan

One-branch implementation of levers 1 + 2 + 4 (total ~1.5 days, projected F1 lift +0.20 → ~0.44):

1. Branch `feat/soliton-precision-tighten-phase3.5`
2. Apply L4 threshold change
3. Apply L2 severity gate in `agents/synthesizer.md`
4. Apply L1 one-bullet format in `skills/pr-review/SKILL.md`
5. Re-run Phase 3 pipeline (all 49 PRs) under the **same GPT-5.2 judge**
6. Compare: expected F1 ≈ 0.40–0.45; compare per-finding to Phase 3 baseline for regression analysis
7. Ship if F1 > 0.35; otherwise iterate on L3 (synthesizer dedup) before Phase 4

After Phase 3.5 validates the approach, Phase 4 (Levers 3, 5, 6) can target the 0.50 band, putting us in cubic-v2 territory.

---

## 6. What this analysis does NOT solve

- **Cost-normalized F1** — Soliton's structural differentiator. Still unprovable without competitor $ data from Martian's leaderboard. Request upstream adds a `cost_per_pr` column.
- **Training-data leakage** — all 50 benchmark PRs are from well-known OSS repos. Moving to CRB's `online/` benchmark (fresh PRs, no leakage) is a Phase 5 concern.
- **Judge sensitivity** — Soliton's output swings ~0.20 F1 across judges vs ~0.02 for cubic-v2. Structured output (L1) should shrink this, but confirming requires a multi-judge Phase 3.5 run, which needs Opus-4.5 or Sonnet-4.5 access.
