---
description: Install packages or tools through the Windows Dev Agent package_install MCP tool. Use when the user asks to install a package or a missing dependency must be added. Execution is always host approval-gated.
---

# Package Install

Package installation is always `approval-required`. The Claude Code PreToolUse hook is the authority that prompts the human before an executing call reaches the MCP server.

## Procedure

1. Resolve the exact package identity. Prefer WinGet package IDs for Windows-native runtimes and developer tools.
2. Call `package_install` with `execute: false`. Present the returned source, argv, and command exactly as planned.
3. When installation is actually requested, call the same tool with `execute: true` and `user_approved: true`. This flag acknowledges the approval boundary; it does **not** bypass it. The bundled PreToolUse hook must still return `permissionDecision: ask`, and the call runs only if the user accepts that host prompt.
4. Inspect stdout/stderr and the return code. Do not infer success from having launched the installer.
5. Verify the installed tool with its version or availability command. If PATH propagation requires a new shell, say so rather than claiming the current shell is updated.

## Routing

- WinGet is the default for Windows runtimes and developer tools.
- Chocolatey or Scoop are fallbacks only when the requested package is intentionally sourced there.
- Windows optional features such as WSL or Hyper-V are **not** package installs; route them through an explicitly approved PowerShell action.
- Language package managers (`pip`, `uv`, `npm`, `cargo`) belong to the project/runtime workflow and should not be smuggled through this system-package tool.

## Boundaries

- Never interpolate a package ID into a shell string. The MCP server validates the ID and executes an argv vector with `shell=False`.
- Never set `execute: true` merely to test availability.
- Never claim an install succeeded without observing the installer result and a relevant post-install check.
- Never bypass or disable the PreToolUse hook to avoid the approval prompt.
