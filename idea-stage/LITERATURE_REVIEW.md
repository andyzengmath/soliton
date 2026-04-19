# Literature Review: AI-Powered Code Review for CI-Integrated Systems (2024 – April 2026)

> Produced by a parallel research sub-agent during Stage 1 of `/research-pipeline`.
> Independent survey. Primary source citations are in Section E. Numbers without a primary
> source are marked `[unverified]`.

---

## Section A — Most important papers (10 + honourable mentions)

### A1. Sun et al. 2025 — *Does AI Code Review Lead to Code Changes?* (arXiv 2508.18771)
22,326 comments / 16 GH Actions / 178 repos. **AI addressing 0.9–19.2 % vs. human 60 %.** Hunk-level inline > file-level. Manually-triggered > auto-triggered. Newcomers ~5× more responsive than experienced contributors.

### A2. Tantithamthavorn et al. 2026 — *RovoDev Code Reviewer* (Atlassian, arXiv 2601.01129)
Production over 1,900+ repos / 54k+ comments / 1 year. **38.70 % resolution vs. 44.45 % human.** 3-component arch: zero-shot Sonnet generator → LLM-judge factual check → ModernBERT actionability filter. **31 % PR cycle-time reduction, 35.6 % human-comment reduction.** Actionability filter (+15-20 pp) > factual filter.

### A3. Zhong et al. 2026 — *Human-AI Synergy in Agentic Code Review* (Queen's, arXiv 2603.15911)
278,790 inline convos / 300 projects / 54k PRs. **Human adoption 56.5 % vs. AI-agent 16.6 %.** AI suggestions: 29.6 tokens/LOC vs. 4.1 for humans. AI increases cyclomatic complexity **10-50×** more than human ones. **85-87 % of AI reviews end after first comment.** Over half of unadopted AI suggestions were incorrect or superseded.

### A4. Zeng et al. 2025 — *SWR-Bench* (arXiv 2509.01494)
1,000 manually verified PRs / 12 projects. Top-model F1 **~19 %** (Gemini-2.5-Pro 19.38 %, DeepSeek-R1 18.58 %, GPT-4o 18.73 %). **Precision < 20 % across models.** Multi-Review ensemble (10 independent → aggregate) pushes F1 to **43.67 %**. Functional-change detection F1 26 % vs. evolutionary 14 %.

### A5. Pereira et al. 2026 — *CR-Bench* (Nutanix, arXiv 2603.11078)
584 instances / 174 verified. Introduces **Bug Hit / Valid Suggestion / Noise** 3-way + **Signal-to-Noise Ratio**. **Reflexion increases recall but collapses SNR** to 0.91 on smaller models.

### A6. Kumar 2026 — *SWE-PRBench* (Foundry-AI, arXiv 2603.26130)
350 PRs / 65 repos / 3 frozen context configs (diff-only / diff+file / full). Detection **15-31 %** on config_A. **All 8 frontier models show monotonic decline as context grows** — attention-representation limits, not retrieval. Judge κ = 0.616 (Sonnet 4.6 vs. GPT-5.2).

### A7. Khati et al. 2026 — *Detecting & Correcting Hallucinations via Deterministic AST* (W&M, arXiv 2601.19106)
**Zero-LLM** AST analyzer against a library-introspection KB. **100 % precision, 87.6 % recall, F1 = 0.934** (161 hallucinated + 39 clean Python samples). Auto-correction **77 %**. Directly relevant to Soliton's `hallucination.md` agent — much of that agent's work could be delegated to a deterministic tool.

### A8. Lin et al. 2026 — *Fine-grained Confidence Calibration* (arXiv 2604.06723)
Token-level confidence (min / lowest-K / attention-weighted) + local Platt-scaling via HDBSCAN on embeddings. **ECE 0.01-0.08** across repair / vulnerability / refinement. Sequence-level LLM self-reporting is uninformative.

### A9. Hora & Robbes 2026 — *Are Coding Agents Over-Mocking?* (arXiv 2602.00409)
1.2M+ commits / 2,168 repos / Claude + Copilot + Cursor + Aider + OpenHands + Devin. Agents mock in **36 %** of test commits vs. **26 %** non-agents. Agents concentrate 95 % of mocks on "mock" type vs. humans' diverse mock/fake/spy/dummy. Empirical validation of Soliton's test-quality agent.

