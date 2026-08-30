---
name: env-inspect
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
5. Treat cached environment state as time-bound. After an install, optional-feature change, PATH/toolchain mutation, or other action that can invalidate the field you are about to consume, prefer the narrow fresh probe; if a full snapshot is required, call `env_inspect` with `force_refresh: true`.
6. Use `ecosystem_scan` for MCP configs, VS Code extensions, project agent config, or host plugin inventory. Those are not silently part of `env_inspect`.
7. Suggest a repair only when the observed state blocks or materially degrades the requested task.

For package installation, route to the `package-install` skill and `package_search` / `package_install` MCP tools rather than emitting an unreviewed install command as if it had already been authorized.
