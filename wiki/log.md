# Wiki Evolution Log (log.md)

> 本文件为 Herdr 知识层的 Append-Only 演进记录。  
> 仅记录 Wiki 结构与知识库发生实质性变更的原因与概要，不记录细碎的代码提交流水。

---

## [2026-09-12] init | Initial repository analysis & Wiki creation
Created initial LLM Wiki directly derived from active repository code inspection and behavioral evidence.
- Established Wiki Governance Rules in [[WIKI]].
- Designed master navigation and domain routing index in [[index]].
- Captured high-level architecture, problem boundaries, and core topology in [[system-overview]] and [[architecture]].
- Formulated core business domain entities and persistent storage contracts in [[domain-model]].
- Captured the critical Tab = Node spatial paradigm, Anchor Pane split mechanism, and runtime dynamic self-healing engine in [[tab-node-model]].
- Codified the full 11-state task lifecycle, CoW Git clone isolation, and baseline fingerprint verification in [[task-lifecycle]].
- Documented DAG topology parsing, cycle detection via Kahn's algorithm, and node ready resolution in [[dag-workflow-engine]].
- Documented multi-agent load balancing, reservation locking, and policy-driven routing in [[agent-routing-and-pools]].
- Documented non-destructive lightweight and deep sandbox health probes in [[preflight-and-health]].
- Codified developer & agent guide for safely making frequent codebase modifications in [[common-change-paths]].

## [2026-09-12] add | Agent Operations Center knowledge
- Added [[ops-center]]: documented the four dashboard layers, runtime/task state distinction, duration buckets, trajectory output, and anomaly action contract.
- Updated [[index]]: indexed the new operations view for future code navigation.

## [2026-09-12] move | Console source into repository
- Added the canonical Console source under `console/` and a repeatable `scripts/install-herdr-console.sh` deployment path.
- Updated service operations documentation and [[ops-center]] to distinguish repository source from the LaunchAgent deployment copy.

## [2026-09-12] fix | Workflow deadlock permanent engineering fix

Root-cause analysis identified three compounding failure modes causing DAG advance to permanently stall:

1. **`failed` status as permanent blocker** — `is_node_complete` had no way to skip a task that was replaced by another attempt; a single `failed` task would prevent the entire node from ever completing.

2. **`stage-state.json` write-once latch** — once `notified` was written for a stage, `mark_stage_advance_queued` would refuse to re-queue it even after the predecessor node regressed (e.g., new `failed` tasks arrived). The Controller would never re-trigger the advance.

3. **Head-of-Line blocking in `coordinator_worker`** — a single thread handled all workflows sequentially. One coordinator blocked on a busy agent would stall all other pending workflow advances indefinitely.

### Changes made

- **`bin/herdr-task`**:
  - `TRANSITIONS`: added `superseded` as a valid exit from `failed`, `cleaned`, and all in-progress states (`dispatched`, `working`, `blocked`, `agent_done`, `rework`). `superseded` is a terminal state.
  - `node_status` / `is_node_complete`: active tasks are now computed excluding `superseded` ones. A node with only superseded tasks and no active replacements is `incomplete`.
  - New `supersede_task()` function: marks a task `superseded`, optionally linking `superseded_by` and `supersede_reason`.
  - New `supersede` CLI subcommand: `herdr-task supersede <task_id> [--by <new_id>] [--reason ...]`
  - New `--supersedes` flag on `launch`: atomically supersedes the old task before launching the replacement in a single command.
  - New `stage-reset` subcommand: clears `stage-state.json` advance locks for a workflow (optionally scoped to a single stage).
  - New `advance` subcommand: calls `stage-reset` then prints a confirmation that the Controller will re-evaluate within ~2 s.

- **`services/herdr-controller.py`**:
  - `is_node_complete`: mirrors the `bin/herdr-task` logic — excludes `superseded` / `superseded_by` tasks.
  - `reconcile_stage_advance_states()`: called at the start of every `check_workflow_stage_advance` cycle. Scans `stage-state.json` for `notified` entries whose predecessor nodes are no longer complete, and revokes them so the advance can be re-triggered on the next cycle (~2 s).
  - `coordinator_worker` refactored to a lightweight dispatcher using `ThreadPoolExecutor(max_workers=16)` with per-workflow serialization locks (`_workflow_dispatch_lock`). Each workflow's blocking prompt call runs in its own executor thread, eliminating HoL blocking across workflows.

- **`tests/test_stage_advance_and_supersede.py`**: 17 new regression tests covering all five engineering changes. Full suite: 33 passed, 0 regressions.
