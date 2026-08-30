---
name: package-install
description: Resolve and install Windows packages or tools through Windows Dev Agent. Use when the user asks to install a package or a missing system dependency must be added. Execution is always host approval-gated.
---

# Package Install

Package installation is always `approval-required`. Claude forces executing package calls through its `PreToolUse` prompt. Codex configures `package_install` with native MCP approval mode `prompt`. The MCP `user_approved` field is defense-in-depth acknowledgement only; it is never proof of host permission.

## Procedure

1. **Establish package identity before mutation.** If the user or an authoritative project/config source supplied an exact package ID for the chosen manager, preserve it. Otherwise call `package_search` with the human package name and intended source; inspect the returned candidates and resolve the exact package identity before continuing. Do not guess an ID from naming conventions.
2. Call `package_install` with `execute: false`. Present the returned source, exact package ID, argv, and command as the planned mutation.
3. When installation is actually requested and the active host has granted permission, call the same tool with `execute: true` and `user_approved: true`. Do not set that acknowledgement pre-emptively merely to reach the host prompt.
4. Inspect stdout/stderr and the return code. Do not infer success from having launched the installer. A failed installer may still have changed host state.
5. Verify the resulting host state on the narrowest relevant surface: executable discovery, version output, or the task-specific check that required the package. Any executed package-install attempt invalidates Windows Dev Agent's cached environment snapshot because partial mutation is possible even when the installer exits nonzero. If full `env_inspect` is needed afterward, use fresh state. If PATH propagation requires a new shell, say so rather than claiming the current shell is updated.

## Routing

- WinGet is the default for Windows runtimes and developer tools.
- Chocolatey or Scoop are fallbacks only when the requested package is intentionally sourced there.
- Windows optional features such as WSL, Windows Sandbox, or Hyper-V are **not** package installs; use an explicitly approved host PowerShell action when the feature is actually required.
- Language package managers (`pip`, `uv`, `npm`, `cargo`) belong to the project/runtime workflow and should not be smuggled through this system-package tool.

## Boundaries

- Never interpolate a package ID into a shell string. The MCP server validates the ID and executes an argv vector with `shell=False`.
- Never set `execute: true` merely to test availability.
- Never claim an install succeeded without observing the installer result and a relevant post-install check.
- Never bypass, disable, or imitate the active host's permission boundary.
