"""Registry and gatekeeper for model-requested tools.

The model can only *request* a tool. Every request goes through :meth:`ToolRegistry.invoke`,
which in order:

1. checks that the tool name is registered,
2. checks that the user has enabled it (deny by default),
3. validates the arguments against the tool's strict schema,
4. runs the tool's own policy checks (paths, allow-lists),
5. asks the user to confirm when the tool requires it (every call, never remembered),
6. checks that the request has not been cancelled,
7. executes it, and records the outcome in the tool history.

Model output can never skip a step. Restricted capabilities (shell execution,
credential access, installing software, ...) cannot be registered at all.
Tools are plain Python classes with fixed code; nothing is built from model output,
and there is no use of eval/exec or a shell.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from ..security.path_policy import PathPolicyError
from .permission_service import AuditLog, PermissionService

log = logging.getLogger(__name__)


class ToolCategory(StrEnum):
    READ = "read"
    CONTROLLED = "controlled"
    RESTRICTED = "restricted"

    @property
    def label(self) -> str:
        return {"read": "Read", "controlled": "Controlled (asks first)", "restricted": "Restricted"}[self.value]


class ErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    PERMISSION_DENIED = "permission_denied"
    INVALID_ARGUMENTS = "invalid_arguments"
    POLICY_VIOLATION = "policy_violation"
    DECLINED = "declined_by_user"
    CANCELLED = "cancelled"
    EXECUTION_FAILED = "execution_failed"


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


_HUMAN_ERRORS = {
    ErrorCode.DECLINED.value: "You declined, so nothing was done.",
    ErrorCode.CANCELLED.value: "Stopped before it ran.",
}


class ToolArgs(BaseModel):
    """Base for tool argument schemas: unknown fields are rejected.

    Strings are not stripped here because file content must be preserved exactly.
    """

    model_config = ConfigDict(extra="forbid")


class ToolError(Exception):
    """Expected tool failure; the message is safe to show to the user and the model."""


class Cancellable(Protocol):
    @property
    def cancelled(self) -> bool: ...


@dataclass
class ConfirmationRequest:
    tool_name: str
    title: str
    summary: str
    target: str = ""
    details: str = ""
    details_are_diff: bool = False
    destructive: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    tool_name: str
    ok: bool
    data: Any = None
    error: str = ""
    code: str | None = None
    decision: str = "allowed"  # allowed | approved_by_user | denied | declined | rejected | cancelled
    description: str = ""
    summary: str = ""
    duration_ms: int = 0

    def to_model_content(self) -> str:
        payload: dict[str, Any] = {"tool": self.tool_name, "ok": self.ok}
        if self.ok:
            payload["result"] = self.data
        else:
            payload["error"] = {"code": self.code or ErrorCode.EXECUTION_FAILED.value, "message": self.error}
        return json.dumps(payload, ensure_ascii=False, default=str)

    @property
    def human_summary(self) -> str:
        """What happened, phrased for the user (the model gets ``error`` instead)."""
        if self.ok:
            return self.summary
        return _HUMAN_ERRORS.get(self.code or "", self.error)

    def to_record(self) -> dict[str, Any]:
        """Non-sensitive summary stored with the assistant message."""
        return {
            "tool": self.tool_name,
            "ok": self.ok,
            "decision": self.decision,
            "code": self.code,
            "description": self.description,
            "summary": self.human_summary,
        }


@dataclass
class ToolEvent:
    """Progress of one tool call, for the UI. ``phase`` is one of:
    ``started``, ``awaiting_approval``, ``running``, ``finished``."""

    phase: str
    call_id: int
    tool_name: str
    description: str
    result: ToolResult | None = None


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

    def needs_confirmation(self, args: ToolArgs, prepared: Any) -> bool:
        return self.requires_confirmation

    def confirmation(self, args: ToolArgs, prepared: Any) -> ConfirmationRequest:
        return ConfirmationRequest(self.name, f"Allow {self.name}?", self.describe(args, prepared))

    def describe(self, args: ToolArgs, prepared: Any) -> str:
        """Readable description of the action, shown while it runs."""
        return f"Running {self.name}"

    def run(self, args: ToolArgs, prepared: Any) -> Any:
        raise NotImplementedError

    def summarize(self, data: Any) -> str:
        """Short, non-sensitive summary of a successful result (never file contents)."""
        return "Completed"

    def audit_detail(self, args: ToolArgs, prepared: Any) -> str:
        return ""

    def schema(self) -> dict[str, Any]:
        params = self.Args.model_json_schema()
        params.pop("title", None)
        params.setdefault("properties", {})
        params.setdefault("type", "object")
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": params}}


ConfirmCallback = Callable[[ConfirmationRequest], bool]
EventCallback = Callable[[ToolEvent], None]


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


@dataclass
class ToolRegistry:
    permissions: PermissionService
    audit: AuditLog
    _tools: dict[str, Tool] = field(default_factory=dict)
    _call_ids: int = 0

    def register(self, tool: Tool) -> None:
        if not getattr(tool, "name", None) or not getattr(tool, "description", None):
            raise ValueError("Tools need a name and a description")
        if tool.category == ToolCategory.RESTRICTED:
            raise ValueError(f"Restricted tool '{tool.name}' cannot be registered")
        if tool.category == ToolCategory.CONTROLLED and type(tool).confirmation is Tool.confirmation:
            raise ValueError(f"Controlled tool '{tool.name}' must define its confirmation")
        if tool.category == ToolCategory.CONTROLLED and type(tool).needs_confirmation is Tool.needs_confirmation \
                and not tool.requires_confirmation:
            raise ValueError(f"Controlled tool '{tool.name}' must require confirmation")
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

    def invoke(
        self,
        name: Any,
        raw_args: Any,
        confirm: ConfirmCallback | None = None,
        *,
        cancel: Cancellable | None = None,
        on_event: EventCallback | None = None,
        conversation_id: str | None = None,
    ) -> ToolResult:
        self._call_ids += 1
        call_id = self._call_ids
        started = time.monotonic()
        tool_name = name if isinstance(name, str) else repr(name)[:60]
        tool = self._tools.get(name) if isinstance(name, str) else None
        description = f"Requested tool '{tool_name}'"

        def emit(phase: str, result: ToolResult | None = None) -> None:
            if on_event is not None:
                try:
                    on_event(ToolEvent(phase, call_id, tool_name, description, result))
                except Exception:  # UI problems must not change the outcome
                    log.exception("Tool event callback failed")

        def finish(result: ToolResult, category: str, outcome: str, detail: str = "") -> ToolResult:
            result.description = description
            result.duration_ms = int((time.monotonic() - started) * 1000)
            self.audit.record(
                tool_name, category, result.decision, outcome, detail,
                summary=result.human_summary,
                conversation_id=conversation_id, duration_ms=result.duration_ms,
            )
            emit("finished", result)
            return result

        emit("started")
        if tool is None:
            return finish(
                ToolResult(tool_name, False, error=f"Unknown tool '{tool_name}'. Only the listed tools exist.",
                           code=ErrorCode.UNKNOWN_TOOL, decision="rejected"),
                "unknown", "error", "unknown tool requested",
            )

        category = tool.category.value
        description = f"Using {tool.name}"
        if not self.permissions.is_enabled(tool.name):
            return finish(
                ToolResult(tool.name, False, code=ErrorCode.PERMISSION_DENIED, decision="denied",
                           error=f"The tool '{tool.name}' is disabled. The user can enable it under Tools & Permissions."),
                category, "not_run", "tool disabled in permissions",
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
            return finish(
                ToolResult(tool.name, False, error=f"Invalid arguments: {detail}", code=ErrorCode.INVALID_ARGUMENTS,
                           decision="rejected"),
                category, "invalid_arguments", detail,
            )

        try:
            prepared = tool.prepare(args)
            description = tool.describe(args, prepared)
            needs_confirmation = tool.category == ToolCategory.CONTROLLED and tool.needs_confirmation(args, prepared)
        except (ToolError, PathPolicyError) as exc:
            return finish(
                ToolResult(tool.name, False, error=str(exc), code=ErrorCode.POLICY_VIOLATION, decision="denied"),
                category, "policy", str(exc),
            )
        except Exception as exc:  # a broken check must deny, never fall through to execution
            log.exception("Policy check for %s failed", tool.name)
            return finish(
                ToolResult(tool.name, False, error="The request could not be checked safely, so it was not run.",
                           code=ErrorCode.POLICY_VIOLATION, decision="denied"),
                category, "policy", exc.__class__.__name__,
            )
        try:
            detail = tool.audit_detail(args, prepared)
        except Exception:
            detail = ""

        if cancel is not None and cancel.cancelled:
            return finish(ToolResult(tool.name, False, error="Cancelled before running.", code=ErrorCode.CANCELLED,
                                     decision="cancelled"), category, "not_run", detail)

        if needs_confirmation:
            try:
                request = tool.confirmation(args, prepared)
            except Exception:
                log.exception("Building the confirmation for %s failed", tool.name)
                request = None
            emit("awaiting_approval")
            approved = False
            if confirm is not None and request is not None:
                try:
                    approved = bool(confirm(request))
                except Exception:
                    log.exception("Confirmation callback failed")
            if cancel is not None and cancel.cancelled:
                return finish(ToolResult(tool.name, False, error="Cancelled before running.", code=ErrorCode.CANCELLED,
                                         decision="cancelled"), category, "not_run", detail)
            if not approved:
                return finish(
                    ToolResult(tool.name, False, code=ErrorCode.DECLINED, decision="declined",
                               error="The user declined this action. Do not retry it unless the user asks."),
                    category, "not_run", detail,
                )
            decision = "approved_by_user"
        else:
            decision = "allowed"

        emit("running")
        try:
            data = tool.run(args, prepared)
        except (ToolError, PathPolicyError) as exc:
            return finish(ToolResult(tool.name, False, error=str(exc), code=ErrorCode.EXECUTION_FAILED, decision=decision),
                          category, "error", f"{detail} | {exc}")
        except PermissionError:
            return finish(ToolResult(tool.name, False, error="Access was denied by the operating system.",
                                     code=ErrorCode.EXECUTION_FAILED, decision=decision),
                          category, "error", f"{detail} | PermissionError")
        except OSError as exc:
            return finish(ToolResult(tool.name, False, error=f"File or system error: {exc.strerror or exc.__class__.__name__}",
                                     code=ErrorCode.EXECUTION_FAILED, decision=decision),
                          category, "error", f"{detail} | {exc.__class__.__name__}")
        except Exception as exc:  # never let a tool crash the conversation
            log.exception("Tool %s failed", tool.name)
            return finish(ToolResult(tool.name, False, error="The tool failed unexpectedly.",
                                     code=ErrorCode.EXECUTION_FAILED, decision=decision),
                          category, "error", f"{detail} | {exc.__class__.__name__}")
        try:
            summary = tool.summarize(data)
        except Exception:
            summary = "Completed"
        return finish(ToolResult(tool.name, True, data=data, decision=decision, summary=summary), category, "ok", detail)
