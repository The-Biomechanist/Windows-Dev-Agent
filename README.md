# Windows Dev Agent

Windows-native development orchestration for **Claude Code and Codex**.

Windows Dev Agent (WDA) gives an agent a small, shared runtime for inspecting a Windows development environment, routing project work, resolving and installing system packages, choosing an appropriate isolation boundary, and auditing the resulting actions without turning host permission into a model-controlled flag.

The runtime is local, Python-standard-library-only, and designed around explicit Windows authority boundaries: the host owns permission, project scope comes from the host adapter, external effects are not inferred from requests, and unavailable or unobserved state remains unknown rather than being guessed.

## What it adds

WDA exposes ten MCP tools through both host adapters:

| Tool | Purpose |
| --- | --- |
| `env_inspect` | Build a tri-state Windows environment snapshot with a bounded cache. |
| `tool_discover` | Resolve common developer tools to exact executables and probe versions. |
| `capability_run` | Plan or execute a registered argv-based capability. |
| `workflow_plan` | Select a capability only when task evidence actually distinguishes one. |
| `package_search` | Search an installed Windows package manager for an exact package identity. |
| `package_install` | Plan or execute one exact WinGet, Chocolatey, or Scoop install. |
| `sandbox_run` | Route execution to WSL, a project Dev Container, or Windows Sandbox from an explicit isolation requirement. |
| `ecosystem_scan` | Inventory project agent/tool configuration, with broader host reads opt-in. |
| `logs_query` | Query bounded retained WDA audit metadata. |
| `mcp_audit` | Inspect project MCP configuration without returning secrets or raw command values. |

Six shared skills provide the procedural layer: `env-inspect`, `workflow-plan`, `package-install`, `sandbox-run`, `win-setup`, and `ecosystem-defrag`.

## Requirements

- Windows 10 or Windows 11 for the native runtime.
- Windows PowerShell 5.1 or newer.
- **Python 3.11 or newer.** WDA does not install Python for itself.
- A current Claude Code or Codex build with plugin/MCP support.
- Optional backends only when you use them: WSL, the Dev Container CLI plus project configuration, or Windows Sandbox.

There are no third-party Python runtime dependencies. Development uses pytest.

## Install

### Claude Code

For a local checkout or development build:

```text
claude --plugin-dir /absolute/path/to/Windows-Dev-Agent
```

Claude loads the plugin from its root for that session. The plugin uses `${CLAUDE_PLUGIN_ROOT}` for code, `${CLAUDE_PLUGIN_DATA}` for persistent WDA state, and `${CLAUDE_PROJECT_DIR}` as the project authority boundary. It does not depend on the process current directory to import the runtime.

### Codex

Codex plugin installation is marketplace-based. With this repository configured as a marketplace, install the published plugin with:

```text
codex plugin marketplace add The-Biomechanist/Windows-Dev-Agent
codex plugin add windows-dev-agent@windows-dev-agent
```

The published marketplace entry is pinned to an immutable payload commit. Installed code is separate from `${CODEX_HOME:-~/.codex}/plugins/data/windows-dev-agent`, where WDA keeps its bounded cache and audit state.

Codex uses the installed plugin root as the MCP server's startup working directory so the relative launcher path resolves inside plugin code. That startup directory is **not** treated as project identity: project-scoped WDA tools still require the current absolute Codex project directory explicitly.

Codex plugin hooks are an additional trusted layer, not a replacement for native permissions. Until the user trusts those hooks, Codex's own MCP/shell approval policy remains the operative boundary. Mutation-capable WDA tools remain prompt-gated.

## Python bootstrap

MCP servers and hooks do not execute a bare `python` from the active project's current directory or inherited PATH.

`scripts/launch-python.ps1` resolves Python from Windows installation authorities and standard host installation locations, rejects interpreters older than 3.11, then launches the selected interpreter in isolated mode (`-I`). A managed host or CI environment may supply `WINDOWS_DEV_AGENT_PYTHON`, but it must be an **absolute `python.exe` path** and is version-checked before use. The launcher is compatible with Windows PowerShell 5.1 and does not require newer .NET path APIs.

