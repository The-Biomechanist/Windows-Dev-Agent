# Windows Dev Agent

Windows-native developer orchestration for **Claude Code and Codex**, built around one shared runtime and six shared procedural skills.

Version **0.4.0** tightens the package around observable state and host-owned authority: environment discovery preserves unknown probe state, executing MCP calls use one real host permission boundary, Windows Sandbox stages the selected workload into the isolated VM, audit summaries keep unknown outcomes explicit, and user-level inventory is separated from project-local reads.

## Architecture

```text
shared core
  skills/
  capabilities.yaml
  src/mcp/server.py
  src/capabilities.py
  src/discovery/
  src/models/environment.py
  src/observability/
  src/safety/gate.py
        │
        ├── Claude Code adapter
        │     .claude-plugin/plugin.json
        │     .mcp.json
        │     src/claude_server.py
        │     agents/  commands/  hooks/hooks.json
        │
        └── Codex adapter
              .codex-plugin/plugin.json
              .mcp.codex.json
              src/codex_server.py
              hooks/codex-hooks.json
              src/safety/codex_*.py
              src/observability/codex_*.py
```

Host adapters own only packaging, path binding, permission integration, and host hook contracts. Windows behavior and reusable procedures stay shared.

## Shared skills

| Skill | Purpose |
| --- | --- |
| `env-inspect` | Inspect Windows host, runtime, toolchain, package-manager, and isolation state without collapsing unknown probes into absence. |
| `workflow-plan` | Build a bounded Windows-aware plan when dependencies or uncertainty can change the route. |
| `package-install` | Resolve exact package identity, review the concrete mutation, execute under host permission, and verify fresh state. |
| `sandbox-run` | Select WSL, a project Dev Container, or Windows Sandbox from the required execution property. |
| `win-setup` | Repair missing or broken Windows development prerequisites with the smallest native change. |
| `ecosystem-defrag` | Inventory concrete agent/tool overlap and plan or execute a reversible consolidation. |

Claude's `/windows-dev-agent:env`, `/windows-dev-agent:plan`, and `/windows-dev-agent:defrag` commands are thin adapters to those canonical skills. Codex consumes the same skills directly.

## Install surfaces

### Claude Code

From a checkout:

```text
claude --plugin-dir .
```

`.mcp.json` keeps three identities separate:

- `${CLAUDE_PLUGIN_ROOT}` — immutable plugin code/config;
- `${CLAUDE_PLUGIN_DATA}` — persistent cache/audit data;
- `${CLAUDE_PROJECT_DIR}` — active project boundary.

### Codex / ChatGPT desktop

Canonical `main` carries `.codex-plugin/plugin.json` plus the release index at `.agents/plugins/marketplace.json`. Each published marketplace entry is pinned to an immutable Git commit rather than treating a moving `main` branch as the identity of a fixed version. The immutable plugin payload itself does not depend on carrying its own marketplace index.

Codex installs plugin code into its cache, so project-scoped MCP tools require the current project directory explicitly. Runtime cache/audit state converges on `${CODEX_HOME:-~/.codex}/plugins/data/windows-dev-agent` rather than the installed plugin tree.

Bundled Codex hooks are an **optional trusted layer**. Codex does not automatically trust newly installed/changed plugin hooks; until the user reviews and trusts them, native Codex MCP/shell approval policy remains the operative boundary. Mutation-capable WDA MCP tools remain `prompt`-gated regardless.

## MCP surface

Both adapters expose the same ten tools:

| Tool | Purpose |
| --- | --- |
| `env_inspect` | Native Windows environment snapshot with time-bound cache and `force_refresh`. |
| `tool_discover` | Focused executable/version discovery. |
| `capability_run` | Plan or execute one registered argv-based capability. |
| `workflow_plan` | Deterministic capability-aware execution scaffold. |
| `package_search` | Resolve package identity before mutation. |
| `package_install` | Plan or execute one exact WinGet/Chocolatey/Scoop install. |
| `sandbox_run` | Route and optionally launch WSL, Dev Container, or Windows Sandbox execution. |
| `ecosystem_scan` | Project-local agent/tool inventory by default; optional broader host inventory. |
| `logs_query` | Query bounded persistent WDA audit metadata. |
| `mcp_audit` | Inspect project MCP configuration by default; broader reads are explicit. |

## Authority model

Windows Dev Agent does **not** carry a model-controlled `user_approved` token.

The sequence is deliberately simple:

```text
model constructs exact tool call
→ active host applies its permission policy to that call
→ if permitted, the same call reaches the MCP runtime
```

Plan-only calls use `execute: false`. Mutation calls use `execute: true`. The shared runtime independently blocks capabilities classified `forbidden`; it does not pretend to prove that the host prompt occurred.

### Claude Code

The Claude `PreToolUse` hook is tightening-only:

- read-only / reversible → no WDA decision; Claude's normal policy remains authoritative;
- approval-required / checkpoint → `ask`;
- forbidden → `deny`;
- it never returns `allow`.

Project-local ecosystem/MCP inspection is classified separately from broader host reads, but Claude Code's own normal permission flow remains authoritative for both.

### Codex

Codex uses native MCP approval modes. Always-bounded non-filesystem reads can be `approve`; mutation-capable and filesystem-inventory tools remain `prompt`.

When Codex plugin hooks are trusted, `PermissionRequest` removes needless prompts only for the three plan-first MCP tools when `execute: false`. Executing calls receive no WDA allow decision and continue to normal Codex approval.

