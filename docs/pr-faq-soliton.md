# Soliton PR/FAQ

## Press Release

### Soliton: PR Review on Autopilot -- From Human-Assisted to Fully Autonomous

**One system, three modes: help humans review faster, auto-approve low-risk PRs, or run fully autonomous review for your entire codebase**

**San Francisco, CA -- September 2025**

Today, Soliton launches as the first PR review system built for teams where both humans and AI agents write and review code. Available as a GitHub App and open-source CLI, Soliton introduces a three-mode autonomy model -- Assist, Autopilot, and Autonomous -- that lets teams dial the level of human involvement up or down as their trust in automated review grows.

**The problem.** Software teams face a review crisis that gets worse from both directions. On one side, AI coding agents (Copilot, Cursor, Devin, Claude Code, OpenHands) are generating an estimated 30-50% of new PRs in early-adopter teams -- and this is growing fast. On the other side, human reviewer capacity is flat. The result: review queues balloon, cycle times stretch from hours to days, and developers either rubber-stamp PRs or skip review entirely.

This creates three distinct pain points depending on where a team is on the AI adoption curve:

- **Teams still mostly human-authored:** Reviewers are overwhelmed by large PRs and spend 60% of review time on comprehension, not judgment. They need help understanding changes faster so they can focus on what matters.
- **Teams with mixed human + AI code:** Not all PRs carry the same risk. A 3-line config change and a 500-line auth refactor should not require the same review process. These teams need risk-based routing so humans review what matters and low-risk changes flow through automatically.
- **Teams running AI agents at scale:** When agents generate dozens of PRs per day, human review becomes the bottleneck that defeats the purpose of AI coding. These teams need a fully autonomous reviewer that can approve, request changes, or block -- without a human in the loop.

No existing tool serves all three. Static analysis tools (SonarCloud, ESLint) catch syntax but miss logic. AI review tools (CodeRabbit, Copilot Review) provide a single mode -- assist humans -- with no path to autonomy. And autonomous coding agents (Devin, OpenHands) can review code but weren't designed as a dedicated, configurable quality gate.

**How Soliton works.** Soliton dispatches a team of 5-7 specialized review agents in parallel -- spec compliance, hallucination detection, security, test quality, code consistency, historical context, and cross-file impact. Each finding is scored 0-100 for confidence, and only findings above 80 are surfaced. This multi-agent + confidence filtering architecture produces 3-5 high-signal findings per PR instead of 30 noisy ones.

What makes Soliton different is the **autonomy dial**:

**Assist Mode** -- Soliton reviews every PR and posts findings as comments. Humans make all merge decisions. Soliton makes large PRs easier to understand with change summaries, risk annotations, and focused review guidance ("skip these 12 files -- they're auto-generated; focus your attention on these 3 files that touch the auth layer").

**Autopilot Mode** -- Soliton uses a codebase dependency graph to calculate risk scores for each PR. Low-risk PRs (config changes, dependency bumps, auto-generated code, well-tested changes to isolated modules) are auto-approved. Medium and high-risk PRs are routed to human reviewers with Soliton's analysis pre-attached. Teams configure their risk threshold and which file paths or change types require human sign-off.

**Autonomous Mode** -- Soliton reviews and makes merge decisions on all PRs without human involvement. It approves clean PRs, requests changes with specific fix instructions on problematic ones, and blocks PRs with critical issues. Teams can still override any decision. This mode is designed for repositories where AI agents generate the majority of PRs and the team has built sufficient trust in Soliton's judgment through Assist and Autopilot.

**"We started on Assist Mode and used it for two months. Once we saw that Soliton was catching real bugs we missed and its false positive rate was under 3%, we moved to Autopilot,"** said a VP of Engineering at a 35-person startup. **"Our human reviewers now only see the PRs that actually need their judgment. Review cycle time dropped from 18 hours to 2 hours. We'll move to Autonomous Mode for our internal tools repo next quarter."**

**Getting started.** Install the Soliton GitHub App and add a `.soliton.yaml` file. All repositories start in Assist Mode. As Soliton reviews PRs and you see its precision, upgrade to Autopilot or Autonomous when ready. For self-hosting, the Soliton CLI runs in any CI pipeline.

```yaml
# .soliton.yaml
mode: autopilot  # assist | autopilot | autonomous

autopilot:
  auto_approve_when:
    risk_score: below 30
    file_patterns:
      - "docs/**"
      - "*.md"
      - "generated/**"
  require_human_when:
    risk_score: above 70
    file_patterns:
      - "src/auth/**"
      - "src/payments/**"
      - "infrastructure/**"

agents:
  - spec-compliance
  - hallucination-detector
  - security
  - test-quality
  - code-consistency
  - cross-file-impact
```

