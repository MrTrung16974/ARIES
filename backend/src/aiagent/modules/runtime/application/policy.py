"""Policy cho tool call (ADR-0016). Hàm thuần — dễ test, dễ thay bằng Cedar sau này.

| Điều kiện                                             | Quyết định                       |
|-------------------------------------------------------|----------------------------------|
| risk `read`                                           | cho phép                         |
| risk nằm trong `approval.require_for`                 | cần duyệt                        |
| risk `write` ngoài require_for, run đã nhiễm (taint)  | cần duyệt                        |
| risk `destructive`                                    | cần duyệt bởi Admin/Owner        |
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aiagent.modules.runtime.domain import Effect, ToolDecision
from aiagent.plugin_sdk import RiskLevel, ToolSpec

DESTRUCTIVE_APPROVERS: tuple[str, ...] = ("admin", "owner")


def decide(
    tool: ToolSpec,
    *,
    require_for: Iterable[RiskLevel],
    tainted: bool,
    arguments: dict[str, Any],
    connection_id: str | None = None,
) -> ToolDecision:
    needs = set(require_for)

    def make(effect: Effect, reason: str, roles: tuple[str, ...] = ()) -> ToolDecision:
        return ToolDecision(
            effect=effect,
            tool_name=tool.name,
            risk=tool.risk.value,
            reason=reason,
            approver_roles=roles,
            connection_id=connection_id,
            arguments=arguments,
        )

    if tool.risk is RiskLevel.READ:
        return make(Effect.ALLOW, "Chỉ đọc")
    if tool.risk is RiskLevel.DESTRUCTIVE:
        return make(
            Effect.APPROVE, "Thao tác phá hủy — cần Admin/Owner duyệt", DESTRUCTIVE_APPROVERS
        )
    if tool.risk in needs:
        label = "gửi dữ liệu ra ngoài" if tool.risk is RiskLevel.EXTERNAL_SEND else "ghi dữ liệu"
        reason = f"Thao tác {label}"
        if tainted:
            reason += " sau khi đã đọc nội dung bên ngoài"
        return make(Effect.APPROVE, reason + " — cần duyệt")
    if tainted:
        return make(Effect.APPROVE, "Ghi dữ liệu sau khi đã đọc nội dung bên ngoài — cần duyệt")
    return make(Effect.ALLOW, "Ghi dữ liệu (agent cho phép tự động)")


def can_decide(roles: Iterable[str], *, approver_roles: Iterable[str], is_requester: bool) -> bool:
    """Ai được duyệt: approver_roles rỗng → chính người yêu cầu (hoặc Admin/Owner);
    có approver_roles → phải có một trong các vai trò đó (Owner luôn được)."""
    mine = set(roles)
    required = set(approver_roles)
    if "owner" in mine:
        return True
    if required:
        return bool(mine & required)
    return is_requester or "admin" in mine
