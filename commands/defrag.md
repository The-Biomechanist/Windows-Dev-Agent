---
description: Inventory an existing agent/developer-tool setup, identify overlap, and plan a reversible consolidation. Use on first install or when the setup has become fragmented.
---

You are running the Windows Dev Agent ecosystem defrag.

## 1. Establish the inventory

Call the `ecosystem_scan` MCP tool for the current project. Use `include_packages: true` only when the installed WinGet package inventory is relevant; it is slower and usually unnecessary for agent-config cleanup.

The scan owns read-only discovery of:

- installed and project-recommended VS Code extensions;
- MCP configuration in the user Claude config, project `.mcp.json`, and Continue config when present;
- project agent configuration such as `.clinerules`, `.roo/`, `.continue/`, Copilot instructions, and `CLAUDE.md`;
- locally discoverable Claude plugin directories;
- optional WinGet package inventory.

Do not invent results for surfaces the scan cannot establish. Claude Code's own `/plugin list` may be used separately when the user wants the host's authoritative enabled-plugin state.

## 2. Build the overlap map

For each discovered item, distinguish:

- **specialist** — owns a domain Windows Dev Agent should route to rather than absorb;
- **orchestration overlap** — duplicates planning, generic shell routing, MCP aggregation, package/install orchestration, or environment discovery;
- **configuration only** — instructions or project policy that should be preserved as input;
- **unknown** — purpose is not established yet.

Do not recommend removal merely because two tools are both "agentic." Name the concrete duplicated capability or leave the relationship unresolved.

## 3. Choose a mode

Present the smallest set of materially different modes that fit the observed inventory:

**Absorb** — preserve existing tools/configs and make Windows Dev Agent the routing layer where overlap is useful.

**Route** — retain specialists and language tooling while disabling or retiring genuinely redundant orchestration surfaces.

**Clean house** — archive the exact configs being replaced, then remove only the user-approved redundant surfaces.

If the inventory does not justify a change, say so. Defrag may legally end as a read-only audit.

## 4. Mutate only after approval

Before any change:

1. show exact files/extensions/config entries affected;
2. state the backup/restore path;
3. distinguish disable, move/archive, edit, and delete;
4. obtain the required host/user approval for each mutating tool path.

Never delete first and call Git history or a vague backup strategy "rollback."

## 5. Verify and document

Re-scan the changed surfaces. Only then summarize what is active, what routes through Windows Dev Agent, what was retained, and how to undo the task-owned changes.

Create `AGENT_SETUP.md` only when the user wants a persistent project handoff. Do not write it merely to prove the command ran.
