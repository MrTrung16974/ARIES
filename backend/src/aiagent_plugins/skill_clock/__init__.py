"""Skill thời gian: ngày giờ hiện tại theo múi giờ.

Model không biết thời điểm hiện tại nếu không có tool này.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
)

_WEEKDAYS_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

_MANIFEST = PluginManifest(
    id="clock",
    kind=PluginKind.SKILL,
    name="Đồng hồ",
    description="Cho agent biết ngày giờ hiện tại theo múi giờ.",
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    tools=(
        ToolSpec(
            name="clock.now",
            description=(
                "Lấy ngày giờ hiện tại. Dùng khi câu hỏi phụ thuộc thời điểm hiện tại "
                "(hôm nay, bây giờ, thứ mấy, còn bao nhiêu ngày...)."
            ),
            risk=RiskLevel.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Múi giờ IANA, mặc định Asia/Ho_Chi_Minh.",
                        "maxLength": 64,
                    }
                },
                "additionalProperties": False,
            },
        ),
    ),
    ui_hints={"icon": "pi pi-clock", "category": "utility"},
)


class ClockSkill(Plugin):
    def __init__(self, now: Any = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name != "clock.now":
            raise ToolNotFoundError(name)
        tz_name = str(arguments.get("timezone") or "Asia/Ho_Chi_Minh")
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            return ToolResult(content=f"Múi giờ không hợp lệ: {tz_name}", is_error=True)
        now = self._now().astimezone(tz)
        text = f"{_WEEKDAYS_VI[now.weekday()]}, {now:%d/%m/%Y %H:%M:%S} ({tz_name}, UTC{now:%z})"
        return ToolResult(content=text, data={"iso": now.isoformat(), "timezone": tz_name})