`ecosystem_scan` and `mcp_audit` remain on native Codex approval even for project-only requests. Their caller-supplied `cwd` is required, but the plugin cannot independently prove that an arbitrary supplied directory is the active Codex project, so it does not auto-approve those reads.

`PreToolUse` may deny a known-forbidden action but never emulates Claude's `ask` result.

## Environment evidence

Availability fields are tri-state:

- `true` — observed present/enabled;
- `false` — observed absent/disabled;
- `null` — the probe did not establish the fact.

The shipped PowerShell producer uses live-image optional-feature queries (`Get-WindowsOptionalFeature -Online`). Probe failures are recorded in `snapshot.errors` and make the snapshot degraded rather than silently becoming `false`. Windows Sandbox is queried using its canonical `Containers-DisposableClientVM` optional-feature identity.

The cache stores the same canonical representation returned to consumers; it is not a second serialization format. Cache TTL is five minutes, and package-install execution invalidates the cached snapshot even on a failed installer because partial mutation is possible.

Discovery intentionally does **not** persist username/domain, Git user identity, or full PowerShell module inventory. Those values are not required by the current routing consumers.

## Package setup

`package_search` is the read-only package-identity producer. A package mutation should use an exact ID from the user/authoritative project state or resolve one through search before `package_install`.

`package_install(execute:false)` returns the concrete argv for review. `execute:true` requests that exact mutation under the active host permission policy. Installer exit is not treated as proof that the requested task now works; verify executable/version/task state afterward.

## Isolation

`environment:auto` requires an `isolation_requirement`:

- `linux_compatibility` → WSL;
- `project_reproducibility` → configured project Dev Container;
- `untrusted_windows` → Windows Sandbox.

WSL enters the active project using `wsl --cd <project>` and uses `sh -lc` by default. Dev Container execution uses the project configuration and `sh -lc`.

### Windows Sandbox payloads

A hostile Windows workload is not isolated merely because a Sandbox window launches. For `untrusted_windows`, supply workspace-relative `payload_paths` identifying the files/directories the inner command actually needs. The runtime:

1. rejects absolute paths, `..` escapes, missing paths, symbolic-link escapes, and trees over 10,000 filesystem entries;
2. stages the selected payload into a temporary bundle;
3. maps only that generated bundle into Windows Sandbox, read-only;
4. disables Sandbox networking and clipboard;
5. runs the inner command from `C:\WDAShare\payload`.

Planning does not materialize the bundle. An executing Sandbox call returns `launched` plus `cleanup_path`; that establishes launch only. Inner command success remains unknown until observed from inside the Sandbox.

## Ecosystem and MCP reads

`ecosystem_scan` starts project-local. Set `include_host:true` only when user-level extensions/plugins/MCP state can change the decision. `include_packages:true` is legal only with host inventory enabled.

`mcp_audit` likewise starts from the project boundary. User-level MCP configuration and arbitrary `config_path` reads are explicit broader requests.

Returned MCP summaries omit environment values and do not expose secrets from the inspected config. On Codex, all filesystem inventory reads remain on native approval because caller-supplied project identity cannot be independently authenticated by the plugin.

## Audit state and retention

WDA persists only metadata its audit consumers need; raw commands, MCP arguments, stdout/stderr, and arbitrary tool responses are not retained.

For execution-capable calls, the audit representation distinguishes:

- `succeeded` — result establishes successful execution;
- `failed` — result establishes failed execution;
- `unknown` — execution/launch occurred but the available observation does not establish outcome;
- `not_executed` — plan/block/unavailable/invalid input prevented execution;
- `not_applicable` — the lifecycle event has no execution outcome to classify, including permission/control events.

Codex PostToolUse may inspect the WDA MCP result **in memory** to derive that small status, then discards the raw response. A Windows Sandbox launch therefore remains `unknown`, never “zero failures.”

`agent.log` is bounded to 2 MiB and one rotated predecessor (`agent.log.1`). Environment cache and audit state live outside the immutable plugin code tree.

## Capability catalog

`capabilities.yaml` contains only fields with live consumers: description, safety, tags, and argv tools. Configured commands execute with `shell=False` and `DEVNULL` stdin. Caller-supplied `extra_args` upgrade effective authority to approval-required rather than inheriting the base capability class.

The current catalog covers Git inspection, Python/JavaScript linting, Python/.NET tests, .NET build, and GitHub PR creation.

## Verification

GitHub Actions runs on `windows-latest` across Python **3.9** and **3.13** and checks:

1. Python runtime compilation;
2. the contract-focused pytest suite;
3. the **actual shipped PowerShell discovery producer** on the Windows runner;
4. exact MCP initialization/version/tool surfaces for Claude and Codex;
5. when the canonical release index is present, that its immutable SHA resolves to a Codex plugin manifest with the same published version.

The suite covers the one-call authority sequence, tri-state discovery/cache roundtrip, host/project read boundaries, argument-dependent safety, package freshness, Sandbox payload staging and path containment, WSL project binding, audit outcome uncertainty/retention, host adapter wiring, and MCP transport isolation.

For local development:

```text
python -m pip install -r requirements-dev.txt
python -m compileall -q src
python -m pytest tests -q
```

A green run supports only the contracts those checks actually exercise. It does not establish that a human accepted a real Claude/Codex permission dialog, nor that a command launched inside an interactive Windows Sandbox completed successfully on an end-user desktop.

## Requirements

- Windows 10/11 for full native behavior;
- Windows PowerShell 5.1+;
- Python 3.9+;
- current Claude Code for the Claude adapter;
- current Codex / ChatGPT desktop plugin runtime for the Codex adapter.

Runtime dependencies are Python standard-library only. Development tests use pytest.