The launcher then imports only the requested `src.*` WDA entrypoint from the plugin root. WDA never silently installs or upgrades the interpreter.

## Permission and authority model

WDA does not accept a model-supplied `user_approved` bit.

```text
agent constructs exact call
→ Claude Code or Codex applies host permission policy
→ permitted call reaches WDA
→ WDA validates the request again at execution boundary
→ external result is observed separately from the request
```

`execute: false` produces a plan. `execute: true` asks the active host to permit the exact execution request. The runtime independently blocks forbidden capability classes and rejects malformed direct MCP calls even when a client skipped advertised JSON-schema validation.

Claude's `PreToolUse` adapter can tighten the host decision (`ask` or `deny`) but never grants permission itself. Codex uses its native approval modes; trusted hooks may remove a prompt only for narrowly proven plan-only calls. Sandbox planning is intentionally not auto-allowed.

## Project boundaries and reads

Claude project-scoped tools are restricted to `${CLAUDE_PROJECT_DIR}` or a descendant. Codex requires the current absolute project directory explicitly because installed plugin code lives in a separate cache.

Project-local configuration reads—including `.mcp.json`, `.continue/config.json`, `.vscode/extensions.json`, agent configuration markers, and Dev Container configuration detection—are checked component-by-component before use. A symbolic link or NTFS reparse point that would redirect a project-scoped read outside the intended tree is rejected rather than followed.

MCP audit results contain structural metadata only. Raw command strings, URLs, argument values, and environment values are not returned.

## Environment discovery

Availability is tri-state:

- `true`: observed present/enabled;
- `false`: observed absent/disabled;
- `null`: not established by the probe.

Native discovery is performed with Windows-owned PowerShell and returns one canonical `EnvironmentSnapshot` shape even when the probe degrades or fails unexpectedly.

The cache uses the same canonical snapshot representation, is capped at 1 MiB, written atomically, and expires after five minutes. Package-install execution must first advance the cache mutation generation and invalidate the prior snapshot; if that authority transition cannot be established, the installer is not started. A discovery that began against an older generation cannot later resurrect a stale cache entry.

## Package execution

WDA resolves the selected package-manager executable once and executes that exact absolute identity. It does not verify one path and then perform a second PATH lookup at execution time.

`package_search` can contact the package manager's configured source and therefore remains host-controlled even though it is non-mutating by intent. `package_install(execute:false)` returns the exact argv for review; `execute:true` requests that same operation under host approval. WinGet installation is bound to the `winget` source and runs non-interactively.

Installer exit status is not proof that the requested development task now works. Re-inspect or verify the actual post-state that matters.

## Process execution

All captured subprocess execution goes through one bounded runner. It:

- requires an absolute executable identity;
- disconnects child stdin from the MCP transport;
- streams stdout/stderr while retaining only bounded tails in memory;
- applies a runtime timeout;
- preserves whether execution actually started;
- attempts process-tree termination on Windows after timeout.

Timeout after launch is not treated as proof of failure-with-no-effect: partial external mutation may already have occurred, so audit state can remain `unknown`.

## Isolation

Every `sandbox_run` call names the property it requires:

| Requirement | Backend |
| --- | --- |
| `linux_compatibility` | WSL |
| `project_reproducibility` | configured project Dev Container |
| `untrusted_windows` | Windows Sandbox |

`environment:auto` chooses the backend dictated by that property. If the caller names an explicit backend that does not satisfy the requirement, WDA rejects the request instead of silently weakening the boundary.

### Windows Sandbox

For `untrusted_windows`, `payload_paths` is mandatory whether routing is automatic or explicit. Paths must be workspace-relative ordinary files/directories. Staging rejects escapes, symbolic links, NTFS reparse points, overlapping roots, more than 10,000 filesystem entries, or more than 1 GiB of files.

