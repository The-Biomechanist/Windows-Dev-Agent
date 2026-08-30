---
name: ecosystem-defrag
description: Inventory an existing Windows agent/developer-tool setup, identify concrete capability overlap, and plan or execute a reversible consolidation when the setup is fragmented.
---

# Ecosystem Defrag

Use this when the agent/developer-tool environment itself has become duplicated, contradictory, or hard to maintain. A clean inventory with no justified change is a valid terminal result.

## 1. Establish the inventory

Call `ecosystem_scan` for the current project. In Codex, pass the current session/project directory explicitly as `cwd`. Use `include_packages: true` only when installed WinGet packages are relevant; it is slower and usually unnecessary for agent-config cleanup.

The scan may establish:

- installed and project-recommended VS Code extensions;
- MCP configuration surfaces it can read safely;
- project agent configuration such as `.clinerules`, `.roo/`, `.continue/`, Copilot instructions, `CLAUDE.md`, or Codex `.agents/` configuration when present;
- locally discoverable plugin directories for the active host;
- optional WinGet package inventory.

Do not invent enabled/disabled plugin state or configuration meaning the scan cannot establish. Treat unavailable host-owned state as `unknown` unless an authoritative host surface is actually queried.

## 2. Build the overlap map

For each discovered item, distinguish:

- **specialist** — owns a domain Windows Dev Agent should route to rather than absorb;
- **orchestration overlap** — duplicates planning, generic shell routing, MCP aggregation, package/install orchestration, or environment discovery;
- **configuration only** — instructions or project policy that should be preserved as input;
- **unknown** — purpose or ownership is not established yet.

Do not recommend removal merely because two tools are both agentic. Name the concrete duplicated capability or leave the relationship unresolved.

## 3. Choose the smallest justified mode

**Absorb** — preserve existing tools/configs and make Windows Dev Agent the routing layer where overlap is useful.

**Route** — retain specialists and language tooling while disabling or retiring genuinely redundant orchestration surfaces.

**Clean house** — archive the exact configs being replaced, then remove only user-approved redundant surfaces.

If the inventory does not justify a change, stop with the read-only audit.

## 4. Mutate only under the active host authority

Before any change:

1. show exact files/extensions/config entries affected;
2. state the real backup/restore path;
3. distinguish disable, move/archive, edit, and delete;
4. use the active host's ordinary approval boundary for every mutation.

Never delete first and call Git history or a vague backup strategy rollback.

## 5. Verify and document

Re-scan the changed surfaces. Only then summarize what is active, what routes through Windows Dev Agent, what was retained, what remains unknown, and how to undo the task-owned changes.

Create `AGENT_SETUP.md` only when the user wants a persistent project handoff. Do not write it merely to prove the workflow ran.
