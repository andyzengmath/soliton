# Soliton Hook Integration Guide

Soliton ships **optional Claude Code hooks** that surface review-relevant signals during your coding session — outside the dispatch path of `/pr-review`, but using the same primitives.

The hooks are **degraded-mode** (grep + git heuristics) until the graph plugin lands per POST_V2_FOLLOWUPS §B1. When the graph plugin ships, the hooks can be upgraded to use real call/import edges; the user-facing wiring (this doc) stays the same.

## Available hooks

| Hook | File | Trigger | What it does | Status |
|------|------|---------|--------------|--------|
| **Hook C — blast-radius warning** | `hooks/blast-radius-warning.sh` | `PostToolUse` on `Edit` or `Write` | Emits advisory warning to stderr when an edited file is referenced by ≥ N other files (grep heuristic) | ✅ shipped |
| Hook A — graph context injection | (not shipped) | `PreToolUse` on `Read` | Prepends import/reference annotations to the file content | ⏸ deferred — graph-cli gated |
| Hook B — Grep query rewrite | (not shipped) | `PreToolUse` on `Grep` | Suggests narrower query when a broad search would return many irrelevant matches | ⏸ deferred — graph-cli gated |

The other two hooks (A, B) are documented in `Logical_inference/docs/strategy/2026-05-01-A2-agent-integration-architecture.md` § 6.1 but require graph-aware backing signals to be useful. They will land when the graph plugin can supply real dependency edges.

## Hook C — blast-radius warning

**Behavior**: after Claude Code edits a file, the script computes how many other files in the repo reference the edited file (via `git grep -l --fixed-strings <basename-without-extension>`). If the count meets or exceeds a configurable threshold, the script emits an advisory block to stderr noting the importer count, sensitive-paths hit (auth/, security/, payment/, *.env, etc.), and a suggested `/blast-radius` follow-up command for full context.

**Advisory only**: the script always exits 0. It never blocks tool execution. The intent is *information surface*, not *gate*.

**Cost**: zero per invocation in practice. `git grep` against a single literal string completes in milliseconds even on multi-million-line repos.

### Wiring (Claude Code settings)

Add a `PostToolUse` hook entry to `~/.claude/settings.json` (user-level) or `.claude/settings.json` (project-level):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "/abs/path/to/soliton/hooks/blast-radius-warning.sh"
          }
        ]
      }
    ]
  }
}
```

Replace `/abs/path/to/soliton/` with the absolute path to your Soliton repo clone. The hook script reads its input as JSON on stdin (Claude Code's standard hook contract) and emits its advisory to stderr — both visible in your Claude Code session.

### Configuration

The script reads two environment variables. Set them in your shell profile or in the hook command line:

| Variable | Default | Effect |
|----------|---------|--------|
| `SOLITON_BLAST_THRESHOLD` | `10` | Minimum importer count to trigger the warning. Lower = more advisories; higher = only the most-referenced files. |
| `SOLITON_BLAST_QUIET` | _(unset)_ | If set to any non-empty value, the script runs but emits nothing. Useful for temporarily silencing without removing the hook. |

Example with custom threshold:

```json
{
  "command": "SOLITON_BLAST_THRESHOLD=20 /abs/path/to/soliton/hooks/blast-radius-warning.sh"
}
```

### Verifying it works

After wiring the hook, edit any file in a Soliton-or-similar codebase via Claude Code. If the file's basename matches ≥ N other files (where N = `SOLITON_BLAST_THRESHOLD`), you'll see something like:

```
⚠️  Soliton blast-radius warning (Hook C)
   File: agents/correctness.md
   Importers: 23 files (grep heuristic; threshold=10)
   Sensitive: clean
   Suggestion: run /blast-radius agents/correctness.md for top-5 importers + full context
   Hook source: hooks/blast-radius-warning.sh (advisory; never blocks)
```

If you don't see the warning when expected, debug the hook by piping a sample payload manually:

```bash
echo '{"tool":"Edit","tool_input":{"file_path":"agents/correctness.md"}}' \
  | bash hooks/blast-radius-warning.sh
```

If this produces output, the hook is working — your `settings.json` wiring is the issue. If it doesn't, check:

- `git rev-parse --is-inside-work-tree` succeeds (script bails outside a repo)
- The basename without extension is ≥ 4 characters (script skips short symbols to avoid false positives like `index`, `main`, `a`)
- `python3` is on PATH (script uses python3 for JSON parsing; falls back to grep parse if missing)

## Strategic context

Hooks are A2 §6 of the Logical_inference architecture doc — described there as the "killer feature" that bridges Soliton's review pipeline with Claude Code's tool-use loop. The full vision (Hook A injecting graph context into every Read; Hook B rewriting Grep queries against the call graph) requires `graph-cli` from the sibling repo.

Hook C is the **degraded-mode shippable today** subset: same UX surface (PostToolUse advisory), backed by grep instead of graph. The advisory framing is intentional — see `bench/crb/IMPROVEMENTS.md` § "subtraction wins, addition fails" for why we don't enforce-on-fail. The Phase 5.3 evidence (PR #68: −0.045 F1 regression when wirings went default-ON) means new advisory surfaces ship default-OFF until measured.

If you opt in to Hook C and find the threshold mis-calibrated for your codebase, please file an issue with your import-count distribution so we can tune the default. Phase 6 (Java-only L5 cross-file retrieval) is the related CRB-measurable experiment that will inform whether grep-based blast-radius signals improve actual review quality.

## Non-goals

- Hooks are NOT part of `/pr-review`'s measured output. They run during your interactive coding session, not during review dispatch.
- Hook C does NOT replace the risk-scorer agent's blast-radius computation. The agent uses the same heuristic but in the context of a full multi-factor risk score; the hook surfaces a single signal in isolation for ad-hoc developer feedback.
- Hooks do NOT block. Even when a sensitive path or extreme blast radius is detected, the hook emits to stderr and exits 0. The user retains full control over whether to proceed.