Only the generated payload share is mapped into Windows Sandbox and it is read-only. The generated `.wsb` configuration remains outside that mapped share. Host-facing settings are hardened for this route:

```xml
<vGPU>Disable</vGPU>
<Networking>Disable</Networking>
<AudioInput>Disable</AudioInput>
<VideoInput>Disable</VideoInput>
<PrinterRedirection>Disable</PrinterRedirection>
<ClipboardRedirection>Disable</ClipboardRedirection>
```

WDA owns cleanup responsibility for its temporary Sandbox bundles. It performs best-effort cleanup after the launched Sandbox process exits when that process lifetime is usable as a cleanup witness, and it also runs stale-bundle collection at host startup and before later Sandbox launches. Callers are not given a host cleanup path to remember.

A returned `launched` status proves only that Windows Sandbox was launched. It does not establish the inner command's success or that the launched process handle is a universal Windows Sandbox session-lifetime oracle.

## Audit and privacy

WDA persists only the metadata needed by its audit consumers. It does **not** retain raw commands, MCP arguments, stdout/stderr, or arbitrary tool responses.

New audit records carry a schema version plus explicit lifecycle fields. Legacy retained records remain readable. Rotate-and-append is serialized between independent Windows hook processes so concurrent Claude/Codex hooks cannot race log rotation.

Execution outcomes distinguish `succeeded`, `failed`, `unknown`, `not_executed`, and `not_applicable`. Lifecycle success and external-effect success are separate facts.

`agent.log` is bounded to 2 MiB plus one rotated predecessor. `logs_query` returns audit events and counts without exposing the physical user-home/plugin-data path.

## Repository layout

```text
Windows-Dev-Agent/
├── .agents/plugins/marketplace.json
├── .claude-plugin/plugin.json
├── .codex-plugin/plugin.json
├── .github/workflows/ci.yml
├── .mcp.json
├── .mcp.codex.json
├── capabilities.json
├── hooks/
├── scripts/launch-python.ps1
├── skills/
├── src/
│   ├── capabilities.py
│   ├── execution.py
│   ├── claude_server.py
│   ├── codex_server.py
│   ├── discovery/
│   ├── mcp/
│   │   ├── server.py
│   │   └── stdio.py
│   ├── models/
│   ├── observability/
│   └── safety/
└── tests/
```

Host adapters own host-specific project/data/permission binding. `src/mcp/server.py` is the host-neutral core and is not exposed as a third directly executable runtime. Claude and Codex share one bounded stdio transport and one Windows execution core.

## Development and verification

```text
python -m pip install -r requirements-dev.txt
python -m compileall -q src
python -m pytest tests -q
```

GitHub Actions runs on `windows-latest` against the supported Python floor and a current Python release, currently 3.11 and 3.14. CI also exercises the shipped Windows PowerShell 5.1 bootstrap path, the native PowerShell discovery producer, both MCP adapter surfaces, Windows reparse/junction containment, and the published-release ancestry contract.

A green CI run supports only the surfaces those checks can observe. It does not establish that a person accepted a real Claude/Codex permission dialog, that an installed host UI behaves identically to the test harness, or that an interactive Windows Sandbox workload completed on an end-user desktop.

## Release integrity

Published Codex releases use an immutable two-commit identity:

```text
index-free payload commit
→ index-only commit pinned to that payload SHA
→ normal development/merge ancestry
```

CI locates the commit that introduced the current marketplace index and proves that its direct parent is the pinned index-free payload, that the index commit changes only the marketplace file, and that the published index remains an ancestor of the current branch. Ordinary later development is allowed to differ from the last published payload.

See [CHANGELOG.md](CHANGELOG.md) for release history and [SECURITY.md](SECURITY.md) for the security boundary and reporting guidance.

## License

MIT. See [LICENSE](LICENSE).
