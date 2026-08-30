# Windows Dev Agent

A Windows-native developer-orchestration plugin for **Claude Code and Codex**, built around one shared runtime and one shared skill set.

Windows Dev Agent inspects the real Windows host, routes development workflows, resolves and installs system packages, selects appropriate isolation, and helps consolidate fragmented agent/tool setups. The control plane stays Windows-native where Windows owns the problem: PowerShell, WinGet, WMI/CIM, .NET/MSBuild, and Windows management surfaces come first. WSL, Dev Containers, and Windows Sandbox are selected only when their actual boundary fits the task.

Release **0.3.0** adds a Codex/ChatGPT desktop adapter without forking the implementation.

## One core, two host adapters

```text
shared Windows Dev Agent core
  skills/                       host-neutral procedures
  capabilities.yaml             executable capability catalog
  src/mcp/server.py             Windows behavior + 10 MCP tools
  src/capabilities.py
  src/discovery/
  src/models/environment.py
        │
        ├── Claude Code adapter
        │     .claude-plugin/plugin.json
        │     .mcp.json
        │     agents/
        │     commands/
        │     hooks/hooks.json
        │
        └── Codex adapter
              .codex-plugin/plugin.json
              .mcp.codex.json
              .agents/plugins/marketplace.json
              hooks/codex-hooks.json
              src/codex_server.py
              src/safety/codex_gate.py
              src/observability/codex_*.py
```

Host adapters own only host-specific packaging, path binding, permission integration, and hook output contracts. Windows behavior, capability semantics, and reusable procedures stay shared.

## Shared skills

| Skill | Purpose |
| --- | --- |
| `env-inspect` | Inspect Windows host, runtime, toolchain, package-manager, and isolation state. |
| `workflow-plan` | Build a bounded Windows-aware execution plan when planning can change the route. |
| `package-install` | Resolve exact Windows package identity, plan installation, execute under host approval, and verify fresh state. |
| `sandbox-run` | Route execution through WSL, a project Dev Container, or Windows Sandbox from the required isolation property. |
| `win-setup` | Repair or bootstrap missing/broken Windows development prerequisites without rewriting unrelated machine state. |
| `ecosystem-defrag` | Inventory agent/developer-tool overlap and plan or execute a reversible consolidation. |

Claude slash commands are thin adapters to those skills rather than separate workflow copies:

- `/windows-dev-agent:env` → `env-inspect`
- `/windows-dev-agent:plan <task>` → `workflow-plan`
- `/windows-dev-agent:defrag` → `ecosystem-defrag`

Codex consumes the shared skills directly.

## Install / run

### Claude Code

From a checkout:

```text
claude --plugin-dir .
```

The Claude adapter uses `.claude-plugin/plugin.json`, `.mcp.json`, the Windows orchestrator agent, slash commands, and Claude-specific hooks.

### Codex / ChatGPT desktop

This repository carries a Codex plugin manifest at `.codex-plugin/plugin.json` and a repo marketplace at `.agents/plugins/marketplace.json`. Add the repository as a marketplace source:

```text
codex plugin marketplace add The-Biomechanist/Windows-Dev-Agent
```

Then restart the ChatGPT desktop app, choose the **Windows Dev Agent** marketplace in the Plugins Directory, and install `windows-dev-agent` from that source.

The marketplace uses a Git-backed root plugin source rather than a local `./` path, so the canonical repository remains the single plugin tree.

## Runtime requirements

- Windows 10 or Windows 11 for full native behavior;
- PowerShell 5.1+;
- Python 3.9+;
- a current Claude Code release for the Claude adapter;
- a current Codex / ChatGPT desktop plugin runtime for the Codex adapter.

The plugin runtime uses only Python's standard library. Development tests use pytest.

## Shared MCP tools

Both host adapters expose the same 10 runtime tools:

| Tool | Purpose |
| --- | --- |
| `env_inspect` | Native Windows environment snapshot with explicit `force_refresh`. |
| `tool_discover` | Focused runtime/editor/package-manager/VCS discovery. |
| `capability_run` | Plan or execute a registered argv-based capability with `shell=False`. |
| `workflow_plan` | Deterministic capability-aware execution scaffold. |
| `package_search` | Search an installed Windows package manager to resolve package identity before mutation. |
| `package_install` | Plan or execute an exact WinGet, Chocolatey, or Scoop install. |
| `sandbox_run` | Route through WSL, a configured project Dev Container, or Windows Sandbox. |
| `ecosystem_scan` | Read-only inventory for agent/tool consolidation. |
| `logs_query` | Query minimal persistent Windows Dev Agent audit metadata. |
| `mcp_audit` | Inspect supported MCP configuration surfaces without exposing environment values. |

The Codex MCP server key is internally `windows_dev_agent` so the current Codex runtime can expose a callable namespace reliably; this does not change the plugin/product name `windows-dev-agent`.

## Project and state binding

The two hosts provide different installation/runtime geometry, so their adapters bind state differently instead of pretending one path contract is universal.

### Claude

`.mcp.json` separates:

- `${CLAUDE_PLUGIN_ROOT}` — plugin code/config and MCP working directory;
- `${CLAUDE_PLUGIN_DATA}` — persistent discovery cache and audit metadata;
- `${CLAUDE_PROJECT_DIR}` — the active project and default project-scoped execution surface.

### Codex

Codex installs plugins into its plugin cache, so that cache must **not** become the user's project by accident.

- `.mcp.codex.json` starts the shared runtime through `src.codex_server` with plugin-root `cwd: "."`.
- The adapter uses the host plugin-data directory when available; otherwise it falls back to a stable user-writable Codex data directory.
- Project-scoped tools require the current Codex session/project directory explicitly:
  - `capability_run.cwd`
  - `workflow_plan.cwd`
  - `sandbox_run.workspace_folder`
  - `ecosystem_scan.cwd`
  - `mcp_audit.cwd`

