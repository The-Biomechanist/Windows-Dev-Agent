---
name: package-install
description: Resolve and install Windows packages or tools through Windows Dev Agent. Use when the user asks to install a package or a missing system dependency must be added. Execution is host approval-gated.
---

# Package Install

Package installation is `approval-required`. The executing MCP call contains the reviewed mutation and `execute: true`; the active host asks for permission around that same call. Do not invent a second model-supplied flag that claims approval happened before the host has actually asked.

`package_search` is non-mutating by intent, but it executes the selected package manager and may contact its configured source. It therefore remains on the active host permission surface rather than being silently auto-approved. `package_install` also remains host-controlled; in Codex, only `execute: false` plan calls may be auto-approved by the optional trusted `PermissionRequest` hook.

## Procedure

1. **Establish package identity before mutation.** Preserve an exact package ID supplied by the user or authoritative project/config state. Otherwise call `package_search`, inspect the candidates, and resolve the exact identity. Do not guess an ID from naming conventions.
2. Call `package_install` with `execute: false`. Present the returned source, exact package ID, `executable`, `executable_identity_kind`, `executable_identity_sha256`, argv, command, and agreement flags as the planned mutation. The path plus typed identity fingerprint form the reviewed execution identity.
3. When installation is actually requested, call the same tool with `execute: true` and copy the plan values unchanged into `expected_executable`, `expected_executable_identity_kind`, and `expected_executable_identity_sha256`. These are identity preconditions, not approval tokens. If the runtime returns `stale_plan`, obtain a fresh `execute: false` plan instead of substituting newly observed identity values into the old plan. The active host's permission system decides whether the executing call proceeds.
4. Inspect stdout/stderr, return code, and `execution_started`. Do not infer success from installer invocation. A failed installer can still partially mutate host state.
5. Verify the resulting host state on the narrowest relevant surface: executable discovery, version output, or the task-specific check that required the package. Package execution invalidates the cached environment snapshot before the installer starts; if that invalidation cannot be established, the runtime refuses to launch the installer. If a full snapshot is needed afterward, use fresh state.

## Routing

- WinGet is the default for Windows runtimes and developer tools.
- Chocolatey or Scoop are fallbacks only when the requested package is intentionally sourced there.
- Windows optional features such as WSL, Windows Sandbox, or Hyper-V are **not** package installs; use an explicitly approved host PowerShell action when the feature is actually required.
- Language package managers (`pip`, `uv`, `npm`, `cargo`) belong to the project/runtime workflow and should not be smuggled through this system-package tool.

## Boundaries

- Never interpolate a package ID into a shell string. The MCP server validates it and executes an argv vector with `shell=False`.
- Never set `execute: true` merely to test availability.
- Never change any `expected_executable*` identity field merely to make an old plan executable; changed path, kind, or fingerprint invalidates the plan.
- Never claim an install succeeded without observing the installer result and a relevant post-install check.
- Never bypass, disable, or imitate the active host's permission boundary.