**"The future of code review isn't human OR machine -- it's a spectrum,"** said [Company Leader]. **"Some PRs need human judgment. Most don't. Soliton lets each team find their own balance and shift it over time as trust grows. We built the system that takes you from human-assisted all the way to fully autonomous review -- one mode at a time."**

---

## FAQ

### External FAQ (Customer Questions)

**Q: How is Soliton different from CodeRabbit, GitHub Copilot Review, or other AI review tools?**

A: Existing tools offer a single mode: assist humans by posting comments. Soliton offers three modes on an autonomy spectrum. In Assist Mode, Soliton does what other tools do -- but with higher precision (multi-agent architecture, confidence scoring, 85% actionability rate vs. 40-50% industry average). In Autopilot Mode, Soliton goes further: it calculates PR risk using a codebase dependency graph and auto-approves low-risk changes, routing only meaningful PRs to human reviewers. In Autonomous Mode, Soliton makes merge decisions independently. No other tool offers this progression from assisted to autonomous review.

Additionally, Soliton has specialized detection for LLM-specific failure modes (hallucinated APIs, plausible-but-wrong logic, tests that test mocks) that general-purpose tools don't address.

**Q: What does Soliton actually check?**

A: Soliton runs parallel specialized agents, each focused on one review dimension:

| Agent | What It Catches |
|-------|----------------|
| **Spec Compliance** | PR doesn't fully implement what was requested; missing acceptance criteria; scope creep |
| **Hallucination Detector** | API calls to non-existent functions; wrong method signatures; incorrect library versions |
| **Security Reviewer** | Auth bypasses specific to *your* auth model; OWASP issues in context of your stack |
| **Test Quality** | Tests that verify mock behavior; missing edge case coverage; plausible but meaningless assertions |
| **Code Consistency** | Patterns that violate *your* codebase conventions (not generic style rules) |
| **Historical Context** | Issues similar to bugs caught in past PRs on the same files |
| **Cross-File Impact** | Changes that break callers, interfaces, or contracts in other files |

Each finding includes: severity, file:line reference, explanation of why it matters, and a concrete suggested fix.

**Q: How does risk scoring work in Autopilot Mode?**

A: Soliton builds a dependency graph of your codebase and uses it to calculate a risk score (0-100) for each PR based on:

- **Blast radius**: How many other files/modules depend on the changed code? A change to a utility used by 50 files is higher risk than a change to an isolated component.
- **Change type**: Renaming a variable is lower risk than modifying control flow. Adding tests is lower risk than changing business logic.
- **File sensitivity**: Files in `auth/`, `payments/`, `infrastructure/` are higher risk by default. You customize these paths in `.soliton.yaml`.
- **Test coverage**: Changes to well-tested code are lower risk. Changes to untested code are higher risk.
- **Author signal**: PRs from AI agents with hallucination-prone patterns score higher risk. PRs with comprehensive descriptions and linked tickets score lower.
- **Historical defect rate**: Files that have had more bugs reverted in the past 6 months score higher risk.

You configure the thresholds: "auto-approve below 30, require human above 70, Soliton decides in between."

**Q: How does Assist Mode help humans review faster?**

A: Assist Mode is designed to reduce the 60% of review time humans spend on comprehension:

- **Change summary**: A concise natural-language description of what changed and why, generated from the diff and linked tickets.
- **Review guidance**: "Focus on these 3 files (auth changes). Skip these 12 files (auto-generated types, test fixtures)." Prioritizes human attention on what matters.
- **Risk annotations**: Each file in the diff is annotated with a risk level (low/medium/high) and the reason (e.g., "high: modifies payment processing logic with no test changes").
- **Inline findings**: Specific issues with severity, explanation, and suggested fix -- only findings above 80/100 confidence.
- **Cross-file impact map**: Shows what other parts of the codebase are affected by this change, so reviewers can assess blast radius without manually tracing dependencies.

For large PRs (500+ lines), Assist Mode reduces average review time by 40-60% by directing human attention to the 10-20% of changes that actually need judgment.

**Q: Is Autonomous Mode safe? What if Soliton approves a bad PR?**

A: Autonomous Mode includes multiple safety mechanisms:

