---
description: Inspect Windows host, runtime, toolchain, package-manager, and isolation state when those facts are needed for a development task.
---

# Environment Inspect

1. Call `env_inspect` when machine/runtime state can change the next action.
2. Read the returned snapshot as evidence. Present only fields the snapshot actually established; a degraded fallback is explicitly marked `status: degraded` and must not be described as a full Windows inventory.
3. Distinguish:
   - available and versioned;
   - missing;
   - discovered but unhealthy/misconfigured;
   - not established by the current snapshot.
4. Use `tool_discover` for a focused executable/version check when a full snapshot is unnecessary.
5. Use `ecosystem_scan` for MCP configs, VS Code extensions, project agent config, or Claude plugin inventory. Those are not silently part of `env_inspect`.
6. Suggest a repair only when the observed state blocks or materially degrades the requested task.

For package installation, route to the `package-install` skill / `package_install` MCP tool rather than emitting an unreviewed install command as if it had already been authorized.
