---
name: workflow-plan
description: Build a Windows-aware execution plan when a task has consequential dependencies, multiple phases, uncertainty that can change the route, or mutation that needs explicit verification and rollback.
---

# Workflow Plan

Use planning when it changes execution quality. A trivial read or obvious single action does not need a multi-phase ceremony.

## Procedure

1. **Bind the task.** State the requested outcome, exact project/host boundary, hard constraints, and observable success condition.
2. **Inspect discriminating state.** Use `env_inspect`, `tool_discover`, or project-local reads only for facts that can change the route. Preserve unknown/failed probes instead of treating them as missing.
3. **Get the runtime scaffold.** Call `workflow_plan` with the task, relevant context, and the active project boundary. In Codex, pass the current session/project directory explicitly as `cwd`.
4. **Resolve the route.** Read `selection_status` before treating any candidate as chosen. Deterministic selection requires discriminating overlap with capability identity/tags; generic operation words or description-only similarity are not enough. `selected` means one strongest semantically distinguished route is executable. `ambiguous` means equal strongest task evidence remains; tool availability is an execution prerequisite, not a semantic tie-breaker. `matched_unavailable` means the uniquely strongest semantic route lacks an available configured tool; do not silently fall through to a weaker match. `no_match` means the scaffold lacks sufficient discriminating capability evidence and ordinary task reasoning must choose the path. When `route_discriminator` is present, use it to state the unresolved joint rather than inventing a selection.
5. **State execution phases.** For each consequential phase include the entry state actually required, concrete action/tool, safety class, observable exit condition, and rollback only when an explicit restore path exists. A scaffold whose route is unresolved is not executable merely because it contains an `execute` phase.
6. **Execute under the active host permission boundary.** The model constructs the concrete executing call; Claude Code or Codex then decides whether that same call is permitted. Do not add a second model-controlled confirmation field that claims the host already approved it.
7. **Verify before continuing.** A phase that changes upstream state invalidates downstream assumptions that depended on it; re-evaluate those dependencies rather than blindly replaying the original plan.

## Stop conditions

- Stop planning when further decomposition cannot change the next action or verification surface.
- If one missing fact selects between materially different routes, obtain that fact rather than expanding the plan.
- If execution is blocked, return the exact blocker and the smallest fact or authorization needed to continue.
- Do not call a task complete until the requested post-state is observed; preserve unresolved outcomes when the available surface cannot establish them.