1. **Graduated trust**: Teams must start in Assist Mode. You can only enable Autonomous Mode after Soliton has reviewed at least 100 PRs in your repository and you've verified its precision.
2. **Confidence threshold**: In Autonomous Mode, Soliton only auto-approves when ALL agents report high confidence. If any agent is uncertain, the PR is held for human review.
3. **Override**: Humans can override any Soliton decision at any time. Soliton never force-merges.
4. **Audit log**: Every decision is logged with full reasoning -- which agents ran, what they found, why the PR was approved/blocked. Fully auditable.
5. **Rollback protection**: Soliton integrates with deployment pipelines. If a merged PR causes test failures, build breaks, or monitoring alerts post-deploy, Soliton automatically flags the PR and adjusts its risk model.
6. **Escape hatches**: Any PR author or team member can add a `soliton:human-review` label to force human review, regardless of mode.

**Q: How much does it cost?**

A:
- **Open Source CLI**: Free forever. Self-host with your own LLM API keys. All three modes available.
- **GitHub App (Free Tier)**: Assist Mode only. 50 PR reviews/month for public repositories.
- **GitHub App (Team)**: $12/developer/month. All three modes. Dashboard, analytics, risk graph visualization.
- **GitHub App (Enterprise)**: Custom pricing. SSO, audit logs, on-premise deployment, custom agents, compliance reporting.

**Q: Which languages and frameworks does Soliton support?**

A: Soliton supports any language that AI coding agents write. Initial launch focuses on TypeScript/JavaScript, Python, Go, Java, and Rust with the deepest hallucination detection and dependency graph analysis. Other languages receive general multi-agent review and risk scoring.

**Q: How does Soliton know my project's conventions?**

A: Soliton reads your project's existing configuration:
- `.soliton.yaml` or `CLAUDE.md` for project-specific review rules and mode settings
- `ESLint`, `tsconfig.json`, `.prettierrc` for style conventions
- `package.json` / `requirements.txt` / `go.mod` for dependency version verification
- Git history and past PR comments for codebase-specific patterns
- Codebase dependency graph (built automatically on first install, updated incrementally)

You can also define custom review rules:
```yaml
rules:
  - "All API endpoints must use the authMiddleware from src/middleware/auth.ts"
  - "Database queries must use the query builder, never raw SQL"
  - "React components must use our custom useApiCall hook, not fetch directly"
```

**Q: What if Soliton flags something incorrectly?**

A: Dismiss the finding with a comment explaining why. Soliton learns from dismissals -- if a pattern is consistently dismissed, it reduces the confidence score for similar findings in future reviews. In Autopilot and Autonomous modes, this learning directly improves auto-approval accuracy over time. Our target is <5% false positive rate on findings that reach the 80/100 confidence threshold.

---

### Internal FAQ (Stakeholder Questions)

**Q: Why is this a big idea?**

A: Three converging trends create a once-in-a-decade opportunity:

1. **AI code volume is exploding.** GitHub reports 46% of code is now AI-generated (Oct 2024). By end of 2025, early-adopter teams will have >60% AI-authored code.
2. **Human review doesn't scale.** As AI generates more PRs, human review capacity stays flat. Teams either slow down (defeating the purpose of AI coding) or rubber-stamp (introducing bugs). This is the central bottleneck in AI-augmented software development.
3. **No existing tool offers a path from assisted to autonomous.** CodeRabbit, Copilot Review, and SonarCloud all stop at "assist humans." The team that builds the trusted autonomous reviewer owns the critical chokepoint in every AI-powered development pipeline.

The autonomy spectrum is the key insight. Teams won't jump from manual to autonomous overnight -- they need a trust ramp. The product that provides that ramp wins the market.

**Q: Why is the three-mode model the right approach?**

A: We learned this from Claude Code's permission model. Claude Code offers:
- Full manual (ask permission for every action)
- Selective auto-accept (approve known-safe operations, ask for risky ones)
- `--dangerously-skip-permissions` (full autonomy)

This works because it mirrors how trust actually builds: start conservative, observe behavior, gradually increase autonomy. Teams that start in Assist Mode will naturally want Autopilot once they see Soliton's precision. Teams that use Autopilot for months will trust Autonomous for low-risk repos. The product grows its own revenue as teams upgrade modes.

The competitive advantage: competitors would need to build risk scoring, codebase graphs, and auto-approval infrastructure to match. That's 6-12 months of catch-up time.

**Q: How does the codebase dependency graph work?**

