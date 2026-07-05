"""Pure DAG graph parser used by Studio previews."""

from __future__ import annotations

import ast
from typing import Any


def parse_dag_graph(source: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"tasks": [], "edges": [], "error": str(e)}

    tasks: list[dict[str, Any]] = []
    edges: list[list[str]] = []
    var_to_id: dict[str, str] = {}
    fn_to_id: dict[str, str] = {}
    output_vars: dict[str, str] = {}
    helper_fns: dict[str, int] = {}
    task_fn_names: set[str] = set()

    def is_task_deco(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "task"
        if isinstance(node, ast.Attribute):
            return node.attr == "task"
        if isinstance(node, ast.Call):
            return is_task_deco(node.func)
        return False

    def is_operator(name: str) -> bool:
        return any(
            name.endswith(s)
            for s in ("Operator", "Sensor", "Hook", "Branch", "Trigger", "Task")
        )

    def task_id_from_call(call_node: ast.Call) -> str | None:
        for kw in call_node.keywords:
            if kw.arg == "task_id" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return None

    def fn_name_of(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    # Pass 1: classify all function defs.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(is_task_deco(d) for d in node.decorator_list):
            tid = node.name
            task_fn_names.add(tid)
            fn_to_id[tid] = tid
            var_to_id[tid] = tid
            tasks.append({"id": tid, "op": "TaskFlow", "line": node.lineno, "calls": []})
        else:
            helper_fns[node.name] = node.lineno

    # Pass 2: classic Operators + output-var data deps.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        var = node.targets[0].id
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        fname = fn_name_of(call.func)
        if not fname:
            continue
        if is_operator(fname):
            tid = task_id_from_call(call) or var
            var_to_id[var] = tid
            tasks.append({"id": tid, "op": fname, "line": node.lineno, "calls": []})
        elif fname in fn_to_id:
            output_vars[var] = fn_to_id[fname]
            var_to_id[var] = fn_to_id[fname]

    # Pass 3: helper calls inside each @task body.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in task_fn_names:
            continue
        task = next((t for t in tasks if t["id"] == node.name), None)
        if not task:
            continue
        seen: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            cn = fn_name_of(child.func)
            if cn and cn in helper_fns and cn not in seen:
                seen.add(cn)
                task["calls"].append({"name": cn, "line": helper_fns[cn]})

    # Pass 4: >> chains + TaskFlow data deps.
    def resolve(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            t = var_to_id.get(node.id) or fn_to_id.get(node.id)
            return [t] if t else []
        if isinstance(node, ast.List):
            out: list[str] = []
            for e in node.elts:
                out.extend(resolve(e))
            return out
        if isinstance(node, ast.Call):
            fname = fn_name_of(node.func)
            if fname:
                tid = var_to_id.get(fname) or fn_to_id.get(fname)
                if tid:
                    for arg in node.args:
                        for src in resolve(arg):
                            if src and src != tid:
                                edges.append([src, tid])
                        if isinstance(arg, ast.Name):
                            src = output_vars.get(arg.id)
                            if src and src != tid:
                                edges.append([src, tid])
                    return [tid]
        return []

    def collect_chain(node: ast.AST) -> list[list[str]]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift):
            left = collect_chain(node.left)
            right = resolve(node.right)
            for f in left[-1] if left else []:
                for t in right:
                    if f != t:
                        edges.append([f, t])
            return left + [right]
        return [resolve(node)]

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.BinOp)
            and isinstance(node.value.op, ast.RShift)
        ):
            collect_chain(node.value)

    # Pass 4c: data deps from standalone task calls, not in >> chains.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = fn_name_of(node.func)
        if not fname:
            continue
        tid = fn_to_id.get(fname) or var_to_id.get(fname)
        if not tid:
            continue
        for arg in node.args:
            for src in resolve(arg):
                if src and src != tid:
                    edges.append([src, tid])
            if isinstance(arg, ast.Name):
                src = output_vars.get(arg.id)
                if src and src != tid:
                    edges.append([src, tid])

    seen_e: set[str] = set()
    dedup: list[list[str]] = []
    for e in edges:
        k = f"{e[0]}→{e[1]}"
        if k not in seen_e:
            seen_e.add(k)
            dedup.append(e)

    return {"tasks": tasks, "edges": dedup}
