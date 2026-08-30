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
4. **Resolve the route.** Choose the smallest coherent path that satisfies the task. If `selection_status` is `ambiguous`, preserve the tied top candidates and obtain the discriminator that can select among them. If it is `matched_unavailable`, preserve the semantic match but establish an executable tool or another supported route before execution. Do not convert a tie or an unavailable route into a false selection.
5. **State execution phases.** For each consequential phase include the entry state actually required, concrete action/tool, safety class, observable exit condition, and rollback only when an explicit restore path exists.
6. **Bind plan-first execution to fresh runtime state.** For `capability_run`, `package_install`, or `sandbox_run`, first obtain the concrete `execute: false` plan. Review the returned route, resolved executable, argv/project binding, and `plan_fingerprint`, then carry that fingerprint unchanged into the corresponding `execute: true` call. The fingerprint establishes only that the reviewed execution plan is still current; it does not establish permission.
7. **Execute under the active host permission boundary.** The model constructs the concrete executing call; Claude Code or Codex then decides whether that same call is permitted. Do not add a second model-controlled confirmation field that claims the host already approved it.
8. **Recover from stale plans at the changed dependency.** If a plan-first executor returns `stale_plan`, do not retry the unchanged call or silently substitute the newly available executable/backend. Obtain a fresh plan, review the changed binding, and reconsider every downstream step that depended on the invalidated plan while preserving unrelated state.
9. **Verify before continuing.** A phase that changes upstream state invalidates downstream assumptions that depended on it; re-evaluate those dependencies rather than blindly replaying the original plan.

## Stop conditions

- Stop planning when further decomposition cannot change the next action or verification surface.
- If one missing fact selects between materially different routes, obtain that fact rather than expanding the plan.
- If execution is blocked, return the exact blocker and the smallest fact or authorization needed to continue.
- Do not call a task complete until the requested post-state is observed; preserve unresolved outcomes when the available surface cannot establish them.
