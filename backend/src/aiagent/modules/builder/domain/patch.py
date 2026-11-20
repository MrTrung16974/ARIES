"""JSON Patch (tập con RFC 6902: add/replace/remove) và diff AgentSpec dễ đọc.

Mọi thay đổi của Architect đi qua đây: chỉ được đụng các path trong `scope` đã khai báo và không
được đụng các path cấm (approval, schema_version...).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from aiagent.modules.builder.domain.models import PatchOp

FORBIDDEN_PREFIXES = ("/schema_version", "/approval", "/graph_type", "/knowledge_bases")


class PatchError(ValueError):
    pass


def _tokens(path: str) -> list[str]:
    if not path.startswith("/"):
        raise PatchError(f"Path không hợp lệ: {path}")
    return [t.replace("~1", "/").replace("~0", "~") for t in path[1:].split("/")]


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def apply_patch(doc: dict[str, Any], ops: list[PatchOp], scope: list[str]) -> dict[str, Any]:
    result = copy.deepcopy(doc)
    for op in ops:
        if any(_within(op.path, f) for f in FORBIDDEN_PREFIXES):
            raise PatchError(f"Không được sửa {op.path}")
        if not any(_within(op.path, s) for s in scope):
            raise PatchError(f"{op.path} nằm ngoài phạm vi đã khai báo {scope}")
        tokens = _tokens(op.path)
        parent: Any = result
        for t in tokens[:-1]:
            parent = parent[int(t)] if isinstance(parent, list) else parent.get(t)
            if parent is None:
                raise PatchError(f"Path không tồn tại: {op.path}")
        last = tokens[-1]
        if isinstance(parent, list):
            if op.op == "add":
                if last == "-":
                    parent.append(op.value)
                else:
                    parent.insert(int(last), op.value)
            elif op.op == "replace":
                parent[int(last)] = op.value
            else:
                del parent[int(last)]
        elif isinstance(parent, dict):
            if op.op == "remove":
                if last not in parent:
                    raise PatchError(f"Không có khóa để xóa: {op.path}")
                del parent[last]
            else:
                if op.op == "replace" and last not in parent:
                    raise PatchError(f"Không có khóa để thay: {op.path}")
                parent[last] = op.value
        else:
            raise PatchError(f"Không áp dụng được {op.path}")
    return result


@dataclass(frozen=True)
class SpecChange:
    path: str
    before: Any
    after: Any

    def human(self) -> str:
        def show(v: Any) -> str:
            text = json.dumps(v, ensure_ascii=False, default=str)
            return text if len(text) <= 120 else text[:117] + "…"

        if self.before is None:
            return f"+ {self.path}: {show(self.after)}"
        if self.after is None:
            return f"− {self.path}: {show(self.before)}"
        return f"~ {self.path}: {show(self.before)} → {show(self.after)}"


def diff_specs(before: dict[str, Any] | None, after: dict[str, Any]) -> list[SpecChange]:
    """Diff theo trường lá; danh sách (rules, skills...) so sánh nguyên khối cho dễ đọc."""
    changes: list[SpecChange] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                walk(a.get(key), b.get(key), f"{path}/{key}")
            return
        if a != b:
            changes.append(SpecChange(path or "/", a, b))

    walk(before or {}, after, "")
    return changes