### A10. Schreiber & Tippe 2025 — *Security Vulnerabilities in AI-Generated Code* (arXiv 2510.26103)
7,703 AI-generated files / 1.2M LOC / **4,241 CWE instances across 77 types**. 12.1 % of files contain ≥1 vulnerability. **Python 16-18.5 % vs. JS 8.7-9 % vs. TS 2.5-7 %.** Top CWEs: CWE-89 SQLi, CWE-78 OS-Command-Injection, CWE-94 Code-Injection, CWE-259/798 hardcoded-creds.

### Honourable mentions
- **Duy Minh et al. 2026** (arXiv 2601.00753): AUC 0.9571 for review-effort prediction from structural signals; **intercepts 69 % of high-burden PRs at 20 % review budget**. Semantic features add only AUC 0.52-0.57.
- **Haider & Zimmermann 2026** (arXiv 2601.19287): 19k agentic PR comments; rejected ones have significantly more security + build-config issues.
- **Chowdhury et al. 2026** (arXiv 2604.03196): **CRA-only PRs merge at 45.2 % vs. 68.4 % for human review.**
- **Liang et al. 2026 — Argus** (arXiv 2604.06633): 5-agent security ensemble: **$2.54 / repo, 0.44 hr runtime**, found multiple assigned CVEs.
- **Zhang et al. 2026 — Sphinx** (MS + Rochester, arXiv 2601.04252): Checklist Reward Policy Optimization, **+40 %** checklist coverage over GPT-4.
- **METR 2025**: RCT / 16 experienced OSS devs — **AI slowed experienced developers by ~19 %** despite a perceived +20-24 % speedup.

---

## Section B — Key empirical findings (tables)

### B1. Actionability head-to-head

| System / study | Dataset | Human | AI | Gap |
|---|---|---|---|---|
| Sun et al. 2025 (16 GH Actions) | 178 repos / 22 k comments | 60 % | 0.9-19.2 % | 40-59 pp |
| Zhong et al. 2026 (300 projects) | 278 k inline convos | 56.5 % | 16.6 % | 39.9 pp |
| Chowdhury et al. 2026 | 3,109 PRs | 68.4 % merged | 45.2 % merged | 23.2 pp |
| RovoDev 2026 (Atlassian prod) | 1,900 repos | 44.45 % | 38.70 % | 5.75 pp |
| Qodo industry study 2024 | 10 projects | — | **73.8 %** acted upon | — |

**Takeaway**: actionability spans an order of magnitude (0.9 → 73.8 %). Hunk-specific + concise + manually-triggered + post-filtered comments close the gap. Target ≥ 20 % to beat public SOTA; ideal 35-40 % (RovoDev-class).

### B2. PR-review benchmark quality

| Benchmark | Size | Best detection | Notes |
|---|---|---|---|
| SWR-Bench (2025) | 1,000 PRs | 19.4 % (Gemini 2.5 Pro); 43.7 % multi-review | Precision < 20 % |
| CR-Bench (2026) | 584 / 174 verified | SNR-based | Reflexion hurts SNR |
| SWE-PRBench (2026) | 350 PRs | 15-31 % config_A | **Monotonic decline as context grows** |
| Sphinx (2026) | 2,500 eval / 41.7 k train | +40 % over GPT-4 | Checklist RL |

### B3. AI-code error profile (detector targets)

| Error class | Empirical rate | Source |
|---|---|---|
| CWE-bearing files (Python) | 16-18.5 % | Schreiber & Tippe 2025 |
| Package hallucination (OSS models) | 21.7 % | Spracklen et al. 2024 |
| Package hallucination (commercial) | ≥ 5.2 % | same |
| Over-mocking (agent tests) | 36 % vs. 26 % | Hora & Robbes 2026 |
| Low-freq API calls (GPT-4o correct) | 38.58 % | Zhou et al. 2024 |
| AI code with ≥ 1 vuln (formal verification) | **55.8 %** | Broken by Default 2026 |
| Privilege-escalation paths | +322 % vs. human | Apiiro 2025 [unverified source] |
| Design flaws | +153 % vs. human | Apiiro 2025 [unverified source] |

### B4. Cost / latency / scale (enterprise)

