"""Plugin SDK — API công khai, ổn định (semver) cho mọi plugin.

Plugin (built-in hay bên thứ ba) CHỈ được import từ package này.
Thay đổi phá vỡ tương thích phải tăng `PLUGIN_API_VERSION` (xem ADR-0003).
"""

from aiagent.plugin_sdk.manifest import (
    ENTRY_POINT_GROUPS,
    PLUGIN_API_VERSION,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolSpec,
)
from aiagent.plugin_sdk.plugin import (
    ConnectionContext,
    HealthStatus,
    Plugin,
    PluginContext,
    ToolContext,
    ToolNotFoundError,
    ToolResult,
)

__all__ = [
    "ENTRY_POINT_GROUPS",
    "PLUGIN_API_VERSION",
    "ConnectionContext",
    "HealthStatus",
    "Plugin",
    "PluginContext",
    "PluginKind",
    "PluginManifest",
    "RiskLevel",
    "ToolContext",
    "ToolNotFoundError",
    "ToolResult",
    "ToolSpec",
]
