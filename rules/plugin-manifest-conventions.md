# Plugin Manifest Conventions

Rules for reviewing Claude Code plugin manifest files (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.cursor-plugin/plugin.json`).

## Path Resolution

All paths in `.claude-plugin/plugin.json` are resolved **relative to the plugin root directory**, NOT relative to the `.claude-plugin/` subdirectory where the manifest lives.

Example: if the plugin root is `my-plugin/`, then:
- `"./agents/reviewer.md"` resolves to `my-plugin/agents/reviewer.md`
- `"./skills/"` resolves to `my-plugin/skills/`
- `"./custom/hooks.json"` resolves to `my-plugin/custom/hooks.json`

This is a Claude Code convention documented at https://code.claude.com/docs/en/plugins-reference:
> "All paths must be relative to plugin root and start with ./"

**Do NOT flag `./` paths in plugin manifests as incorrect because the manifest is inside `.claude-plugin/`.** This is the most common false positive when reviewing plugin PRs.

## Auto-Discovery

Claude Code auto-discovers components in default directories (`agents/`, `skills/`, `commands/`, `hooks/`). Paths specified in the manifest **supplement** defaults — they don't replace them. Listing `"agents": ["./agents/risk-scorer.md"]` when `agents/` already exists at the root is redundant but not incorrect.

## Schema Flexibility

The `agents`, `skills`, `commands`, `hooks`, `mcpServers`, `outputStyles`, and `lspServers` fields accept both `string` and `array` types:
- `"agents": "agents/"` (string — directory path)
- `"agents": ["./agents/reviewer.md", "./agents/tester.md"]` (array — individual files)

Different platform manifests (`.claude-plugin/` vs `.cursor-plugin/`) may use different formats. This is expected and not an inconsistency worth flagging.

## ${CLAUDE_PLUGIN_ROOT}

In hooks, MCP servers, and scripts, use `${CLAUDE_PLUGIN_ROOT}` for absolute path references. This variable resolves to the plugin root directory at runtime.

## Versioning

Claude Code uses the `version` field to decide whether to update a cached plugin. **If you change plugin code but don't bump the version, existing users won't see your changes.**

### Rules

1. **Bump the version on every PR that changes plugin behavior** (agents, skills, rules, hooks, manifests). Documentation-only changes (README, LICENSE, CHANGELOG) do not require a bump.
2. **Use semver** (`MAJOR.MINOR.PATCH`):
   - **PATCH** — bug fixes, rule tweaks, agent prompt improvements
   - **MINOR** — new agents, new skills, new flags, new rules
   - **MAJOR** — breaking changes to output format, removed agents, changed default behavior
3. **Keep all version fields in sync.** The version appears in:
   - `.claude-plugin/plugin.json` → `version`
   - `.claude-plugin/marketplace.json` → `metadata.version` AND `plugins[0].version`
   - `.cursor-plugin/plugin.json` → `version`

   All four values must match. If a PR changes any of them, flag a finding if the others are not updated to the same value.
4. **`plugin.json` takes priority** over `marketplace.json` when both specify a version. But keeping them in sync avoids confusion.

### Review guidance

When reviewing a PR that modifies files under `agents/`, `skills/`, `rules/`, or `hooks/`:
- Check whether any version field was bumped. If not, flag as an **improvement** finding: "Plugin behavior changed but version was not bumped — cached installs won't pick up the update."
- Check that all 4 version locations are consistent. If they differ, flag as an **improvement** finding.

## Plugin Caching

Installed marketplace plugins are cached to `~/.claude/plugins/cache/`. Plugins **cannot reference files outside their directory** — paths like `../shared-utils` will fail after installation because external files are not copied to the cache.

Because of caching, version bumps are the only reliable way for users to receive updates. If the version doesn't change, `claude plugin update` is a no-op.
