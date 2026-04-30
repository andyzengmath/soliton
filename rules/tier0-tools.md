# Tier-0 Tool Catalog

Canonical tool choices, invocations, and exit-code conventions for the deterministic gate. This
file is the single source of truth for what Soliton runs pre-LLM.

## Design principles

1. **Every tool must be OSS or have a permissive license.** No cloud services. No auth tokens.
2. **Every tool must emit structured output** — SARIF preferred, JSON or TSV fallback.
3. **No tool is hard-required.** If absent on PATH, skip with a warning.
4. **Hold-the-line**: findings are filtered to changed lines only (except secret-scan).
5. **Budgets**: each tool ≤ 60 s. Total Tier 0 ≤ 60 s wall-clock (parallel).

## Lint + format

| Tool | Lang | Canonical invocation | Exit-code contract |
|---|---|---|---|
| `ruff` | python | `ruff check --output-format sarif --output-file {out} {files}` | 0 = clean; 1 = findings emitted to SARIF |
| `eslint` | ts/js | `eslint --format @microsoft/sarif --output-file {out} {files}` | 0 = clean; 1 = findings; 2 = config error |
| `biome` | ts/js (alt) | `biome ci --reporter=sarif {files} > {out}` | 0 = clean; 1 = findings |
| `golangci-lint` | go | `golangci-lint run --out-format=sarif {files} > {out}` | 0 = clean; 1 = findings |
| `clippy` | rust | `cargo clippy --message-format=json > {out}` | bespoke — parse JSON |
| `checkstyle` | java | `checkstyle -f sarif -o {out} {files}` | 0 = clean |
| `checkstyle` (Maven) | java (alt) | `mvn checkstyle:check -Dcheckstyle.failOnViolation=false -Dcheckstyle.outputFile={out} -Dcheckstyle.outputFileFormat=sarif` | 0 = clean (failOnViolation=false collects, doesn't block) |

**No-format policy**: Tier 0 does NOT auto-format. That is `pre-commit`'s job. Tier 0 only *reports*
formatting drift — never rewrites the PR.

## Type check

| Tool | Lang | Invocation | Fatal = block? |
|---|---|---|---|
| `tsc` | ts | `tsc --noEmit --pretty false` | **Yes** — any `error TS` blocks |
| `mypy` | python | `mypy --no-color-output --show-error-codes {files}` | **Yes** if `--strict`-configured error |
| `pyright` | python (alt) | `pyright --outputjson {files} > {out}` | **Yes** on `"severity": "error"` |
| `go build` | go | `go vet ./... && go build ./...` | **Yes** — non-zero exit |

Fatal type errors set `verdict = blocked` because they are not false positives — the code literally
does not compile, and there's no point spending LLM tokens to report that.

## SAST

| Tool | Lang | Invocation | Severity source |
|---|---|---|---|
| `semgrep` | multi | `semgrep ci --sarif --output {out}` | SARIF `level` field |
| `bandit` | python (alt) | `bandit -f sarif -o {out} -r {dirs}` | SARIF |
| `gosec` | go (alt) | `gosec -fmt sarif -out {out} ./...` | SARIF |
| `brakeman` | ruby | `brakeman -f sarif -o {out}` | SARIF |
| `spotbugs` (Maven) | java | `mvn com.github.spotbugs:spotbugs-maven-plugin:check -Dspotbugs.failOnError=false -Dspotbugs.sarifOutput=true -Dspotbugs.sarifOutputDir={out_dir}` | SARIF (Maven plugin v4.7+) |
| `spotbugs` (CLI) | java (alt) | `spotbugs -textui -sarif -output {out} {classes_dir}` | needs compiled `.class` files; SARIF output — install via standalone JAR from spotbugs.github.io/releases |

**Default rulesets**:
- `semgrep --config p/owasp-top-ten --config p/security-audit --config p/secrets`
- Add `--config auto` to enable repo-specific detection via Semgrep Registry.

**Blocking rule**: any Semgrep finding with `severity == "error"` AND `category == "security"` →
`verdict = blocked`.

## Secret scan

| Tool | Invocation | Notes |
|---|---|---|
| `gitleaks` | `gitleaks detect --source . --log-opts="{base}..HEAD" --report-format sarif --report-path {out}` | Scans new commits only |
| `trufflehog` (alt) | `trufflehog git file://. --since-commit={base} --json > {out}` | Verifies with live-key check when possible |

Any match → `verdict = blocked`. Secret-scan findings surface even on unchanged lines if the secret
*appears* in the PR diff's new content.

## SCA (dependencies)

| Tool | Manifest | Invocation |
|---|---|---|
| `osv-scanner` | all | `osv-scanner --format sarif --output {out} --lockfile={path}` |
| `pip-audit` | `requirements.txt` | `pip-audit -r {path} --format sarif -o {out}` |
| `npm audit` | `package-lock.json` | `npm audit --json > {out}` — bespoke |
| `cargo-audit` | `Cargo.lock` | `cargo audit --json > {out}` |

Severity mapping: CVE CVSS ≥ 9.0 → `critical` → `verdict = blocked`. CVSS 7.0-8.9 → `high`. Below → `medium`.

## AST structural diff

| Tool | Invocation | Output |
|---|---|---|
| `difftastic` | `difft --display json --list {base_file} {head_file}` | Per-hunk structural change class |

Structural change classes surfaced as non-blocking annotations:
- `function_signature_changed` → hint to `cross-file-impact` agent
- `control_flow_added` → hint to `correctness` agent
- `import_added` → hint to `hallucination` agent
- `error_handling_changed` → hint to `silent-failure` agent (I7)
- `type_changed` → hint to `cross-file-impact` + `correctness`

These are hints, not findings. They're attached to the risk-scorer's focus areas.

## Clone detection

| Tool | Invocation |
|---|---|
| `jscpd` | `jscpd --min-tokens 50 --reporters json --output .soliton/tier0/clones.json {dirs}` |
| `pmd-cpd` (alt) | `pmd cpd --minimum-tokens 50 --files {files} --format json > {out}` |

Only report clones created *by* this PR — filter out pre-existing clones the PR touches.

## Test-impact selection (advisory only)

| Tool | Lang | Purpose |
|---|---|---|
| `pytest-testmon` | python | List tests affected by the diff |
| `jest --findRelatedTests` | ts/js | Same |
| `bazel query` | bazel monorepo | Same |

Output is appended to the `testCoverage` signal, not a blocking finding.

## Configuration — `.claude/soliton.local.md`

```yaml
tier0:
  enabled: true
  skip_llm_on_clean: true          # Phase-2 default
  block_on: ["secret_leak", "cve_critical", "type_error_fatal", "security_critical"]

  tools:
    lint:
      python: ["ruff"]
      typescript: ["eslint"]        # or ["biome"]
      go: ["golangci-lint"]
      rust: ["clippy"]
      java: ["checkstyle"]          # standalone CLI; or ["checkstyle-maven"] if mvn on PATH
    type_check:
      python: ["mypy"]              # or ["pyright"]
      typescript: ["tsc"]
      go: ["go-vet"]
      java: ["javac-mvn"]           # mvn compile (fatal on compilation error)
    sast: ["semgrep", "spotbugs"]   # spotbugs is Java-specific; gracefully skipped on non-Java diffs
    secrets: ["gitleaks"]
    sca:
      npm: ["osv-scanner"]
      pip: ["osv-scanner"]
      cargo: ["cargo-audit"]
      maven: ["osv-scanner"]        # operates on pom.xml dependency tree

  disabled_tools: []                # e.g. ["jscpd"] to disable clone detection
  skip_languages: []                # e.g. ["sql"] to skip SQL-tier checks
  max_duration_ms: 60000
```

## Installation cheatsheet

Tier-0 tools follow Soliton's catalog principle 3 (graceful skip when absent). For integrators who want full coverage on a given language, here's the canonical install path per OS / package manager:

### Cross-language (always install)

```bash
# gitleaks — OSS secret scanner
winget install gitleaks.gitleaks               # Windows
brew install gitleaks                          # macOS
go install github.com/gitleaks/gitleaks/v8@latest   # Linux / fallback

# osv-scanner — CVE/SCA across all manifests
winget install Google.OSVScanner               # Windows
brew install osv-scanner                       # macOS
go install github.com/google/osv-scanner/cmd/osv-scanner@latest   # Linux / fallback

# semgrep — multi-lang SAST (Python, Java, JS, Go, Ruby, etc.)
pip install semgrep                            # any platform with Python
```

### Python

```bash
pip install ruff mypy bandit
```

### TypeScript / JavaScript

```bash
npm i -g eslint typescript                     # or @biomejs/biome (alt)
```

### Go

```bash
# golangci-lint
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
# gosec (alt)
go install github.com/securego/gosec/v2/cmd/gosec@latest
```

### Java

```bash
# Option A: Maven plugins (preferred when integrator already has mvn)
# No separate install — invoked via Maven plugins:
#   mvn checkstyle:check   (lint)
#   mvn com.github.spotbugs:spotbugs-maven-plugin:check   (SAST)
#   mvn compile            (type check / fatal compilation errors)
# Add the plugins to your pom.xml's <build><plugins> section. Reference:
#   https://maven.apache.org/plugins/maven-checkstyle-plugin/
#   https://spotbugs.readthedocs.io/en/latest/maven.html

# Option B: Standalone CLIs (when Maven not on PATH)
# checkstyle — Java lint
choco install checkstyle                       # Windows (admin required)
brew install checkstyle                        # macOS
# Else download standalone JAR + alias:
#   https://github.com/checkstyle/checkstyle/releases
# spotbugs — Java SAST
# Currently no choco/brew/winget package; install via standalone:
#   https://github.com/spotbugs/spotbugs/releases (download spotbugs-X.Y.Z.zip,
#   add bin/ to PATH).
```

### Ruby

```bash
gem install brakeman rubocop
```

### Rust

```bash
rustup component add clippy
cargo install cargo-audit
```

### AST diff (multi-lang, advisory only)

```bash
cargo install difftastic                       # Linux/macOS/Windows (any with cargo)
choco install difftastic                       # Windows (alt, admin)
brew install difftastic                        # macOS (alt)
```

### Verification — Tier-0 self-test

After install, verify each tool fires from a small repo:

```bash
gitleaks detect --source . --no-git --report-format json --report-path /tmp/gl.json
osv-scanner --format json --recursive .
semgrep --config p/owasp-top-ten --sarif --output /tmp/sg.sarif .
# (Java-specific, in a Maven project)
mvn checkstyle:check
mvn com.github.spotbugs:spotbugs-maven-plugin:check
```

Tools absent from PATH are silently skipped; `Tier-0 verdict` falls back to `needs_llm` (vs `clean`) when the deterministic floor isn't fully covered for the diff's language. Soliton's behavior degrades gracefully — integrators with only the cross-language tools (gitleaks + osv-scanner + semgrep) still get supply-chain integrity + secret + CVE coverage on every PR.

## Exit-code conventions for CI gating

When Soliton is invoked via `anthropics/claude-code-action` in a gated workflow:

| `tierZeroVerdict` | CI action |
|---|---|
| `clean` + `skip_llm_on_clean=true` | Exit 0. No PR comment (or confirm-only). |
| `clean` + `skip_llm_on_clean=false` | Exit 0. LLM swarm runs normally. |
| `advisory_only` | Exit 0. LLM swarm runs with elevated threshold. |
| `needs_llm` | Exit 0. LLM swarm runs normally. |
| `blocked` | Exit 1. Post Tier-0 findings as PR comment. Fail the check. |

The gated-workflow example (`examples/workflows/soliton-review-gated.yml`) parses `tierZeroVerdict`
from the JSON output to decide exit code.

## Provenance

Every Tier-0 finding that makes it to PR comments must include a `tool: <name>` tag so developers
can rerun the tool locally with the same flags. The markdown output format adds a parenthetical:

```
🔴 [lint:ruff] Unused import `os` in foo.py:3 (confidence: 100, rule: F401)
```

The `confidence: 100` is deterministic — Tier-0 findings are always confidence 100 (the tool
itself had no uncertainty; the source of any error is the tool's rule definition, not LLM
guessing).
