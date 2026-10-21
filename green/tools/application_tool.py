from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pydantic import Field

from ..services.tool_registry import ConfirmationRequest, Tool, ToolArgs, ToolCategory, ToolError
from .context import ToolContext

LAUNCH_CHECK_SECONDS = 0.5


class OpenApplicationArgs(ToolArgs):
    name: str = Field(..., min_length=1, max_length=64, description="Name of an approved application")


class OpenApplicationTool(Tool):
    name = "open_application"
    description = (
        "Open one of the applications the user has approved, by its approved name. "
        "No arguments or commands can be passed to the program."
    )
    category = ToolCategory.CONTROLLED
    Args = OpenApplicationArgs
    requires_confirmation = True

    def __init__(self, context: ToolContext, popen=None):
        self.context = context
        self._popen = popen

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

    def describe(self, args, prepared):
        return f"Opening application {prepared[0]}"

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
        popen = self._popen or subprocess.Popen
        # A fixed argv list without a shell: the model cannot inject commands or arguments.
        process = popen([str(path)], shell=False, cwd=str(path.parent), **kwargs)  # noqa: S603
        # Report a program that fails immediately instead of claiming success.
        deadline = time.monotonic() + LAUNCH_CHECK_SECONDS
        while time.monotonic() < deadline:
            code = process.poll()
            if code is not None:
                if code != 0:
                    raise ToolError(f"{name} exited immediately with code {code}.")
                break
            time.sleep(0.05)
        return {"opened": name, "process_id": process.pid}

    def summarize(self, data):
        return f"Started {data['opened']} (process {data['process_id']})"