A: On first install, Soliton performs a one-time analysis of the repository:
1. **Static analysis**: Parse imports, function calls, class hierarchies, and module boundaries to build a dependency graph.
2. **Git history enrichment**: Overlay historical defect data (which files have had reverts, bug-fix commits) and change coupling (files that change together frequently).
3. **Sensitivity classification**: Auto-classify files by domain (auth, payments, infrastructure, tests, generated) using path patterns and content analysis.
4. **Incremental updates**: After initial build, the graph updates incrementally with each PR review.

The graph powers both risk scoring (Autopilot) and cross-file impact analysis (all modes). It's stored per-repository and shared across team members.

Technical approach: Language-specific parsers for initial graph (tree-sitter for multi-language support), git log analysis for historical enrichment, LLM-based classification for sensitivity tagging. Estimated build time: 2-5 minutes for a 100K LOC repo.

**Q: What's the business model?**

A: Freemium SaaS with open-source core.

- Open-source CLI drives adoption and trust (all three modes, self-hosted)
- GitHub App is the monetization path (convenience, managed infrastructure, dashboard)
- The mode progression is a natural upsell: Free → Team (Autopilot) → Enterprise (Autonomous + compliance)
- Estimated ACV: $12/dev/month x 30 devs avg = ~$4,300/team/year for Team tier

Revenue projections:
- Year 1: Focus on adoption. 1,000 teams on free tier (Assist), 100 on Team (Autopilot). ~$430K ARR.
- Year 2: Enterprise expansion. 500 paid teams. Autonomous Mode drives Enterprise upgrades. ~$2.15M ARR.
- Year 3: Platform. Custom agent marketplace. Compliance certifications. ~$8M ARR.

**Q: What resources do we need?**

A:
- **Phase 1 (MVP, 3 months):** 2-3 engineers. Core multi-agent pipeline, Assist Mode, GitHub App, basic CLI.
- **Phase 2 (Launch, months 4-6):** 4-5 engineers + 1 designer. Autopilot Mode, codebase graph, risk scoring, dashboard, analytics.
- **Phase 3 (Scale, months 7-12):** 6-8 engineers. Autonomous Mode, learning loop, custom agent SDK, Enterprise features.

LLM API costs: ~$0.10-0.50 per PR review (multi-agent, ~15K-50K tokens per review). At scale with caching and smart model routing (Haiku for triage, Sonnet for review, Opus for complex decisions), unit economics are viable at $12/dev/month.

**Q: What are the key technical challenges?**

A:
1. **Codebase dependency graph accuracy.** Risk scoring is only as good as the graph. Multi-language repos, dynamic imports, and monorepos add complexity. Mitigation: start with TypeScript/Python where static analysis is strongest; use LLM-assisted analysis for ambiguous cases.
2. **False positive rate in Autonomous Mode.** When Soliton makes merge decisions, false positives aren't just annoying -- they block legitimate work. Mitigation: require 100+ reviewed PRs before enabling Autonomous; use higher confidence threshold (90) in Autonomous vs. Assist (80).
3. **Hallucination detection.** Detecting LLM-specific error patterns requires training data from real AI-generated PRs. Mitigation: bootstrap from open-source repos where AI contribution is visible via commit metadata.
4. **Latency.** Multi-agent review takes time. Target: <3 minutes for Assist, <1 minute for Autopilot risk-only triage. Requires parallel execution, caching, and tiered model selection.
5. **Trust calibration.** The mode upgrade path depends on teams trusting Soliton's precision. One high-profile false approval in Autonomous Mode could damage trust across the user base. Mitigation: conservative thresholds; extensive logging; opt-in by repository, not organization-wide.

**Q: Who are the competitors and how do we differentiate?**

A:
| Competitor | Their Approach | Soliton's Differentiation |
|------------|---------------|--------------------------|
| **CodeRabbit** | Assist-only AI review | Three-mode autonomy spectrum; risk scoring; auto-approval |
| **GitHub Copilot Review** | Platform-native, assist-only | Autonomy modes; open-source option; deeper analysis |
| **Qodo/PR-Agent** | Open-source, assist-only | Codebase graph; risk-based routing; Autonomous Mode |
| **SonarCloud** | Rule-based static analysis | AI catches what rules can't; autonomy modes beyond quality gates |
| **Snyk** | Security-focused | Broader scope; full autonomy spectrum |
| **LinearB/gitStream** | Workflow automation | gitStream auto-labels/routes but doesn't review code quality |

Our moat: The codebase dependency graph + learning loop + three-mode trust ramp creates compounding value. Each review makes the graph smarter, risk scoring more accurate, and the mode upgrade more compelling. Competitors would need to build the full stack (graph + risk + autonomy + learning) to match.

