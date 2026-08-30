---
name: package-install
description: Resolve and install Windows packages or tools through Windows Dev Agent. Use when the user asks to install a package or a missing system dependency must be added. Execution is host approval-gated.
---

# Package Install

Package installation is `approval-required`. The executing MCP call contains the reviewed mutation and `execute: true`; the active host asks for permission around that same call. Do not invent a second model-supplied flag that claims approval happened before the host has actually asked.

`package_search` is non-mutating by intent, but it executes the selected package manager and may contact its configured source. It therefore remains on the active host permission surface rather than being silently auto-approved. `package_install` also remains host-controlled; in Codex, only `execute: false` plan calls may be auto-approved by the optional trusted `PermissionRequest` hook.

## Procedure

1. **Establish package identity before mutation.** Preserve an exact package ID supplied by the user or authoritative project/config state. Otherwise call `package_search`, inspect the candidates, and resolve the exact identity. Do not guess an ID from naming conventions.
2. Call `package_install` with `execute: false`. Review the returned source, exact package ID, `resolved_executable`, argv, command, agreement flags, and `plan_fingerprint` as one concrete mutation plan.
3. When installation is actually requested, call the same tool with the same package ID/source and `execute: true`, carrying the returned `plan_fingerprint` unchanged. The fingerprint proves only that the executable/argv plan is still the reviewed one; it does **not** represent user approval. The active host's permission system still decides whether that exact executing call proceeds.
4. If execution returns `stale_plan`, treat the prior plan as invalidated: do not substitute a different package manager or retry the old fingerprint. Obtain a fresh `execute: false` plan, review the changed binding, then execute only that fresh plan if the task still calls for it.
5. Inspect stdout/stderr, return code, and `execution_started`. Do not infer success from installer invocation. A failed installer can still partially mutate host state.
6. Verify the resulting host state on the narrowest relevant surface: executable discovery, version output, or the task-specific check that required the package. Any executed install attempt invalidates the cached environment snapshot. If a full snapshot is needed afterward, use fresh state.

## Routing

- WinGet is the default for Windows runtimes and developer tools.
- Chocolatey or Scoop are fallbacks only when the requested package is intentionally sourced there.
- Windows optional features such as WSL, Windows Sandbox, or Hyper-V are **not** package installs; use an explicitly approved host PowerShell action when the feature is actually required.
- Language package managers (`pip`, `uv`, `npm`, `cargo`) belong to the project/runtime workflow and should not be smuggled through this system-package tool.

## Boundaries

- Never interpolate a package ID into a shell string. The MCP server validates it and executes an argv vector with `shell=False`.
- Never set `execute: true` merely to test availability.
- Never treat `plan_fingerprint` as approval, authorization, or a durable session token. It is only a freshness/identity check for the returned plan.
- Never claim an install succeeded without observing the installer result and a relevant post-install check.
- Never bypass, disable, or imitate the active host's permission boundary.
