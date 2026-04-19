# Stacked-PR Mode

Soliton v2 supports reviewing a PR as part of a **stack** — a chain of dependent PRs where
each builds on the previous one (Graphite, gherrit, git-gud, Gerrit workflows). In this mode,
Soliton reviews only the delta between the current PR and its parent PR's head, rather than
re-reviewing everything from `main`.

**Enabled via** the `--parent <PR#>` CLI flag on `/pr-review`, or by auto-detection when
`config.stack.auto_detect == true`.

**Rationale**: reviewing a 3rd-in-stack PR against `main` produces findings already made on
the 1st and 2nd PRs. A reviewer (human or AI) would be overwhelmed. Stack-aware mode puts the
review in scope.

No other OSS AI reviewer does this in April 2026 (see `idea-stage/OSS_ECOSYSTEM_REVIEW.md`
§7a); Anthropic's managed service also does not.

## Use cases

1. **Graphite `gt` stacks** — `gt submit --stack` creates PR N+1 with base branch = PR N's
   head branch (not `main`). Soliton's default behavior reviews PR N+1 vs its base branch,
   which is already correct — but `--parent` lets the reviewer explicitly claim the ancestor,
   which is useful when:
   - The stack has since been rebased and the base-branch metadata is stale.
   - The reviewer wants to compare against a specific ancestor multiple rungs up.
2. **Gerrit-style `gherrit` / `git-gud`** — each commit becomes a dependent PR. Same scoping
   concern.
3. **Feature-by-feature enterprise rebuild** — the `Logical_inference/idea-stage/USE_CASE_PLANS.md`
   §1.7 pilot plan for graph-driven rebuild generates one PR per feature partition; reviewing
   feature PR N should scope against feature PR N-1's head, not `main`, to isolate that
   feature's delta.

## CLI usage

```bash
# Explicit parent PR number
/pr-review 123 --parent 122

# Auto-detect parent (only works for Graphite stacks with gt installed)
/pr-review 123 --stack-auto

# Review local branch against a specific ancestor SHA
/pr-review --parent-sha abc123def
```

## Detection of stack membership

When `--parent <N>` is passed:

1. Fetch parent PR metadata: `gh pr view <parent> --json headRefOid,title,baseRefName,mergeable,state`.
2. Validate the parent is not merged (if it is, this PR's diff vs `main` is already the right
   thing).
3. Validate the current PR's base branch is not `main` — it should match the parent's head
   branch (`headRefOid`), otherwise the stack assumption is wrong.

If validation fails, Soliton outputs an error and does NOT fall back silently:

```
Error: --parent 122 provided, but PR #123's base branch is 'main', not PR #122's head.
Did you mean to omit --parent?
```

## Diff reconstruction

When stack mode is active, replace `SKILL.md` Step 1 Mode B's `gh pr diff ${prNumber}` with:

```bash
PARENT_HEAD=$(gh pr view ${parentNumber} --json headRefOid -q .headRefOid)
git fetch origin pull/${prNumber}/head:pr-${prNumber}
git fetch origin pull/${parentNumber}/head:pr-${parentNumber}
git diff ${PARENT_HEAD}...pr-${prNumber}
```

Store the result as `diff`. The rest of the pipeline is unchanged — risk-scorer sees the
delta only, agents see the delta only, etc.

### baseBranch / headBranch bookkeeping

- `baseBranch` = parent PR's head branch name (for display).
- `headBranch` = current PR's head branch name (unchanged).
- Add a new field `stackParent: { pr: N, headSha: <SHA> }` to `ReviewRequest` so downstream
  can surface the provenance in the output comment.

### PR-description augmentation

Prepend to `prDescription`:

```
[Stacked PR — reviewed vs parent PR #<N>: <parent title>]

<original description>
```

This tells review agents the context so they don't flag "missing function foo" when `foo` was
added in the parent PR (not this one).

## Output changes

Synthesis header includes stack context:

```
## Summary
<files> changed, <lines>+/<lines>- against PR #<parent> ("Parent PR title").
<findings count>. <one-liner>.
```

Output metadata JSON adds:

```json
{
  "metadata": {
    "stackParent": {
      "pr": 122,
      "headSha": "abc123...",
      "title": "parent PR title"
    }
  }
}
```

## Configuration

`.claude/soliton.local.md`:

```yaml
stack:
  auto_detect: false              # on by default once gt-like CLI integration stabilises
  # When auto-detect is on, soliton checks:
  #   - `gt log` output (Graphite)
  #   - PR body template markers like "Part N of M:"
  #   - base branch pattern (matches "stack/*" glob)
  require_parent_merged_check: true   # error if parent not yet merged vs its own base
```

CLI flags override config.

## Edge cases

### The parent was force-pushed

If the parent PR's `headRefOid` has changed since we fetched, the stacked diff may be stale.
Soliton re-fetches on each invocation — no caching. If the change is too big (> 100 lines
between old and new parent), abort with:

```
Error: Parent PR #122 was force-pushed since you last ran this review.
Re-run to use the fresh parent, or pass --parent-sha <OLD_SHA> to review against the old one.
```

### The parent is closed / merged

If merged: reviewing vs `main` is probably what's wanted. Emit a warning and fall back to
default behavior. Do NOT silently — the reviewer should know the stack was dissolved.

If closed-without-merge: error — this is almost never the right call.

### Stack depth > 3

Soliton does NOT recursively review against a 5-deep stack — that's the reviewer's ergonomic
problem, not Soliton's. `--parent` takes exactly one parent; compose stacks manually if needed.

### Cross-fork stacks

Not supported in v2. Requires multi-fork metadata handling; deferred to v3.

## Graphite-specific integration

If `gt` binary is on PATH AND `config.stack.auto_detect == true`:

```bash
if command -v gt >/dev/null; then
    PARENT_BRANCH=$(gt log short --no-color | head -2 | tail -1 | awk '{print $NF}')
    if [ -n "$PARENT_BRANCH" ] && [ "$PARENT_BRANCH" != "main" ] && [ "$PARENT_BRANCH" != "master" ]; then
        PARENT_PR=$(gh pr list --head "$PARENT_BRANCH" --json number -q '.[0].number')
        if [ -n "$PARENT_PR" ]; then
            echo "Auto-detected Graphite stack; parent PR: #$PARENT_PR"
            # use $PARENT_PR as if --parent $PARENT_PR was passed
        fi
    fi
fi
```

## Relationship to soliton.local.md scope

Stack mode is orthogonal to `tier0`, `graph`, `spec_alignment`, `realist_check`. All the v2
features continue to work — they just operate on the narrower stacked-diff instead of the
full base-branch diff. The graph-signals skill will query with `head` = current PR head and
`base` = parent PR head instead of `main`.

## Future work (not v2)

- **Whole-stack review mode**: review PR N with awareness of PRs N-1, N-2, … as context. The
  current design only scopes against the direct parent.
- **Stack graph visualisation** — render a minimap of the stack in the review comment.
- **Cross-fork stacks** — handle Gerrit-Cloud-style review chains across repo boundaries.