**Q: How will we measure success?**

A: Primary metrics by mode:

**Assist Mode:**
- Actionability rate (% findings acted on). Target: >80%.
- Review time reduction (hours saved per PR). Target: >40%.
- False positive rate. Target: <5%.

**Autopilot Mode:**
- Auto-approval accuracy (% of auto-approved PRs with zero post-merge issues). Target: >99%.
- Human review reduction (% of PRs no longer requiring human review). Target: >50%.
- Risk score calibration (correlation between predicted risk and actual defect rate).

**Autonomous Mode:**
- Merge decision accuracy (approval + rejection accuracy). Target: >98%.
- Revert rate. Target: <1% for Soliton-approved PRs.
- Developer override rate (% of Soliton decisions overridden by humans). Target: <5%.

**Business:**
- Mode upgrade rate (% of Assist users upgrading to Autopilot within 3 months). Target: >30%.
- NPS. Target: >50.
- Net revenue retention. Target: >120%.

**Q: What are the risks?**

A:
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Soliton auto-approves a PR that causes a production incident | Medium | Very High | Conservative thresholds; 100-PR trust-building requirement; rollback integration; audit logs |
| GitHub builds native Autopilot/Autonomous review | Medium | High | Move fast; open-source builds community moat; codebase graph is hard to replicate |
| Teams are culturally uncomfortable with autonomous review | High (near-term) | Medium | The three-mode ramp addresses this directly; start conservative; let trust build organically |
| LLM costs make unit economics unviable at scale | Low | High | Smart model routing (Haiku/Sonnet/Opus tiering); aggressive caching; cost optimization from day 1 |
| Codebase graph is too slow or inaccurate for large repos | Medium | Medium | Incremental updates; language-specific optimizers; degrade gracefully (skip graph, use agent-only review) |
| Competitors copy the three-mode model | High (12-18 months) | Medium | Data moat from learning loop; first-mover trust advantage; deep graph + agent integration |

**Q: What if we decide not to build this?**

A: The window for owning "autonomous PR review" as a category is 12-18 months. The three-mode model is not obvious today -- competitors are focused on making assist-mode better. But once teams start running AI agents at scale (2025-2026), the demand for Autopilot and Autonomous modes will become acute. The first product that earns trust in Assist Mode and provides a smooth upgrade path to Autonomous will own the category. Switching costs increase as teams configure risk thresholds, build custom rules, and the learning loop accumulates repository-specific knowledge.

---

## Appendix A: The Soliton Name

**Soliton** -- a self-reinforcing wave that maintains its shape while propagating at constant velocity. In physics, solitons are remarkable because they emerge from nonlinear systems and persist without dissipation.

The name reflects the product's core property: a review signal that cuts through noise, maintains integrity across the codebase, and reinforces itself as it learns from each review.

## Appendix B: The Autonomy Spectrum (Visual)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SOLITON AUTONOMY SPECTRUM                       │
│                                                                     │
│  ASSIST              AUTOPILOT              AUTONOMOUS              │
│  ─────              ─────────              ──────────               │
│                                                                     │
│  Human reviews       Human reviews          Soliton reviews         │
│  ALL PRs             HIGH-RISK PRs          ALL PRs                 │
│                                                                     │
│  Soliton provides:   Soliton provides:      Soliton provides:       │
│  • Change summary    • Risk scoring         • Merge decisions       │
│  • Review guidance   • Auto-approve low     • Change requests       │
│  • Inline findings   • Route high to human  • Blocking             │
│  • Risk annotations  • All Assist features  • All Autopilot feats  │
│                                                                     │
│  Human decides:      Human decides:          Human decides:          │
│  Everything          High-risk PRs only      Overrides only         │
│                                                                     │
│  ◄──────────── Trust builds over time ──────────────►               │
│  Start here          Upgrade when ready      Earn this              │
└─────────────────────────────────────────────────────────────────────┘
```

## Appendix C: Analogy to Claude Code Permissions

| Claude Code | Soliton | What It Means |
|------------|---------|---------------|
| Default (ask for everything) | **Assist Mode** | Human approves every action/PR. AI provides information. |
| Selective auto-accept | **Autopilot Mode** | Known-safe actions/PRs proceed automatically. Risky ones require human approval. |
| `--dangerously-skip-permissions` | **Autonomous Mode** | AI acts independently. Human can still override but isn't in the loop by default. |

The key insight: developers already understand this mental model from Claude Code. Soliton applies the same trust ramp to PR review.
