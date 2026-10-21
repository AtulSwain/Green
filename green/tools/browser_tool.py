from __future__ import annotations

import webbrowser
from urllib.parse import urlparse

from pydantic import Field

from ..services.tool_registry import ConfirmationRequest, Tool, ToolArgs, ToolCategory, ToolError
from .context import ToolContext


class OpenWebsiteArgs(ToolArgs):
    url: str = Field(..., min_length=4, max_length=2000, description="Full http(s) URL on an allowed website")


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == site or host.endswith("." + site) for site in allowed)


class OpenWebsiteTool(Tool):
    name = "open_website"
    description = "Open a page in the default web browser. Only websites on the user's allow-list can be opened."
    category = ToolCategory.WRITE
    Args = OpenWebsiteArgs
    requires_confirmation = True

    def __init__(self, context: ToolContext, opener=webbrowser.open):
        self.context = context
        self._opener = opener

    def prepare(self, args):
        parsed = urlparse(args.url)
        if parsed.scheme not in ("http", "https"):
            raise ToolError("Only http and https links can be opened.")
        if parsed.username or parsed.password:
            raise ToolError("Links containing credentials are not allowed.")
        host = parsed.hostname or ""
        if not host_allowed(host, self.context.config().tools.allowed_websites):
            raise ToolError(f"The website '{host}' is not on the allowed list.")
        return parsed.geturl()

    def confirmation(self, args, prepared):
        return ConfirmationRequest(
            self.name, "Open website?", "GREEN wants to open this link in your browser.", target=prepared
        )

    def audit_detail(self, args, prepared):
        return f"host={urlparse(prepared).hostname}"

    def run(self, args, prepared):
        if not self._opener(prepared):
            raise ToolError("No web browser could be opened.")
        return {"opened": prepared}
