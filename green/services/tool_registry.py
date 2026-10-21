"""Registry and gatekeeper for model-requested tools.

The model can only *request* a tool. Every request goes through :meth:`ToolRegistry.invoke`,
which in order:

1. checks that the tool exists,
2. checks that the user has enabled it (deny by default),
3. validates the arguments against a strict schema,
4. runs the tool's own policy checks (paths, allow-lists),
5. asks the user to confirm when the tool changes something,
6. executes it and records the outcome in the audit log.

Model output can never skip a step. Restricted capabilities (shell execution,
credential access, installing software, ...) are not registered at all.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..security.path_policy import PathPolicyError
from .permission_service import AuditLog, PermissionService

log = logging.getLogger(__name__)


class ToolCategory(StrEnum):
    READ = "read"
    WRITE = "controlled_write"
    RESTRICTED = "restricted"


# Capabilities that are deliberately unavailable. Shown in the permissions screen.
RESTRICTED_CAPABILITIES = (
    "Arbitrary shell, PowerShell, CMD or Python execution",
    "Access to credentials, browser cookies, saved passwords, SSH keys or API tokens",
    "Unrestricted filesystem access",
    "Changing security settings",
    "Installing software",
    "Destructive system operations",
    "Sending private information to external services",
)


class ToolArgs(BaseModel):
    """Base for tool argument schemas: unknown fields are rejected.

    Strings are not stripped here because file content must be preserved exactly.
    """

    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """Expected tool failure; the message is safe to show to the user and the model."""


@dataclass
class ConfirmationRequest:
    tool_name: str
    title: str
    summary: str
    target: str = ""
    details: str = ""
    details_are_diff: bool = False
    destructive: bool = False


@dataclass
class ToolResult:
    tool_name: str
    ok: bool
    data: Any = None
    error: str = ""
    decision: str = "allowed"  # allowed | denied | declined | rejected

    def to_model_content(self) -> str:
        payload: dict[str, Any] = {"tool": self.tool_name, "ok": self.ok}
        if self.ok:
            payload["result"] = self.data
        else:
            payload["error"] = self.error
        return json.dumps(payload, ensure_ascii=False, default=str)


class Tool:
    """Base class for tools. Subclasses set the class attributes and implement :meth:`run`."""

    name: ClassVar[str]
    description: ClassVar[str]
    category: ClassVar[ToolCategory] = ToolCategory.READ
    Args: ClassVar[type[ToolArgs]] = ToolArgs
    requires_confirmation: ClassVar[bool] = False

    def prepare(self, args: ToolArgs) -> Any:
        """Validate against policy before confirmation. Return data passed to run()."""
        return None

    def confirmation(self, args: ToolArgs, prepared: Any) -> ConfirmationRequest:
        return ConfirmationRequest(self.name, f"Allow {self.name}?", "GREEN wants to run this tool.")

    def run(self, args: ToolArgs, prepared: Any) -> Any:
        raise NotImplementedError

    def audit_detail(self, args: ToolArgs, prepared: Any) -> str:
        return ""

    def schema(self) -> dict[str, Any]:
        params = self.Args.model_json_schema()
        params.pop("title", None)
        params.setdefault("properties", {})
        params.setdefault("type", "object")
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": params}}


ConfirmCallback = Callable[[ConfirmationRequest], bool]


@dataclass
class ToolRegistry:
    permissions: PermissionService
    audit: AuditLog
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.category == ToolCategory.RESTRICTED:
            raise ValueError(f"Restricted tool '{tool.name}' cannot be registered")
        if tool.category == ToolCategory.WRITE and not tool.requires_confirmation:
            raise ValueError(f"Write tool '{tool.name}' must require confirmation")
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def enabled_tools(self) -> list[Tool]:
        return [t for t in self._tools.values() if self.permissions.is_enabled(t.name)]

    def model_schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.enabled_tools()]

    def invoke(self, name: Any, raw_args: Any, confirm: ConfirmCallback | None = None) -> ToolResult:
        tool_name = name if isinstance(name, str) else repr(name)
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            self.audit.record(tool_name, "unknown", "rejected", "error", "unknown tool requested")
            return ToolResult(tool_name, False, error=f"Unknown tool '{tool_name}'.", decision="rejected")

        category = tool.category.value
        if not self.permissions.is_enabled(tool.name):
            self.audit.record(tool.name, category, "denied", "not_run", "tool disabled in permissions")
            return ToolResult(
                tool.name,
                False,
                error=f"The tool '{tool.name}' is disabled. The user can enable it under Tools & Permissions.",
                decision="denied",
            )

        try:
            if raw_args is None or raw_args == "":
                raw_args = {}
            if isinstance(raw_args, str):
                raw_args = json.loads(raw_args)
            if not isinstance(raw_args, dict):
                raise ToolError("Tool arguments must be a JSON object.")
            args = tool.Args.model_validate(raw_args)
        except (json.JSONDecodeError, ToolError, ValidationError) as exc:
            detail = _validation_summary(exc)
            self.audit.record(tool.name, category, "rejected", "invalid_arguments", detail)
            return ToolResult(tool.name, False, error=f"Invalid arguments: {detail}", decision="rejected")

        try:
            prepared = tool.prepare(args)
        except (ToolError, PathPolicyError) as exc:
            self.audit.record(tool.name, category, "denied", "policy", str(exc))
            return ToolResult(tool.name, False, error=str(exc), decision="denied")

        detail = tool.audit_detail(args, prepared)
        if tool.requires_confirmation:
            request = tool.confirmation(args, prepared)
            approved = False
            if confirm is not None:
                try:
                    approved = bool(confirm(request))
                except Exception:
                    log.exception("Confirmation callback failed")
                    approved = False
            if not approved:
                self.audit.record(tool.name, category, "declined", "not_run", detail)
                return ToolResult(tool.name, False, error="The user declined this action.", decision="declined")
            decision = "approved_by_user"
        else:
            decision = "allowed"

        try:
            data = tool.run(args, prepared)
        except (ToolError, PathPolicyError) as exc:
            self.audit.record(tool.name, category, decision, "error", f"{detail} | {exc}")
            return ToolResult(tool.name, False, error=str(exc), decision=decision)
        except OSError as exc:
            self.audit.record(tool.name, category, decision, "error", f"{detail} | {exc.__class__.__name__}")
            return ToolResult(tool.name, False, error=f"File or system error: {exc.strerror or exc}", decision=decision)
        except Exception as exc:  # never let a tool crash the conversation
            log.exception("Tool %s failed", tool.name)
            self.audit.record(tool.name, category, decision, "error", f"{detail} | {exc.__class__.__name__}")
            return ToolResult(tool.name, False, error="The tool failed unexpectedly.", decision=decision)
        self.audit.record(tool.name, category, decision, "ok", detail)
        return ToolResult(tool.name, True, data=data, decision=decision)


def _validation_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:5]:
            loc = ".".join(str(p) for p in err["loc"]) or "arguments"
            parts.append(f"{loc}: {err['msg']}")
        return "; ".join(parts)
    if isinstance(exc, json.JSONDecodeError):
        return "arguments are not valid JSON"
    return str(exc)
