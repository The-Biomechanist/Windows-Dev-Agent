---
description: Route command execution into WSL, a dev container, or Windows Sandbox through the sandbox_run MCP tool. Use when isolation is materially useful. Launching execution is always host approval-gated.
---

# Sandbox Run

Use isolation because the workload benefits from it, not as ceremony. The implemented runtime supports **WSL**, **Dev Containers**, and **Windows Sandbox**.

## Routing

| Situation | Preferred route | Runtime behavior |
| --- | --- | --- |
| Linux-native command or lightweight isolated test | WSL | Runs `wsl -- bash -lc <command>` and captures the result. |
| Reproducible project environment already using devcontainers | Dev Container | Runs through the `devcontainer` CLI and captures the result. |
| Untrusted or installer-like Windows workload | Windows Sandbox | On execution, generates a read-only mapped `.wsb` launch bundle and opens Windows Sandbox interactively. |

`environment: auto` chooses the first available route in this order: WSL, Dev Container, Windows Sandbox. Select an explicit environment when that ordering is not appropriate to the task.

## Procedure

1. Inspect relevant isolation availability with `env_inspect` when it is not already known.
2. Call `sandbox_run` with `execute: false` and inspect the selected environment and launch plan. Planning does not launch the workload or materialize a Windows Sandbox bundle.
3. If the selected route does not fit the task, choose an explicit supported environment or report the missing prerequisite.
4. To launch, call `sandbox_run` with `execute: true` and `user_approved: true`. The bundled PreToolUse hook still returns `permissionDecision: ask`; execution occurs only if the user accepts the host prompt.
5. For WSL and Dev Container runs, evaluate the captured return code/stdout/stderr. For Windows Sandbox, report only that the interactive sandbox was launched; do not claim the command succeeded without an observation from inside that sandbox.
6. Executed Windows Sandbox launches return the temporary config/cleanup path. Remove that temporary bundle only after the sandbox no longer needs it.

## Safety and scope

- Sandbox execution is `approval-required` even when the command itself looks harmless.
- Do not silently fall back from a requested isolation environment to host execution.
- Do not describe Hyper-V execution as supported by `sandbox_run`; Hyper-V remains an external/manual route.
- Do not map arbitrary host folders writable into Windows Sandbox. The generated runtime bundle is mapped read-only.
- Isolation does not establish semantic correctness. Verify the task result separately.
