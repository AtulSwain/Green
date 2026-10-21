"""Built-in tools available to the model (subject to permissions)."""

from __future__ import annotations

from ..services.tool_registry import ToolRegistry
from .app_info_tool import GetAppInfoTool
from .application_tool import OpenApplicationTool
from .assistant_tools import CreateReminderTool, SaveMemoryTool
from .browser_tool import OpenWebsiteTool
from .context import ToolContext
from .datetime_tool import GetDateTimeTool
from .system_info_tool import GetSystemInfoTool
from .filesystem_tool import CreateTextFileTool, ListDirectoryTool, ReadTextFileTool, SearchFilesTool
from .workspace_tool import (
    ProposeFileChangeTool,
    WorkspaceListFilesTool,
    WorkspaceReadFileTool,
    WorkspaceSearchTextTool,
)


def register_default_tools(registry: ToolRegistry, context: ToolContext) -> ToolRegistry:
    registry.register(GetDateTimeTool())
    registry.register(GetAppInfoTool(context))
    registry.register(GetSystemInfoTool(context))
    registry.register(ListDirectoryTool(context))
    registry.register(SearchFilesTool(context))
    registry.register(ReadTextFileTool(context))
    registry.register(CreateTextFileTool(context))
    registry.register(OpenApplicationTool(context))
    registry.register(OpenWebsiteTool(context))
    registry.register(SaveMemoryTool(context))
    registry.register(CreateReminderTool(context))
    registry.register(WorkspaceListFilesTool(context))
    registry.register(WorkspaceReadFileTool(context))
    registry.register(WorkspaceSearchTextTool(context))
    registry.register(ProposeFileChangeTool(context))
    return registry


__all__ = ["ToolContext", "register_default_tools"]