| Study | Cost | Latency | Scale |
|---|---|---|---|
| RovoDev (Atlassian) | not disclosed | 31 % PR cycle-time reduction | 1,900 repos / 54 k comments/yr |
| Argus (security, 5 agents) | **$2.54 / repo** | 0.44 hr / repo | 7 Java repos, 100 k+ LOC each |
| Enterprise platform 2025 | $30-34 / eng / mo | 2-3 s code-suggestion | LLM API = 91.5 % of TCO |
| Duy Minh et al. 2026 (triage) | — | zero-latency triage | **69 % effort @ 20 % budget** |

### B5. LLM-as-judge agreement

| Study | Metric | Value |
|---|---|---|
| SWR-Bench | LLM-human agreement | ~90 % |
| SWE-PRBench | κ (Claude vs. GPT) | 0.616 |
| SWE-PRBench | κ vs. rubric | 0.75 |
| SE-Jury | Near inter-annotator | codegen / APR |

LLM-judge reliability (~90 %) is enough for automated review-quality gating.

---

## Section C — Research gaps relevant to Soliton

1. **Risk-score-driven agent dispatch is essentially unstudied in peer-reviewed venues.** All multi-agent review papers through April 2026 (AgentMesh, 4-agent 2024 paper, Argus) run a **fixed agent set**. Soliton's 2-7 conditional dispatch is **novel** as of this snapshot. Duy Minh 2026 validates the premise (AUC 0.957) but doesn't use it to dispatch reviewers.

2. **"Blast radius" for review is industrial, not academic.** Port / Relyance blogs exist; **no peer-reviewed work** uses a codebase dependency graph to scope which agents run on which PR files. Closest academic: TDAD 2026, RANGER, LocAgent, RepoGraph — all for localization, not review. **Graph-RAG for review is a green field.**

3. **Differential pre/post-PR graph analysis: zero papers found.** No published method compares dependency graphs before and after a PR to direct review attention to new cycles, new cross-module edges, or new data-flow taints. AgentArmor uses PDGs at agent runtime but not PR review.

4. **Context grows → performance drops (SWE-PRBench).** Soliton's per-agent narrow slices likely *avoid* this degradation but no ablation has been published to confirm.

5. **Multi-agent deliberation vs. false positives.** CR-Bench's Reflexion-collapse + SWR-Bench's Multi-Review success → **parallel > iterative**. But the exact signal-aggregation function (vote / max-confidence / calibrated Bayesian) is under-explored.

6. **The "slop at scale" regime (1000s PRs/day) has no dedicated study.** METR covers 16 devs, RovoDev 1,900 repos at normal throughput, AIDev 932 k agentic PRs empirically — nobody has quantified review-fatigue at the volume our enterprise use case assumes.

7. **Calibration of multi-agent ensembles.** Lin 2026 calibrates a *single* reviewer. **No paper calibrates a portfolio** of heterogeneous specialists into a unified score. Exactly what Soliton needs.

8. **Hallucination detection beyond APIs.** Khati 2026 is 100 % precision on API/identifier hallucinations. **Plausible-but-wrong logic**, spec drift, wrong exception handling — still lack high-precision detectors. SemGuard 2025 is embedded in decoder, not post-hoc review.

9. **AI-generated tests specifically are under-reviewed.** Over-mocking, assertion-free, mock-all-pass tests are empirically documented but no review tool targets them. Soliton's `test-quality.md` is addressing a measured gap.

---

## Section D — Design implications for Soliton

### D1. Actionability is the primary KPI
Public SOTA = 0.9-19.2 %; production = 38-40 %. Measure **addressing / resolution rate**, not comment volume. Actionability filter (RovoDev-style ModernBERT) gives +15-20 pp — higher leverage than factual filter.

### D2. Risk-score gating is empirically justified — but mostly structural
Structural signals alone (size, file count, patch shape) give AUC 0.957; semantic signals only 0.52-0.57. **Soliton's risk score should be predominantly structural — graph blast radius + diff stats + sensitive paths — with semantic only for last-mile escalation.** Do not burn LLM tokens computing risk.

### D3. Context degradation is a design constraint
Per-agent, purpose-built context slices > full-repo dump. Soliton's specialization is directionally correct; confirm via ablation.

### D4. Differential graph integration is the biggest differentiator
Biggest academic gap (Section C.2, C.3) is also Soliton's biggest moat: **differential blast radius** (functions whose data flow changes, modules whose fan-in shifts) as (a) risk-score input and (b) per-agent context selector. LocAgent showed 92.7 % localization at 86 % cost reduction — unreplicated in the *review* setting.

