---
description: Inspect the Windows development environment and, when relevant, the surrounding agent/tool configuration surface.
---

Call `env_inspect` for machine/runtime/toolchain state.

Present only relevant sections from the observed snapshot, such as:

- Windows version/build and architecture;
- CPU/memory when material to the task;
- PowerShell and package-manager availability;
- Python, Node, Rust, Go, .NET and other relevant runtimes;
- WSL / Windows Sandbox / virtualization state when isolation matters;
- Git, Docker, editors, or build tools when they bear on the request.

If the user also wants MCP servers, VS Code agent extensions, project agent configs, or installed Claude plugins, call `ecosystem_scan`. Do not imply `env_inspect` established those surfaces when it did not.

Distinguish missing, unavailable, misconfigured, and simply uninspected state. Suggest repairs only for conditions that actually block the requested task.
