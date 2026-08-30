---
name: ecosystem-defrag
description: Inventory an existing Windows agent/developer-tool setup, identify concrete capability overlap, and plan or execute a reversible consolidation when the setup is fragmented.
---

# Ecosystem Defrag

Use this when the agent/developer-tool environment itself has become duplicated, contradictory, or hard to maintain. A clean inventory with no justified change is a valid terminal result.

## 1. Establish the inventory

Start with `ecosystem_scan` for the current project and `include_host: false`. This bounded scan owns project-local VS Code recommendations, MCP config, and agent configuration such as `.clinerules`, `.roo/`, `.continue/`, `.agents/`, Copilot instructions, and `CLAUDE.md` when present.

Request `include_host: true` only when user-level extensions, plugins, MCP configuration, or installed package state is actually needed to decide the consolidation. That broader read remains on the active host permission surface. Use `include_packages: true` only together with `include_host: true` and only when installed WinGet packages matter.

In Codex, `ecosystem_scan` remains on native approval even for project-only requests. The adapter requires a caller-supplied `cwd` but cannot independently prove that an arbitrary supplied directory is the active project, so the optional trusted hook does not auto-approve this filesystem inventory read.

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

Re-scan only the changed surfaces, widening back to host inventory only when the claimed effect lives there. Then summarize what is active, what routes through Windows Dev Agent, what was retained, what remains unknown, and how to undo the task-owned changes.

Create `AGENT_SETUP.md` only when the user wants a persistent project handoff.
