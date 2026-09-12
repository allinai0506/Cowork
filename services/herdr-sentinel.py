#!/opt/homebrew/bin/python3

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

HOME = Path.home()
ROOT = HOME / ".herdr-controller"
TASKS_FILE = ROOT / "tasks.json"
STATE_FILE = ROOT / "sentinel-state.json"
CONTROLLER_SERVICE = f"gui/{os.getuid()}/com.user.herdr-controller"

ACTIVE = {"dispatched", "working", "blocked", "rework"}
CRASH_PATTERNS = (
    "Bun has crashed",
    "segmentation fault",
    "panic(main thread)",
)

POLL_SECONDS = 3
NUDGE_AFTER_SECONDS = 15


def run(cmd, timeout=10):
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def pane_visible(pane_id):
    try:
        r = run(
            ["herdr", "pane", "read", pane_id, "--source", "visible"],
            timeout=10,
        )
        return (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception:
        return ""


def agent_status(pane_id):
    try:
        r = run(["herdr", "agent", "get", pane_id], timeout=10)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        return data["result"]["agent"].get("agent_status")
    except Exception:
        return None


def nudge_enter(pane_id):
    try:
        run(["herdr", "pane", "send-keys", pane_id, "enter"], timeout=10)
        return True
    except Exception:
        return False


def update_statuses(changes):
    if not changes:
        return False

    data = load_json(TASKS_FILE, {"tasks": []})
    changed = False

    for task in data.get("tasks", []):
        task_id = task.get("task_id")
        if task_id not in changes:
            continue

        new_status, reason = changes[task_id]
        old_status = task.get("status")

        if old_status not in ACTIVE:
            continue

        task["status"] = new_status
        task["sentinel_reason"] = reason
        task["sentinel_updated_at"] = int(time.time())
        changed = True

        print(
            f"[SENTINEL STATE] {task_id}: "
            f"{old_status} -> {new_status} ({reason})",
            flush=True,
        )

    if changed:
        save_json_atomic(TASKS_FILE, data)

    return changed


def restart_controller():
    try:
        run(
            [
                "launchctl",
                "kickstart",
                "-k",
                CONTROLLER_SERVICE,
            ],
            timeout=20,
        )
        print("[SENTINEL] Controller restarted for recovery", flush=True)
    except Exception as e:
        print(f"[SENTINEL ERROR] Controller restart: {e}", flush=True)


def main():
    state = load_json(STATE_FILE, {"seen": {}, "nudged": {}})

    print("[HERDR SENTINEL] starting", flush=True)

    while True:
        registry = load_json(TASKS_FILE, {"tasks": []})
        now = time.time()
        changes = {}

        for task in registry.get("tasks", []):
            status = task.get("status")
            if status not in ACTIVE:
                continue

            task_id = task.get("task_id")
            pane_id = task.get("pane_id")

            if not task_id or not pane_id:
                continue

            state["seen"].setdefault(task_id, now)

            screen = pane_visible(pane_id)
            done_marker = f"HERDR_TASK_DONE:{task_id}"
            orchestration_marker = f"HERDR_ORCH_TASK:{task_id}"

            if done_marker in screen:
                changes[task_id] = (
                    "agent_done",
                    "completion_sentinel",
                )
                continue

            if any(pattern in screen for pattern in CRASH_PATTERNS):
                changes[task_id] = (
                    "failed",
                    "agent_process_crash",
                )
                continue

            age = now - state["seen"][task_id]
            if (
                status == "dispatched"
                and age >= NUDGE_AFTER_SECONDS
                and task_id not in state["nudged"]
                and orchestration_marker in screen
                and agent_status(pane_id) in {"idle", "unknown", None}
            ):
                if nudge_enter(pane_id):
                    state["nudged"][task_id] = now
                    print(
                        f"[SENTINEL NUDGE] task={task_id} pane={pane_id}",
                        flush=True,
                    )

        if update_statuses(changes):
            save_json_atomic(STATE_FILE, state)
            restart_controller()
            time.sleep(5)
        else:
            save_json_atomic(STATE_FILE, state)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
