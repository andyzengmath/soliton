# REVIEW.md Convention

`REVIEW.md` is an optional file at the repository root (or at a sub-path configured via
`.claude/soliton.local.md`'s `specSources`) that tells Soliton's spec-alignment agent what
this PR *should* do. It is read by the `spec-alignment` agent in Stage 0 — before the review
swarm runs.

The file is plain Markdown. Soliton parses specific sections; the rest is freeform.

## Compatibility

The same file is read by Anthropic's managed Code Review. Soliton adopts the same section
conventions so a repo using both tools doesn't need two spec files.

## Recognised sections

Any heading at level 2 (`##`) whose title matches one of these (case-insensitive) is parsed:

| Heading | Soliton treats as | Notes |
|---|---|---|
| `## Acceptance Criteria` | `criterion.kind = requirement` | Bullets / numbered list |
| `## Requirements` | `criterion.kind = requirement` | Alias |
| `## Must Have` | `criterion.kind = requirement` | Alias |
| `## Checklist` | `criterion.kind = checklist` | Markdown checkboxes (`- [ ]`, `- [x]`) |
| `## Wiring Verification` | `criterion.kind = wiring` | Mechanical grep assertions |
| `## Test Coverage` | `criterion.kind = test-coverage` | Files/functions that must gain tests |
| `## Out of Scope` | scope-creep allowlist | Files listed here *expected* to change but don't need criteria |
| `## Style & Conventions` | passed to `consistency` agent | Becomes project-specific rules |
| `## Never` | passed to `correctness` + `security` | Patterns that must not appear |

## Acceptance criteria syntax

```markdown
## Acceptance Criteria

- New users can sign up with email + password.
- Passwords are hashed with Argon2id.
- Sign-up emits a `user.created` event to the SNS topic `auth-events`.
- [ ] Rate-limit is enforced: ≤5 sign-ups / IP / hour.
```

Each bullet or checkbox becomes one `criterion`. The agent checks the diff for evidence of each.

## Wiring verification syntax

Strict, literal, grep-backed. Use code spans for the literal strings to match.

```markdown
## Wiring Verification

- `src/routes.ts` MUST contain `app.post('/signup', signupHandler)`
- `src/handlers/signup.ts` MUST export `signupHandler`
- `package.json` MUST list `"argon2": "^0.31"` under dependencies
- `schema/events.json` MUST contain `"user.created"`
```

**Grammar**: `` `<file>` MUST contain `<literal string>` ``. Escape backticks with `\``.

Everything else in the file is freeform Markdown — don't let it interfere.

## Test coverage syntax

```markdown
## Test Coverage

- `src/auth/signup.ts` must have unit tests in `src/auth/__tests__/signup.test.ts`
- Integration test for `/signup` endpoint must exist in `tests/integration/auth.test.ts`
- `hashPassword()` function must have tests that verify Argon2id parameters
```

For each bullet, the agent checks:
1. Does the mentioned test file exist AND was it modified/created in this PR?
2. Does the production file have corresponding test coverage in THIS PR?

Missing test file for a listed production file → `test-coverage` criterion `not_satisfied`,
`improvement` severity (not blocking — unless a project rule upgrades it).

## Out-of-scope syntax

```markdown
## Out of Scope

- `package-lock.json` — dep pin churn expected
- `CHANGELOG.md` — updated mechanically
- `**/*.generated.ts` — generated files, don't review
```

Files matching these globs do NOT trigger scope-creep findings. They're also excluded from the
consistency / correctness agents when the glob matches exactly (no conflict with pipeline
filtering in `rules/generated-file-patterns.md`).

## Style & conventions syntax

Freeform rule bullets. The `consistency` agent treats violations as `improvement`. Sections
tagged `MUST` upgrade to `critical` (matches the quantum-loop pattern: documented rules
violated → critical).

```markdown
## Style & Conventions

- All API handlers MUST use `asyncHandler` wrapper from `lib/async-handler.ts`.
- Database queries MUST use Drizzle ORM — no raw SQL strings.
- Error responses MUST use the shape `{ error: string, code: number, traceId: string }`.
- Log with `logger.info()` / `logger.error()`; no `console.log` outside tests.
```

## Never syntax

Patterns to actively block. Checked by both `correctness` and `security` agents as hard rules.

```markdown
## Never

- `eval(` — dynamic code execution.
- `innerHTML =` — XSS risk; use textContent or DOM APIs.
- `SELECT * FROM` raw SQL strings — parameterised queries only.
- `sudo ` in any shell script.
```

## Examples

### Minimal REVIEW.md

```markdown
## Acceptance Criteria

- Adds a `/healthz` endpoint that returns 200 OK with JSON `{"status":"ok"}`.

## Wiring Verification

- `src/routes.ts` MUST contain `'/healthz'`
```

### Rich enterprise REVIEW.md

```markdown
# Project Review Guidelines

## Acceptance Criteria

- Each new API handler is wrapped in `tracingMiddleware`.
- All database writes emit an audit log entry.

## Checklist

- [ ] New endpoints appear in `openapi.yaml`.
- [ ] New env vars are documented in `docs/env.md`.
- [ ] Migrations have a rollback script.

## Wiring Verification

- `openapi.yaml` MUST contain the new path.
- `docs/env.md` MUST list any new `NEXT_PUBLIC_*` or `DB_*` var.

## Test Coverage

- Handler files under `src/handlers/` must have matching `.test.ts` in the same directory.

## Style & Conventions

- All handlers MUST use `asyncHandler` wrapper.
- Error responses MUST include `traceId`.

## Never

- `eval(`
- `innerHTML =`
- Raw `SELECT * FROM` SQL strings.

## Out of Scope

- `**/*.generated.ts`
- `package-lock.json`
- `yarn.lock`
```

## Relationship to `.claude/soliton.local.md`

Both files can coexist:

- `.claude/soliton.local.md` = **Soliton tooling configuration** (tool choices, thresholds,
  agent selection, output format).
- `REVIEW.md` = **Project review intent** (what this codebase / this PR should do).

`soliton.local.md` frontmatter overrides Soliton defaults; `REVIEW.md` drives spec-alignment
findings. The two are orthogonal.

## Relationship to CLAUDE.md

`CLAUDE.md` documents codebase conventions for Claude Code at large (coding agent context).
Soliton's consistency agent also reads it. If the same rule is in both, `REVIEW.md` `Style
& Conventions` has higher severity (documented explicitly for review).

## Inherit from parent folders

Like CLAUDE.md, Soliton reads `REVIEW.md` from the repo root AND every parent-directory up to
the changed files. A `src/auth/REVIEW.md` governs PRs touching `src/auth/**`. Closest wins
if conflicting.

## When there is no REVIEW.md

Perfectly fine. The spec-alignment agent falls back to PR description + linked issues + any
`.claude/specs/` content. If none of those exist either, the agent emits
`SPEC_ALIGNMENT_NONE` and the orchestrator proceeds to the swarm without a spec-compliance
finding block.
