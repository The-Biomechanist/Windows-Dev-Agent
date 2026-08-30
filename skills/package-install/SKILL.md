---
description: Resolve and install Windows packages or tools through the Windows Dev Agent package tools. Use when the user asks to install a package or a missing system dependency must be added. Execution is always host approval-gated.
---

# Package Install

Package installation is always `approval-required`. The Claude Code `PreToolUse` hook is the authority that prompts the human before an executing call reaches the MCP server.

## Procedure

1. **Establish package identity before mutation.** If the user or an authoritative project/config source supplied an exact package ID for the chosen manager, preserve it. Otherwise call `package_search` with the human package name and intended source; inspect the returned candidates and resolve the exact package identity before continuing. Do not guess an ID from naming conventions.
2. Call `package_install` with `execute: false`. Present the returned source, exact package ID, argv, and command as the planned mutation.
3. When installation is actually requested, call the same tool with `execute: true` and `user_approved: true`. This flag acknowledges the server-side boundary; it does **not** bypass the host hook. The bundled `PreToolUse` hook still returns `permissionDecision: ask`, and execution occurs only if the user accepts the host prompt.
4. Inspect stdout/stderr and the return code. Do not infer success from having launched the installer. A failed installer may still have changed host state.
5. Verify the resulting host state on the narrowest relevant surface: executable discovery, version output, or the task-specific check that required the package. Any executed package-install attempt invalidates Windows Dev Agent's cached environment snapshot because partial mutation is possible even when the installer exits nonzero. If full `env_inspect` is needed afterward, use the fresh state; if another intervening host change may also have invalidated it, call `force_refresh: true`. If PATH propagation requires a new shell, say so rather than claiming the current shell is updated.

## Routing

- WinGet is the default for Windows runtimes and developer tools.
- Chocolatey or Scoop are fallbacks only when the requested package is intentionally sourced there.
- Windows optional features such as WSL, Windows Sandbox, or Hyper-V are **not** package installs; use an explicitly approved PowerShell action when the feature is actually required.
- Language package managers (`pip`, `uv`, `npm`, `cargo`) belong to the project/runtime workflow and should not be smuggled through this system-package tool.

## Boundaries

- Never interpolate a package ID into a shell string. The MCP server validates the ID and executes an argv vector with `shell=False`.
- Never set `execute: true` merely to test availability.
- Never claim an install succeeded without observing the installer result and a relevant post-install check.
- Never bypass or disable the `PreToolUse` hook to avoid the approval prompt.