The adapter rejects those calls when project identity is missing rather than operating on its installed cache.

## Permission model

Windows Dev Agent never treats a model-supplied `user_approved: true` value as authority. That field is only server-side acknowledgement after the active host has actually granted permission.

### Claude Code

The Claude `PreToolUse` hook is tightening-only over Claude's native permission system:

| Effective class | Claude adapter |
| --- | --- |
| `read-only` | no plugin decision; native permission flow remains authoritative |
| `reversible` | no plugin decision; native permission flow remains authoritative |
| `approval-required` | force `ask` |
| `checkpoint` | force `ask` |
| `forbidden` | `deny` |

The hook covers Bash, Claude's native PowerShell tool, and Windows Dev Agent mutation-capable MCP calls. It never returns `allow`.

### Codex

Codex has a different hook contract, so the adapter does **not** emulate Claude's `ask` decision.

- `.mcp.codex.json` sets native MCP tool approval policy:
  - read-only orchestration/discovery tools → `approve`;
  - `capability_run`, `package_install`, `sandbox_run` → `prompt`.
- `hooks/codex-hooks.json` runs a Codex-specific `PreToolUse` guard that can deny known-forbidden effective actions but otherwise emits no permission decision and defers to Codex.
- Shell execution remains subject to Codex's native shell/sandbox permission policy.

This keeps the invariant simple: **Windows Dev Agent decides what operation fits; the host decides whether execution is permitted.**

## Effective-action safety

The shared classifier and capability runtime avoid granting authority from a friendly-looking launcher name alone:

- compound, redirected, substituted, or dynamically invoked shell commands cannot inherit a read-only prefix classification;
- caller-supplied capability `extra_args` cannot inherit a weaker base capability classification;
- project build/test/lint launchers are not assumed harmless merely because their executable name is familiar;
- forbidden capabilities remain blocked by the MCP runtime itself;
- child processes receive `DEVNULL` stdin instead of inheriting the MCP JSON-RPC transport.

The Codex adapter normalizes its underscore MCP namespace back to the shared classifier without changing the shared Claude contract.

## Capability routing

`capabilities.yaml` is intentionally small and executable. It uses the JSON-compatible subset of YAML so the runtime can parse it with Python's standard `json` module.

The current catalog covers:

- Git working-tree inspection;
- Python and JavaScript/TypeScript linting;
- Python and .NET tests;
- .NET builds;
- GitHub PR creation as an approval-required publication action.

Configured commands are argv arrays. Runtime execution uses `shell=False`.

## Package identity and freshness

A package mutation should not begin from a guessed ID. Use an exact ID supplied by the user or authoritative project/config state, or call `package_search` and resolve the matching candidate before `package_install`.

WinGet search is noninteractive and does not auto-accept source agreements. If a source agreement prevents discovery, that unresolved prerequisite is surfaced rather than silently accepted by a read-only search.

Any executed package-install attempt invalidates the cached environment snapshot because an installer can partially mutate host state even when it exits nonzero. Downstream verification should use the narrowest fresh probe or `env_inspect(force_refresh=true)` when a full refreshed snapshot is actually required.

## Isolation

`environment: auto` does **not** mean "first backend installed." It requires `isolation_requirement` and routes by the property the task needs:

- `linux_compatibility` → WSL;
- `project_reproducibility` → a configured project Dev Container;
- `untrusted_windows` → Windows Sandbox.

WSL is an interoperable Linux environment and is not treated as hostile-Windows containment. Windows Sandbox plans do not materialize a bundle. On approved execution, the runtime creates a temporary `.wsb` bundle with networking and clipboard disabled, maps only that generated bundle read-only, and launches the sandbox interactively. Launch is not reported as proof that the command inside succeeded.

Hyper-V is not claimed as an implemented `sandbox_run` backend.

## Ecosystem inventory

The shared scanner covers project/user surfaces that are host-independent or already useful to Claude. The Codex adapter augments that result with Codex plugin/config presence and project `.agents/` state rather than putting Codex-specific filesystem rules into the shared Windows scanner.

`ecosystem-defrag` distinguishes specialists from actual orchestration overlap, preserves unknowns, requires a real restore path before cleanup, and allows a read-only "no change needed" outcome.

## Audit trail

The plugin persists only metadata its audit surfaces consume. It does **not** retain arbitrary command bodies, tool inputs, stdout/stderr, or unrelated tool responses.

Claude and Codex use separate hook adapters because their hook event/decision contracts differ, but both write the same minimal event shape where possible:

- session/tool identity;
- safety classification / permission denial metadata;
- completion metadata without raw payloads.

Session summaries are bound to the current session. Persistent-history queries are explicitly labeled as history.

## Verification

GitHub Actions runs on `windows-latest` and checks:

1. runtime compilation;
2. the pytest regression suite;
3. Claude MCP initialization and the exact 10-tool surface;
4. Codex MCP initialization and the same exact 10-tool surface.

The tests additionally discriminate:

- Claude vs Codex manifest/component wiring;
- root Git marketplace distribution;
- Codex-safe MCP namespace selection;
- per-tool Codex approval policy;
- required Codex project identity;
- deny-only Codex hook semantics;
- Codex-valid Stop-hook JSON;
- host-specific plugin-data paths;
- shared skill frontmatter and command-to-skill routing;
- audit provenance and MCP transport isolation.

For local development:

```text
python -m pip install -r requirements-dev.txt
python -m compileall -q src
python -m pytest tests -q
```

A green run supports only the behavior those checks execute. It does not prove a human accepted an interactive host permission dialog or that a command launched inside Windows Sandbox completed successfully.
