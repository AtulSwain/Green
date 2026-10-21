from __future__ import annotations

from .. import __version__
from ..services.tool_registry import Tool, ToolCategory
from .context import ToolContext


class GetAppInfoTool(Tool):
    name = "get_app_info"
    description = (
        "Get GREEN's non-sensitive configuration: version, model, approved directories, output "
        "directory, project workspace, approved application names, allowed websites and enabled tools."
    )
    category = ToolCategory.READ

    def __init__(self, context: ToolContext):
        self.context = context

    def run(self, args, prepared):
        config = self.context.config()
        return {
            "version": __version__,
            "model": config.ollama.model,
            "local_inference": config.ollama.is_local,
            "approved_directories": config.tools.approved_directories,
            "output_directory": config.tools.output_directory or None,
            "project_workspace": config.workspace.project_directory or None,
            "approved_applications": sorted(config.tools.approved_applications),
            "allowed_websites": config.tools.allowed_websites,
            "enabled_tools": sorted(n for n, on in config.tools.enabled_tools.items() if on),
        }
