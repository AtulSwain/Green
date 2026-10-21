"""Developer-assistance tools scoped to the project directory the user selected."""

from __future__ import annotations

from pydantic import Field

from ..services import file_access
from ..services.tool_registry import ConfirmationRequest, Tool, ToolArgs, ToolCategory, ToolError
from .context import ToolContext
from .filesystem_tool import UNTRUSTED_NOTE

MAX_LISTED_FILES = 500


class _WorkspaceTool(Tool):
    def __init__(self, context: ToolContext):
        self.context = context

    def policy(self):
        policy = self.context.workspace_policy()
        if not policy.roots:
            raise ToolError("No project workspace is selected. The user can choose one on the Workspace screen.")
        return policy


class WorkspaceListArgs(ToolArgs):
    subdirectory: str = Field("", max_length=1024, description="Path relative to the project root; empty for the root")


class WorkspaceListFilesTool(_WorkspaceTool):
    name = "workspace_list_files"
    description = "Recursively list source files in the selected project workspace (protected files excluded)."
    Args = WorkspaceListArgs

    def prepare(self, args):
        policy = self.policy()
        return policy, policy.resolve(args.subdirectory or ".")

    def describe(self, args, prepared):
        return "Listing project files"

    def audit_detail(self, args, prepared):
        return f"path={prepared[1]}"

    def summarize(self, data):
        return f"{len(data['files'])} files" + (" (limit reached)" if data["truncated"] else "")

    def run(self, args, prepared):
        policy, start = prepared
        root = policy.roots[0]
        files = []
        truncated = False
        for path in policy.walk_files(start):
            if len(files) >= MAX_LISTED_FILES:
                truncated = True
                break
            files.append(path.relative_to(root).as_posix())
        return {"project_root": str(root), "files": files, "truncated": truncated}


class WorkspaceReadArgs(ToolArgs):
    path: str = Field(..., min_length=1, max_length=1024, description="File path relative to the project root")


class WorkspaceReadFileTool(_WorkspaceTool):
    name = "workspace_read_file"
    description = "Read a source file from the selected project workspace."
    Args = WorkspaceReadArgs

    def prepare(self, args):
        return self.policy().resolve(args.path)

    def describe(self, args, prepared):
        return f"Reading project file {args.path}"

    def audit_detail(self, args, prepared):
        return f"path={prepared}"

    def summarize(self, data):
        return f"Read {len(data['content']):,} characters" + (" (truncated)" if data["truncated"] else "")

    def run(self, args, prepared):
        content = file_access.read_text(prepared, self.context.config().tools.max_read_bytes)
        return {"path": args.path, "truncated": content.truncated, "note": UNTRUSTED_NOTE, "content": content.text}


class WorkspaceSearchArgs(ToolArgs):
    query: str = Field(..., min_length=1, max_length=200, description="Literal text or symbol name to find")
    file_pattern: str = Field("", max_length=100, description="Optional file name glob such as '*.py'")


class WorkspaceSearchTextTool(_WorkspaceTool):
    name = "workspace_search_text"
    description = "Search for text or a symbol inside source files of the selected project workspace."
    Args = WorkspaceSearchArgs

    def prepare(self, args):
        policy = self.policy()
        return policy, policy.resolve(".")

    def describe(self, args, prepared):
        return "Searching the project for text"

    def audit_detail(self, args, prepared):
        return f"query_length={len(args.query)}"

    def summarize(self, data):
        return f"{len(data['matches'])} matches" + (" (limit reached)" if data["truncated"] else "")

    def run(self, args, prepared):
        policy, root = prepared
        matches, truncated = file_access.search_text(
            policy, root, args.query, self.context.config().tools.max_results, file_pattern=args.file_pattern
        )
        return {
            "matches": [
                {"path": m.path.relative_to(root).as_posix(), "line": m.line_number, "text": m.line} for m in matches
            ],
            "truncated": truncated,
            "note": UNTRUSTED_NOTE,
        }


class ProposeFileChangeArgs(ToolArgs):
    path: str = Field(..., min_length=1, max_length=1024, description="File path relative to the project root")
    new_content: str = Field(..., max_length=file_access.MAX_WRITE_BYTES, description="The complete new file content")
    explanation: str = Field("", max_length=2000, description="Short description of the change")


class ProposeFileChangeTool(_WorkspaceTool):
    name = "propose_file_change"
    description = (
        "Propose a change to a file in the project workspace by giving its complete new content. "
        "The user reviews a diff and decides whether to apply it; a backup of the old file is kept."
    )
    category = ToolCategory.CONTROLLED
    Args = ProposeFileChangeArgs
    requires_confirmation = True

    def prepare(self, args):
        target = self.policy().resolve(args.path, must_exist=False)
        file_access.check_writable_name(target)
        old = ""
        if target.exists():
            old = file_access.read_text(target, file_access.MAX_WRITE_BYTES).text
        diff = file_access.unified_diff(old, args.new_content, args.path)
        return target, diff, target.exists()

    def describe(self, args, prepared):
        return f"Proposing a change to {args.path}"

    def summarize(self, data):
        return f"Applied change to {data['applied']}" + ("; backup kept" if data.get("backup") else "")

    def confirmation(self, args, prepared):
        target, diff, exists = prepared
        summary = args.explanation or ("GREEN proposes changes to this file." if exists else "GREEN proposes a new file.")
        return ConfirmationRequest(
            self.name,
            "Apply proposed change?" if exists else "Create proposed file?",
            summary,
            target=str(target),
            details=diff,
            details_are_diff=True,
            destructive=exists,
        )

    def audit_detail(self, args, prepared):
        return f"path={prepared[0]}"

    def run(self, args, prepared):
        target, _, _ = prepared
        backup = file_access.replace_text_file(target, args.new_content, self.context.file_backup_dir)
        return {"applied": str(target), "backup": str(backup) if backup else None}
