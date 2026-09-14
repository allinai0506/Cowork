# Unified StateStore & SQLite Single Source of Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish StateStore interface and SQLiteStateStore as the single source of truth for all Herdr state (workflows, tasks, steering, checkpoints), relegating JSON strictly to migration, export, and compatibility, completely eliminating dual-state skew.

**Architecture:** Define abstract `StateStore` interface in `herdr/state_store.py`. Implement `SQLiteStateStore` backed by `herdr/state_db.py`. Refactor `herdr/kernel.py` and `herdr/steering.py` to route all state reads and writes exclusively through `StateStore`. Migrate legacy JSON storage into SQLite with seamless compatibility fallbacks and export capabilities.

**Tech Stack:** Python 3 standard library (`sqlite3`, `pathlib`, `json`, `abc`, `typing`), `pytest`.

## Global Constraints

- Zero external dependencies (only Python 3 standard library: `sqlite3`, `abc`, `typing`, `json`, `pathlib`, `time`, `uuid`).
- Strict Single Source of Truth: SQLite is the sole runtime database.
- JSON files (`workflows.json`, `tasks.json`, `steering.json`) are strictly for migration import, snapshot export, and backward-compatible reading, NEVER primary write targets.
- No direct `open("tasks.json")` / `open("workflows.json")` / `open("steering.json")` in runtime logic (`kernel.py`, `steering.py`, etc.).
- All existing tests must remain green.

---

### Task 1: Extend `herdr/state_db.py` Schema and Functions for Unified State Operations

**Files:**
- Modify: `herdr/state_db.py`
- Test: `tests/test_state_db_v2.py`

**Interfaces:**
- Consumes: Existing SQLite connection and table management.
- Produces:
  - `get_task(task_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]`
  - `list_tasks(workflow_id: Optional[str] = None, status: Optional[str] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]`
  - `delete_task(task_id: str, db_path: Optional[Path] = None) -> bool`
  - `list_workflows(status: Optional[str] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]`
  - `delete_workflow(workflow_id: str, db_path: Optional[Path] = None) -> bool`
  - `save_steer(steer_dict: Dict[str, Any], db_path: Optional[Path] = None) -> None`
  - `get_steer(steer_id: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]`
  - `list_steers(task_id: Optional[str] = None, status: Optional[str] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]`
  - `update_steer_status(steer_id: str, status: str, dispatched_at: Optional[float] = None, db_path: Optional[Path] = None) -> bool`
  - `record_steering_history(record: Dict[str, Any], db_path: Optional[Path] = None) -> None`
  - `list_steering_history(task_id: Optional[str] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]`

- [ ] **Step 1: Write failing tests for new state_db capabilities**
Add test in `tests/test_state_db_v2.py` verifying `get_task`, `list_tasks`, `list_workflows`, steering CRUD, and steering history logging.

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_state_db_v2.py -k test_unified_state_db_extensions -v`
Expected: FAIL (functions not yet implemented)

- [ ] **Step 3: Implement schema extensions and query functions in `herdr/state_db.py`**
- In `_ensure_schema`: create tables `steering_items` and `steering_history` with corresponding indexes.
- In `save_task`: auto-create placeholder workflow record if `workflow_id` does not exist, avoiding foreign key failures.
- Implement `get_task`, `list_tasks`, `delete_task`, `list_workflows`, `delete_workflow`.
- Implement `save_steer`, `get_steer`, `list_steers`, `update_steer_status`, `record_steering_history`, `list_steering_history`.
- In `migrate_v1_to_v2`: add support for importing `steering.json`.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_state_db_v2.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit changes**
```bash
git add herdr/state_db.py tests/test_state_db_v2.py
git commit -m "feat(state_db): extend schema and query functions for unified state operations"
```

---

### Task 2: Define `StateStore` Interface and Implement `SQLiteStateStore` in `herdr/state_store.py`

**Files:**
- Create: `herdr/state_store.py`
- Modify: `herdr/__init__.py`
- Test: `tests/test_state_store.py`

**Interfaces:**
- Consumes: `herdr/state_db.py`
- Produces:
  - `class StateStore(ABC)`
  - `class SQLiteStateStore(StateStore)`
  - `get_state_store(db_path: Optional[Path] = None) -> StateStore`
  - `set_state_store(store: Optional[StateStore]) -> None`
  - `reset_state_store() -> None`

- [ ] **Step 1: Write the failing unit tests for StateStore & SQLiteStateStore**
Create `tests/test_state_store.py` covering:
- StateStore interface polymorphism
- SQLiteStateStore CRUD on workflows, tasks, steering, checkpoints
- Seamless JSON import and export
- Singleton lifecycle and environment resolution

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_state_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'herdr.state_store'`

