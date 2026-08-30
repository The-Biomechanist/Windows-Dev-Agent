---
name: env-inspect
description: Inspect Windows host, runtime, toolchain, package-manager, and isolation state when those facts are needed for a development task.
---

# Environment Inspect

1. Call `env_inspect` when machine/runtime state can change the next action.
2. Read the returned snapshot as evidence. Availability values are tri-state:
   - `true` — observed present/enabled;
   - `false` — observed absent/disabled;
   - `null` — the probe did not establish the fact.
3. Treat top-level `status: degraded`, `snapshot.success: false`, or a relevant entry in `snapshot.errors` as partial evidence. Do not convert an unknown/failed probe into “missing.”
4. Use `tool_discover` for a focused executable/version check when a full snapshot is unnecessary. If the executable is present but its version probe fails, preserve `version_status: unknown` rather than inventing a version.
5. Treat cached environment state as time-bound. After an install, optional-feature change, PATH/toolchain mutation, or other action that can invalidate the field you are about to consume, prefer the narrow fresh probe; if a full snapshot is required, call `env_inspect` with `force_refresh: true`.
6. Use `ecosystem_scan` for MCP configs, extensions, project agent config, or host plugin inventory. Project-only inventory is the narrow default; request `include_host: true` only when user-level state is actually needed.
7. Suggest a repair only when observed state blocks or materially degrades the requested task.

For package installation, route to the `package-install` skill and `package_search` / `package_install` MCP tools rather than emitting an unreviewed install command.