### D5. Explicit ensemble calibration + defer-to-human threshold
Multi-Review (+24 pp F1) + Lin-style token-level Platt-scaled confidence should drive:
- per-agent confidence aggregated using **token-level calibrated** scores
- publish comments with ≥ 70 % calibrated confidence (trades recall for SNR)
- auto-escalate to human when ensemble disagreement is high

Without calibration, Soliton will reproduce the 0.9-19.2 % floor. With it, 30-40 % is realistic.

---

## Section E — Sources

Primary papers (arXiv):
- [2508.18771 — Sun et al. 2025](https://arxiv.org/html/2508.18771v1) · [2601.01129 — RovoDev](https://arxiv.org/html/2601.01129v1) · [2603.15911 — Zhong 2026](https://arxiv.org/html/2603.15911v1) · [2509.01494 — SWR-Bench](https://arxiv.org/html/2509.01494v1) · [2603.11078 — CR-Bench](https://arxiv.org/html/2603.11078) · [2603.26130 — SWE-PRBench](https://arxiv.org/html/2603.26130) · [2601.19106 — Khati deterministic AST](https://arxiv.org/html/2601.19106v1) · [2604.06723 — Lin calibration](https://arxiv.org/abs/2604.06723v1) · [2602.00409 — Hora over-mocking](https://arxiv.org/html/2602.00409) · [2510.26103 — Schreiber & Tippe](https://arxiv.org/abs/2510.26103) · [2601.00753 — Duy Minh](https://arxiv.org/html/2601.00753v1) · [2601.19287 — Haider & Zimmermann](https://arxiv.org/html/2601.19287v1) · [2604.03196 — Chowdhury](https://arxiv.org/html/2604.03196) · [2604.06633 — Argus](https://arxiv.org/html/2604.06633) · [2601.04252 — Sphinx](https://arxiv.org/html/2601.04252v1) · [2601.17581 — How AI Coding Agents Modify Code](https://arxiv.org/html/2601.17581v2) · [2602.13377 — CR Benchmark Survey](https://arxiv.org/abs/2602.13377) · [2505.16339 — Rethinking Review Workflows](https://arxiv.org/html/2505.16339v1) · [2412.18531 — Qodo in practice](https://arxiv.org/abs/2412.18531) · [2503.09089 — LocAgent ACL 2025](https://aclanthology.org/2025.acl-long.426/) · [2410.14684 — RepoGraph ICLR 2025](https://arxiv.org/html/2410.14684v1) · [2603.17973 — TDAD](https://arxiv.org/html/2603.17973v1) · [2603.14619 — Release Intelligence](https://arxiv.org/abs/2603.14619) · [2508.01249 — AgentArmor PDG](https://arxiv.org/html/2508.01249) · [2509.25257 — RANGER](https://arxiv.org/html/2509.25257v1) · [2511.01047 — HAFixAgent](https://arxiv.org/abs/2511.01047) · [2509.15433 — SAST-Genius](https://arxiv.org/pdf/2509.15433) · [2510.02534 — ZeroFalse](https://arxiv.org/html/2510.02534) · [2604.05292 — Broken by Default](https://arxiv.org/html/2604.05292v2) · [2406.10279 — Package Hallucinations](https://arxiv.org/abs/2406.10279) · [2409.20550 — LLM Hallucinations in Practice](https://arxiv.org/abs/2409.20550) · [2407.09726 — API Doc Mitigation](https://arxiv.org/abs/2407.09726) · [2404.18496 — 4-Agent AI Code Review](https://arxiv.org/html/2404.18496v2) · [2507.19902 — AgentMesh](https://arxiv.org/html/2507.19902v1) · [2604.14228 — Claude Code Design Space](https://arxiv.org/abs/2604.14228)

Industry:
- [METR — 19 % slowdown RCT](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)
- [Port — Blast Radius with AI](https://docs.port.io/guides/all/calculate-blast-radius-with-ai/)
- [Jet Xu — Low-Noise Code Review](https://jetxu-llm.github.io/posts/low-noise-code-review/)
- [Claude Code — Review docs (parallel agent arch)](https://code.claude.com/docs/en/code-review)
- [Qodo — Best AI Code Review Tools 2026](https://www.qodo.ai/blog/best-ai-code-review-tools-2026/)
