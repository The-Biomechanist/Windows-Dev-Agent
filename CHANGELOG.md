# Changelog

All notable Windows Dev Agent changes are recorded here. The repository uses semantic versioning for public plugin/runtime releases.

## 0.5.0 — Unreleased

Production-hardening and public-release cleanup.

### Runtime and bootstrap

- Raise the supported Python floor to 3.11.
- Add an isolated Windows-native Python launcher that avoids project/PATH executable shadowing, supports an explicit absolute host override, and runs correctly under Windows PowerShell 5.1.
- Root Codex hook bootstrap PowerShell at `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` so hooks executed from the active project directory cannot resolve a project-local `powershell.exe`; preserve the quote-free command form required by Codex's current Windows hook transport.
- Remove ambient/project-current-directory import dependence: Claude launches from the plugin root explicitly, while Codex uses its plugin-owned startup directory only to resolve the bundled launcher and never as project identity.
- Consolidate Claude and Codex onto one bounded stdio transport.
- Add strict runtime MCP argument validation and request/resource bounds.
- Remove the directly executable host-neutral MCP core path.
- Consolidate captured external execution into a bounded streaming runner.
- Bind plan-first capability/package/Sandbox execution to the reviewed executable path **and typed identity fingerprint**. Regular files use SHA-256 of the exact opened file; Windows App Execution Aliases use SHA-256 of the alias reparse data. Execution re-establishes that identity and holds the verified object stable through process creation; a changed identity returns `stale_plan` before execution, staging, or mutation and does not become a second approval token.
- Self-seal non-plan subprocesses at use time too: diagnostic/search/inventory launches snapshot the current typed executable identity and hold that exact object through process creation, closing the absolute-path resolve-to-spawn replacement window without adding plan ceremony.
- Resolve runtime-owned Windows control-plane binaries such as discovery PowerShell, WSL, and Windows Sandbox from trusted Windows locations rather than PATH; Claude also binds its bootstrap PowerShell to the Windows system installation.

### Isolation

- Require an explicit semantic isolation requirement for every `sandbox_run` call.
- Reject explicit backend/requirement combinations that do not provide the requested property.
- Require an actual `.devcontainer/devcontainer.json` or root `.devcontainer.json` before treating a project as Dev Container-configured.
- Require payload staging for every `untrusted_windows` request.
- Disable vGPU, networking, audio input, video input, printer redirection, and clipboard redirection for the hostile-Windows route.
- Keep generated `.wsb` configuration outside the mapped read-only Sandbox share.
- Make WDA own Sandbox bundle cleanup responsibility with best-effort process-exit cleanup plus stale-bundle collection at host startup and before later Sandbox launches.

### State, security, and observability

- Apply symlink/reparse containment to project-local configuration reads and Dev Container configuration detection, then consume project JSON from the same use-time verified handle rather than trusting a prior path check.
- Revalidate Windows Sandbox payload paths, entry/byte budgets, and opened file/directory identities while staging so a post-validation junction/symlink swap cannot redirect WDA-owned copies.
- Make discovery failures return the canonical snapshot shape.
- Add bounded atomic discovery cache writes, a Windows interprocess cache lock, and mutation-generation protection against stale cache resurrection.
- Fail package-install execution closed when the cache mutation/invalidation transition cannot be established before launch.
- Serialize audit rotation/append between Windows hook processes.
- Make session/persistent audit summaries best-effort across retained log segments: a rotated predecessor that disappears or becomes unreadable after enumeration is skipped while remaining readable evidence is preserved, so Stop-time reporting does not become an execution blocker.
- Add audit schema version and explicit lifecycle fields while preserving legacy log readability.
- Classify rejected `stale_plan` requests as `not_executed`, distinct from runtime execution failure.
- Stop exposing the physical WDA data directory from `logs_query`.
- Separate host-neutral safety classification from Claude's hook adapter.

### Cleanup and release

- Rename `capabilities.yaml` to the format-accurate `capabilities.json`.
- Remove legacy command aliases, the duplicate Windows orchestrator agent, unused APM metadata, empty runtime requirements metadata, unused runtime-path helpers, and release-history naming residue from tests.
- Rewrite public documentation around installation, capabilities, boundaries, limitations, and development.
- Add `SECURITY.md`.
- Update Windows CI to test Python 3.11 and 3.14, exercise the Windows PowerShell 5.1 bootstrap, and verify immutable published release ancestry without freezing later development to the last payload.

## 0.4.3

Runtime authority repair release.

- Bound Claude project-scoped calls to the host-supplied project root.
- Required authoritative Codex session scope for trusted plan shortcuts.
- Preserved unknown external-process outcomes instead of collapsing them into failure.
- Kept discovery failure responses in the canonical degraded snapshot shape.
- Made WinGet installs noninteractive and source-bound.
- Published the Codex marketplace entry as an index-only commit pinned to an immutable index-free payload commit.

## 0.4.2

Runtime-integrity release that established the shared Claude/Codex architecture, retained-history audit model, executable-identity checks, and Windows NTFS/reparse containment foundations used by later hardening.
