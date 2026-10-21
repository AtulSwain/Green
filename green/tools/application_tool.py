from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydantic import Field

from ..services.tool_registry import ConfirmationRequest, Tool, ToolArgs, ToolCategory, ToolError
from .context import ToolContext


class OpenApplicationArgs(ToolArgs):
    name: str = Field(..., min_length=1, max_length=64, description="Name of an approved application")


class OpenApplicationTool(Tool):
    name = "open_application"
    description = (
        "Open one of the applications the user has approved, by its approved name. "
        "No arguments or commands can be passed to the program."
    )
    category = ToolCategory.WRITE
    Args = OpenApplicationArgs
    requires_confirmation = True

    def __init__(self, context: ToolContext):
        self.context = context

    def prepare(self, args):
        apps = self.context.config().tools.approved_applications
        wanted = args.name.strip().lower()
        match = next((n for n in apps if n.lower() == wanted), None)
        if match is None:
            raise ToolError(
                f"'{args.name}' is not an approved application. Approved: {', '.join(sorted(apps)) or 'none'}."
            )
        path = Path(apps[match])
        if not path.is_file():
            raise ToolError(f"The approved program for '{match}' was not found on disk.")
        return match, path

    def confirmation(self, args, prepared):
        name, path = prepared
        return ConfirmationRequest(self.name, f"Open {name}?", "GREEN wants to start this program.", target=str(path))

    def audit_detail(self, args, prepared):
        return f"application={prepared[0]}"

    def run(self, args, prepared):
        name, path = prepared
        kwargs = {"close_fds": True, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        # A fixed argv list without a shell: the model cannot inject commands or arguments.
        subprocess.Popen([str(path)], shell=False, cwd=str(path.parent), **kwargs)  # noqa: S603
        return {"opened": name}
