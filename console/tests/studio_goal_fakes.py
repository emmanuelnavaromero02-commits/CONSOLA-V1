from __future__ import annotations

import json
import uuid
from copy import deepcopy
from typing import Any


class FakeStudioGoalPool:
    def __init__(self):
        self.runs: dict[str, dict[str, Any]] = {}
        self.steps: dict[str, list[dict[str, Any]]] = {}
        self.next_step_id = 1
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        if "INSERT INTO studio_goal_runs" in query:
            run_id = str(uuid.uuid4())
            row = {
                "id": uuid.UUID(run_id),
                "user_id": args[0],
                "cartridge_id": args[1],
                "intent": args[2],
                "plan": [],
                "status": "planning",
                "current_step": 0,
                "result": {},
                "error": None,
                "created_at": None,
                "updated_at": None,
                "finished_at": None,
            }
            self.runs[run_id] = row
            self.steps[run_id] = []
            return deepcopy(row)

        if "FROM studio_goal_runs" in query and "WHERE id = $1 AND user_id = $2" in query:
            run = self.runs.get(str(args[0]))
            if run and int(run["user_id"]) == int(args[1]):
                return deepcopy(run)
            return None

        if "UPDATE studio_goal_runs" in query and "SET plan" in query:
            run = self.runs[str(args[0])]
            run["plan"] = json.loads(args[1])
            run["status"] = "running"
            return deepcopy(run)

        if "UPDATE studio_goal_steps" in query:
            run_id = str(args[0])
            step_id = int(args[1])
            step = self._step_by_id(run_id, step_id)
            if step is None:
                return None
            if "SET status = 'pending'" in query and "status = 'waiting_approval'" in query:
                if step["status"] != "waiting_approval":
                    return None
                if "approval_key = $4" in query and step.get("approval_key") != args[3]:
                    return None
                step["status"] = "pending"
                step["result"] = json.loads(args[2])
                step["finished_at"] = None
                return deepcopy(step)
            if "SET status = 'skipped'" in query:
                if step["status"] != "waiting_approval":
                    return None
                if "approval_key = $4" in query and step.get("approval_key") != args[3]:
                    return None
                step["status"] = "skipped"
                step["result"] = json.loads(args[2])
                return deepcopy(step)
            if "SET status = 'waiting_approval'" in query:
                if step["status"] != "pending":
                    return None
                if self._has_previous_unfinished(run_id, step["step_idx"]):
                    return None
                step["status"] = "waiting_approval"
                step["result"] = json.loads(args[2])
                step["approval_key"] = args[3]
                return deepcopy(step)
            if "SET status = 'running'" in query:
                if step["status"] != "pending":
                    return None
                if self._has_previous_unfinished(run_id, step["step_idx"]):
                    return None
                step["status"] = "running"
                return deepcopy(step)
            if "SET status = 'failed'" in query:
                step["status"] = "failed"
                step["result"] = json.loads(args[2])
                return deepcopy(step)
            if "SET status = 'completed'" in query:
                step["status"] = "completed"
                step["result"] = json.loads(args[2])
                return deepcopy(step)

        return None

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        if "FROM studio_goal_steps" in query:
            return deepcopy(sorted(self.steps.get(str(args[0]), []), key=lambda row: row["step_idx"]))
        return []

    async def execute(self, query: str, *args):
        self.execute_calls.append((query, args))
        if "INSERT INTO studio_goal_steps" in query:
            run_id = str(args[0])
            step = {
                "id": self.next_step_id,
                "goal_run_id": uuid.UUID(run_id),
                "step_idx": args[1],
                "step_key": args[2],
                "title": args[3],
                "description": args[4],
                "tool": args[5],
                "args": json.loads(args[6]),
                "risk_level": args[7],
                "status": "pending",
                "result": {},
                "approval_key": None,
                "started_at": None,
                "finished_at": None,
            }
            self.next_step_id += 1
            if not any(existing["step_idx"] == step["step_idx"] for existing in self.steps[run_id]):
                self.steps[run_id].append(step)
            return "INSERT 0 1"
        if "UPDATE studio_goal_runs" in query:
            run = self.runs.get(str(args[0]))
            if not run:
                return "UPDATE 0"
            if "status = 'waiting_approval'" in query:
                run["status"] = "waiting_approval"
                run["current_step"] = args[1]
            elif "status = 'running'" in query:
                run["status"] = "running"
                if len(args) > 1:
                    run["current_step"] = args[1]
            elif "status = 'completed'" in query:
                run["status"] = "completed"
                run["result"] = json.loads(args[1])
            elif "status = 'failed'" in query:
                run["status"] = "failed"
                run["error"] = args[1]
            return "UPDATE 1"
        return "OK"

    def _step_by_id(self, run_id: str, step_id: int) -> dict[str, Any] | None:
        for step in self.steps.get(run_id, []):
            if int(step["id"]) == step_id:
                return step
        return None

    def _has_previous_unfinished(self, run_id: str, step_idx: int) -> bool:
        return any(
            step["step_idx"] < step_idx and step["status"] not in {"completed", "skipped"}
            for step in self.steps.get(run_id, [])
        )


def pool_factory(pool: FakeStudioGoalPool):
    async def fake_pool():
        return pool

    return fake_pool
