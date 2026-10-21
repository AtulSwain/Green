"""Open a link in the default browser. Only opens the page: no scraping, no form submission."""

from __future__ import annotations

import ipaddress
import webbrowser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import Field

from ..services.tool_registry import ConfirmationRequest, Tool, ToolArgs, ToolCategory, ToolError
from .context import ToolContext

MAX_URL_LENGTH = 2000


class OpenWebsiteArgs(ToolArgs):
    url: str = Field(..., min_length=8, max_length=MAX_URL_LENGTH, description="Full http:// or https:// URL")


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == site or host.endswith("." + site) for site in allowed)


@dataclass
class WebsiteTarget:
    url: str
    host: str
    listed: bool
    warnings: list[str] = field(default_factory=list)


def inspect_url(url: str, allowed: list[str]) -> WebsiteTarget:
    """Validate a URL and list anything that makes the destination ambiguous."""
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ToolError("The link contains spaces or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ToolError("The link is not a valid URL.") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise ToolError("Only http:// and https:// links can be opened.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ToolError("Links containing user names or passwords are not allowed.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise ToolError("The link has no host name.")
    warnings: list[str] = []
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ToolError("The host name is not valid.") from exc
    if ascii_host != host or any(label.startswith("xn--") for label in ascii_host.split(".")):
        warnings.append(f"International domain name (appears as {ascii_host}); check it is the site you expect.")
    try:
        ipaddress.ip_address(host.strip("[]"))
        warnings.append("The link points to a raw IP address instead of a named website.")
    except ValueError:
        pass
    if port is not None and port not in (80, 443):
        warnings.append(f"Unusual port {port}.")
    if parts.scheme.lower() == "http":
        warnings.append("The connection is not encrypted (http).")
    return WebsiteTarget(parts.geturl(), ascii_host.lower(), host_allowed(ascii_host, allowed), warnings)


class OpenWebsiteTool(Tool):
    name = "open_website"
    description = (
        "Open a web page in the user's default browser. Only http(s) links. Sites on the user's allow-list open "
        "directly; other sites need the user's confirmation. This tool cannot read the page."
    )
    category = ToolCategory.CONTROLLED
    Args = OpenWebsiteArgs

    def __init__(self, context: ToolContext, opener=webbrowser.open):
        self.context = context
        self._opener = opener

    def prepare(self, args):
        tools = self.context.config().tools
        target = inspect_url(args.url, tools.allowed_websites)
        if not target.listed and not tools.confirm_unlisted_websites:
            raise ToolError(f"The website '{target.host}' is not on the allowed list.")
        return target

    def needs_confirmation(self, args, prepared):
        # Pre-approved, unambiguous destinations open directly; anything else is confirmed.
        return not prepared.listed or bool(prepared.warnings)

    def describe(self, args, prepared):
        return f"Opening {prepared.host} in the browser"

    def confirmation(self, args, prepared):
        note = "" if prepared.listed else " This site is not on your allowed list."
        return ConfirmationRequest(
            self.name,
            "Open website?",
            f"GREEN wants to open this link in your browser.{note}",
            target=prepared.url,
            warnings=prepared.warnings,
        )

    def audit_detail(self, args, prepared):
        return f"host={prepared.host} listed={prepared.listed}"

    def run(self, args, prepared):
        if not self._opener(prepared.url):
            raise ToolError("No web browser could be opened.")
        return {"opened": prepared.url, "host": prepared.host}

    def summarize(self, data):
        return f"Opened a page on {data['host']}"