- [ ] **Step 3: Create `herdr/state_store.py` and implement `StateStore` & `SQLiteStateStore`**
- Define `StateStore` abstract class with all required abstract methods.
- Implement `SQLiteStateStore` wrapping `state_db.py`.
- Implement `export_workflows_json()`, `export_tasks_json()`, `export_steering_json()`, `export_all_json()`.
- Implement `import_from_json()` delegating to `state_db.migrate_v1_to_v2`.
- Export `StateStore`, `SQLiteStateStore`, `get_state_store` in `herdr/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_state_store.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit changes**
```bash
git add herdr/state_store.py herdr/__init__.py tests/test_state_store.py
git commit -m "feat(state_store): introduce StateStore interface and SQLiteStateStore"
```

---

### Task 3: Refactor `herdr/kernel.py` to Route Through `StateStore`

**Files:**
- Modify: `herdr/kernel.py`
- Test: `tests/test_kernel_control_primitives.py`
- Test: `tests/test_console_kernel_api.py`

**Interfaces:**
- Consumes: `herdr/state_store.py` (`get_state_store()`)
- Produces:
  - Kernel control primitives (`pause_workflow`, `resume_workflow`, `force_pass_gate`, `rollback_workflow`, `step_workflow`, `create_checkpoint`, `restore_checkpoint`, `fork_workflow_from_checkpoint`) running against `StateStore`.
  - `load_workflows_data()`, `save_workflows_data()`, `load_tasks_data()`, `save_tasks_data()` converted to backward-compatibility adapters operating on `StateStore`.

- [ ] **Step 1: Run existing kernel tests before refactoring**
Run: `pytest tests/test_kernel_control_primitives.py tests/test_console_kernel_api.py -v`
Expected: ALL PASS

- [ ] **Step 2: Refactor `herdr/kernel.py`**
- Replace internal state operations in `pause_workflow`, `resume_workflow`, `force_pass_gate`, `rollback_workflow`, `step_workflow`, `create_checkpoint`, `restore_checkpoint`, `fork_workflow_from_checkpoint` with calls to `get_state_store()`.
- Refactor `load_workflows_data()` and `load_tasks_data()` to query `StateStore` (with automatic legacy JSON import fallback if DB is empty).
- Refactor `save_workflows_data()` and `save_tasks_data()` to write to `StateStore`.
- Remove direct raw file writes to `workflows.json` and `tasks.json` as primary state storage.

- [ ] **Step 3: Run kernel tests to verify compatibility and correctness**
Run: `pytest tests/test_kernel_control_primitives.py tests/test_console_kernel_api.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit changes**
```bash
git add herdr/kernel.py
git commit -m "refactor(kernel): route all state mutations through StateStore single source of truth"
```

---

### Task 4: Refactor `herdr/steering.py` to Route Through `StateStore`

**Files:**
- Modify: `herdr/steering.py`
- Test: `tests/test_steering_mesh.py`
- Test: `tests/test_console_steering_api.py`

**Interfaces:**
- Consumes: `herdr/state_store.py` (`get_state_store()`)
- Produces:
  - Steering primitives (`queue_steer`, `dispatch_steer_now`, `dispatch_pending_steer`, `halt_task`, `list_task_steers`) running against `StateStore`.
  - `load_tasks_data()`, `save_tasks_data()`, `load_steering_data()`, `save_steering_data()` converted to backward-compatibility adapters operating on `StateStore`.

- [ ] **Step 1: Run existing steering tests before refactoring**
Run: `pytest tests/test_steering_mesh.py tests/test_console_steering_api.py -v`
Expected: ALL PASS

- [ ] **Step 2: Refactor `herdr/steering.py`**
- Replace internal state operations in `queue_steer`, `dispatch_steer_now`, `dispatch_pending_steer`, `halt_task`, `list_task_steers` with calls to `get_state_store()`.
- Store steering queues and steering history in `StateStore` rather than raw `open("steering.json")`.
- Store task updates via `store.save_task(task)` rather than raw `open("tasks.json")`.
- Refactor `load_steering_data()` and `save_steering_data()` to adapt to `StateStore`.

- [ ] **Step 3: Run steering tests to verify compatibility and correctness**
Run: `pytest tests/test_steering_mesh.py tests/test_console_steering_api.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit changes**
```bash
git add herdr/steering.py
git commit -m "refactor(steering): unify steering queue and task intervention via StateStore"
```

---

### Task 5: End-to-End Consistency, Anti-Skew Verification, and Full Regression Check

**Files:**
- Modify: `tests/test_state_store.py`
- Test: All tests in `tests/`

**Interfaces:**
- Consumes: Unified `StateStore`, `kernel`, `steering`, `state_db`
- Produces: Zero dual-state skew guarantee test, 100% passing test suite.

- [ ] **Step 1: Add anti-skew test in `tests/test_state_store.py`**
Verify cross-module mutations:
- Call `kernel.pause_workflow` -> inspect `store.get_workflow` directly -> state matches immediately.
- Call `steering.queue_steer` and `dispatch_steer_now` -> inspect `store.list_steers` and `store.get_task` -> state matches immediately in SQLite.
- Confirm no discrepancy between SQLite and read adapters.

- [ ] **Step 2: Run all core state & control tests**
Run: `pytest tests/test_state_db_v2.py tests/test_state_store.py tests/test_kernel_control_primitives.py tests/test_steering_mesh.py tests/test_console_kernel_api.py tests/test_console_steering_api.py -v`
Expected: ALL PASS

- [ ] **Step 3: Run full repository regression test suite**
Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit final changes**
```bash
git add tests/test_state_store.py
git commit -m "test(state_store): add cross-module single-source-of-truth verification"
```
