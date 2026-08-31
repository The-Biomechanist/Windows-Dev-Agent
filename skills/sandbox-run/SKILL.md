---
name: sandbox-run
description: Route command execution through WSL, a project Dev Container, or Windows Sandbox when that boundary materially fits the task. Launching execution is host approval-gated.
---

# Sandbox Run

Choose the boundary from the property the task requires, not from whichever backend happens to be installed first.

## Routing

| Requirement | `isolation_requirement` | Required backend | Boundary |
| --- | --- | --- | --- |
| Linux-native compatibility or a low-risk Linux execution surface | `linux_compatibility` | WSL | Interoperable Linux environment; **not** hostile-code containment from the Windows host. |
| Reproduce the project's declared devcontainer environment | `project_reproducibility` | Dev Container | Requires the `devcontainer` CLI and an actual project `devcontainer.json` configuration. |
| Run an untrusted Windows artifact away from the host | `untrusted_windows` | Windows Sandbox | Disposable Windows VM with host-facing device/redirection surfaces disabled and only explicitly staged input mapped read-only. |

Every `sandbox_run` call requires `isolation_requirement`. `environment: auto` selects the backend required by that property. An explicit `environment` is legal only when it is the backend that satisfies the same requirement; the runtime rejects incompatible pairs instead of reporting a weaker boundary as sufficient.

For every `untrusted_windows` request, supply one or more workspace-relative `payload_paths` whether routing is automatic or explicit. The runtime refuses a hostile-workload plan that would launch Windows Sandbox without the workload staged inside it.

## Procedure

1. Establish the property the boundary must provide. Do not silently substitute Linux compatibility for hostile-Windows containment.
2. Inspect relevant backend availability with `env_inspect` when it is not already established. Treat `null`/unknown availability as unresolved rather than missing.
3. For WSL or Dev Container work, use the active project as `workspace_folder`. WSL execution enters that Windows project directory through WSL's `--cd` boundary and uses `sh -lc`; a task requiring Bash-specific semantics should request Bash explicitly. Project reproducibility requires an actual `.devcontainer/devcontainer.json` or root `.devcontainer.json`; a bare `.devcontainer/` directory is not configuration.
4. For Windows Sandbox, identify exactly which project files/directories the isolated command needs and pass them as workspace-relative `payload_paths`. Every selected path component and every traversed child must remain an ordinary workspace path: symbolic links and NTFS reparse points such as junctions are rejected before traversal crosses them, and failure to establish that metadata blocks staging rather than being treated as safe. Overlapping selections are rejected, and staged input is limited to 10,000 filesystem entries and 1 GiB total file bytes. The runtime re-establishes those identities and budgets again while copying from use-time verified handles, so a path swapped or enlarged after nomination is rejected. Write the inner command relative to the staged payload root (`C:\WDAShare\payload`).
5. Call `sandbox_run` with `execute: false` and inspect the selected route, requirement, payload list, absolute `executable`, `executable_identity_kind`, `executable_identity_sha256`, and launch plan. Planning must not create the temporary bundle or launch the workload. Planning remains subject to the active host's native permission policy; do not assume a trusted hook will auto-allow Sandbox planning.
6. To launch, call the same reviewed tool with `execute: true` and copy those identity values unchanged into `expected_executable`, `expected_executable_identity_kind`, and `expected_executable_identity_sha256`. This binds execution to the reviewed backend object through process creation; it is not approval. If the runtime returns `stale_plan`, obtain a fresh plan rather than replacing expected identity values in-place. The active host decides whether the executing call proceeds; do not invent a second approval token.
7. For WSL and Dev Container runs, use captured return code/stdout/stderr only for what they establish. For Windows Sandbox, report only that the interactive sandbox launched; the inner command remains `unknown` until an observation from inside the sandbox establishes its outcome.
8. Windows Dev Agent owns cleanup responsibility for Sandbox bundles. It performs best-effort cleanup after the launched Sandbox process exits when that process lifetime is usable as a cleanup witness, and removes stale WDA-owned bundles at host startup and before later Sandbox launches. Do not ask the user/model to manage a host cleanup path.

## Windows Sandbox boundary

For `untrusted_windows`, the generated configuration disables vGPU, networking, audio input, video input, printer redirection, and clipboard redirection. The staged share is mapped read-only. The `.wsb` configuration itself is stored outside the mapped share so the untrusted workload is not given its host-side configuration path.

A `launched` result establishes that WDA started Windows Sandbox; it does not establish the inner command's success or make the returned process handle a universal Sandbox session-lifetime oracle. Cleanup therefore combines the best-effort process witness with stale-bundle collection rather than asking the caller to manage the bundle.

These controls reduce host exposure; they do not turn Windows Sandbox into a proof of semantic correctness or guarantee resistance to every sandbox escape. Verify the task result separately.

## Safety and scope

- Sandbox execution is `approval-required` even when the inner command looks harmless.
- WSL interoperability is not a substitute for Windows Sandbox containment.
- Hyper-V is not an implemented `sandbox_run` backend.
- Do not map arbitrary host folders writable into Windows Sandbox. Only the generated staging share is mapped, read-only.
- Never update any `expected_executable*` identity field merely to make a stale plan run; re-plan instead.
- Isolation establishes a boundary, not semantic correctness. Verify the task result separately.
