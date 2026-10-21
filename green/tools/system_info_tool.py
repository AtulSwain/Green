"""Limited, non-sensitive system information."""

from __future__ import annotations

import platform
import sys

from .. import __version__
from ..services.ollama_service import OllamaError
from ..services.tool_registry import Tool, ToolCategory
from .context import ToolContext


def windows_version() -> str | None:
    """'Windows 11 (build 22631)' on Windows, None elsewhere."""
    if sys.platform != "win32":
        return None
    version = platform.version()  # e.g. 10.0.22631
    try:
        major, _minor, build = (int(p) for p in version.split(".")[:3])
    except ValueError:
        return f"Windows {platform.release()} ({version})"
    name = "Windows 11" if major == 10 and build >= 22000 else f"Windows {platform.release()}"
    return f"{name} (build {build})"


def operating_system() -> str:
    return windows_version() or f"{platform.system()} {platform.release()}"


class GetSystemInfoTool(Tool):
    name = "get_system_info"
    description = (
        "Get limited, non-sensitive system information: operating system version, Python version, "
        "GREEN version, whether Ollama is reachable, the configured model and whether it supports tools."
    )
    category = ToolCategory.READ

    def __init__(self, context: ToolContext):
        self.context = context

    def describe(self, args, prepared):
        return "Checking system information"

    def run(self, args, prepared):
        config = self.context.config()
        ollama: dict = {"configured_model": config.ollama.model, "local": config.ollama.is_local}
        if self.context.ollama is None:
            ollama["reachable"] = None
        else:
            try:
                status = self.context.ollama.status()
            except OllamaError:
                status = None
            if status is None:
                ollama["reachable"] = False
            else:
                ollama["reachable"] = status.reachable
                if status.reachable:
                    ollama["version"] = status.version
                    ollama["model_installed"] = status.model_installed
                    ollama["tool_calling"] = {True: "supported", False: "not supported", None: "unknown"}[status.supports_tools]
        # Deliberately excluded: user name, host name, environment variables, paths, hardware identifiers.
        return {
            "operating_system": operating_system(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "green_version": __version__,
            "ollama": ollama,
        }

    def summarize(self, data):
        reach = data["ollama"].get("reachable")
        return f"{data['operating_system']}, Python {data['python_version']}, Ollama {'reachable' if reach else 'not reachable'}"
