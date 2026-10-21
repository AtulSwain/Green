"""Stage 2: registry contract, permission service and desktop tool safety (no model involved)."""

import json
import os
import platform

import pytest
from pydantic import Field

from green.services.tool_registry import (
    ErrorCode,
    Tool,
    ToolArgs,
    ToolCategory,
    ToolError,
)


def enable(services, *names, **tool_settings):
    def mutate(config):
        config.tools.enabled_tools = {**config.tools.enabled_tools, **{n: True for n in names}}
        for key, value in tool_settings.items():
            setattr(config.tools, key, value)
    services.config_store.update(mutate)


class Recorder:
    """Confirmation callback that records requests and answers a fixed value."""

    def __init__(self, answer: bool):
        self.answer = answer
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.answer


# -- registration and lookup ----------------------------------------------------------------------

def test_every_registered_tool_meets_the_contract(services):
    for tool in services.registry.tools():
        assert tool.name and tool.description
        assert issubclass(tool.Args, ToolArgs)
        assert tool.Args.model_config.get("extra") == "forbid"
        assert tool.category in (ToolCategory.READ, ToolCategory.CONTROLLED)
        schema = tool.schema()
        assert schema["type"] == "function" and schema["function"]["name"] == tool.name
        assert schema["function"]["parameters"]["type"] == "object"
        assert services.registry.get(tool.name) is tool
    assert services.registry.get("nonexistent") is None


def test_no_restricted_capability_is_exposed(services):
    enable(services, *[t.name for t in services.registry.tools()])
    names = {s["function"]["name"] for s in services.registry.model_schemas()}
    assert names == {t.name for t in services.registry.tools()}
    for forbidden in ("shell", "powershell", "cmd", "exec", "eval", "python", "delete", "install", "registry"):
        assert not any(forbidden in name for name in names), forbidden


def test_registration_rules(services):
    class NoDescription(Tool):
        name = "x1"
        description = ""

    class Controlled(Tool):
        name = "x2"
        description = "d"
        category = ToolCategory.CONTROLLED
        requires_confirmation = True  # but no confirmation() defined

    with pytest.raises(ValueError):
        services.registry.register(NoDescription())
    with pytest.raises(ValueError):
        services.registry.register(Controlled())
    with pytest.raises(ValueError):
        services.registry.register(services.registry.get("get_datetime"))  # duplicate


def test_error_format_is_predictable(services):
    unknown = services.registry.invoke("format_disk", {})
    payload = json.loads(unknown.to_model_content())
    assert payload == {"tool": "format_disk", "ok": False,
                       "error": {"code": "unknown_tool", "message": unknown.error}}
    denied = services.registry.invoke("read_text_file", {"path": "a.txt"})
    assert denied.code == ErrorCode.PERMISSION_DENIED and denied.decision == "denied"
    bad = services.registry.invoke("get_datetime", {"unexpected": 1})
    assert bad.code == ErrorCode.INVALID_ARGUMENTS


def test_model_cannot_grant_itself_permission(services, tmp_path):
    # Arguments that try to smuggle authorization are rejected by the schema.
    for args in ({"path": "a.txt", "approved": True}, {"path": "a.txt", "permission": "granted"}):
        enable(services, "read_text_file", approved_directories=[str(tmp_path)])
        assert services.registry.invoke("read_text_file", args).code == ErrorCode.INVALID_ARGUMENTS
    # Disabling a tool takes effect immediately, even mid-conversation.
    services.permissions.set_enabled("read_text_file", False)
    assert services.registry.invoke("read_text_file", {"path": "a.txt"}).code == ErrorCode.PERMISSION_DENIED


def test_broken_policy_check_denies_instead_of_running(services):
    ran = []

    class Broken(Tool):
        name = "broken_tool"
        description = "d"

        def prepare(self, args):
            raise RuntimeError("bug")

        def run(self, args, prepared):
            ran.append(True)

    services.registry.register(Broken())
    enable(services, "broken_tool")
    result = services.registry.invoke("broken_tool", {})
    assert not result.ok and result.code == ErrorCode.POLICY_VIOLATION and not ran


def test_events_and_history_record(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    events = []
    result = services.registry.invoke(
        "create_text_file", {"filename": "n.txt", "content": "secret body text"}, confirm=lambda r: True,
        on_event=events.append, conversation_id="conv-1",
    )
    assert result.ok
    assert [e.phase for e in events] == ["started", "awaiting_approval", "running", "finished"]
    assert events[-1].result is result and result.description.startswith("Creating file")
    entry = services.audit.recent(1)[0]
    assert (entry.tool_name, entry.decision, entry.outcome, entry.conversation_id) == (
        "create_text_file", "approved_by_user", "ok", "conv-1")
    assert entry.summary.startswith("Created") and "secret body text" not in entry.summary + entry.detail
    assert entry.duration_ms >= 0


# -- cancellation and approvals ---------------------------------------------------------------------

class Cancel:
    def __init__(self, cancelled=False):
        self.cancelled = cancelled


def test_cancelled_before_execution(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    confirm = Recorder(True)
    result = services.registry.invoke("create_text_file", {"filename": "a.txt", "content": "x"}, confirm,
                                      cancel=Cancel(True))
    assert result.code == ErrorCode.CANCELLED and not confirm.requests and not (tmp_path / "a.txt").exists()


def test_cancelled_while_waiting_for_approval(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    cancel = Cancel()

    def approve_but_stop(request):
        cancel.cancelled = True  # user pressed Stop while the dialog was open
        return True

    result = services.registry.invoke("create_text_file", {"filename": "a.txt", "content": "x"}, approve_but_stop,
                                      cancel=cancel)
    assert result.code == ErrorCode.CANCELLED and not (tmp_path / "a.txt").exists()


def test_approval_is_never_remembered(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    confirm = Recorder(True)
    for name in ("one.txt", "two.txt"):
        assert services.registry.invoke("create_text_file", {"filename": name, "content": "x"}, confirm).ok
    assert len(confirm.requests) == 2
    declined = services.registry.invoke("create_text_file", {"filename": "three.txt", "content": "x"}, Recorder(False))
    assert declined.code == ErrorCode.DECLINED and not (tmp_path / "three.txt").exists()


# -- filesystem tools -------------------------------------------------------------------------------

@pytest.fixture
def approved(tmp_path, services):
    root = tmp_path / "approved"
    (root / "sub").mkdir(parents=True)
    (root / "notes.txt").write_text("hello notes")
    (root / "sub" / "deep.md").write_text("# deep")
    (root / ".env").write_text("TOKEN=abc")
    (root / "server.key").write_text("KEY")
    (tmp_path / "outside.txt").write_text("outside")
    enable(services, "list_directory", "search_files", "read_text_file", approved_directories=[str(root)])
    return root


@pytest.mark.parametrize("path", ["../outside.txt", "sub/../../outside.txt", "..\\outside.txt"])
def test_traversal_rejected_by_tools(services, approved, path):
    result = services.registry.invoke("read_text_file", {"path": path})
    assert result.code == ErrorCode.POLICY_VIOLATION


def test_access_outside_roots_rejected(services, approved, tmp_path):
    for tool, args in (("read_text_file", {"path": str(tmp_path / "outside.txt")}),
                       ("list_directory", {"path": str(tmp_path)}),
                       ("search_files", {"pattern": "*", "directory": str(tmp_path)})):
        result = services.registry.invoke(tool, args)
        assert result.code == ErrorCode.POLICY_VIOLATION, tool


def test_sensitive_files_excluded(services, approved):
    for path in (".env", "server.key"):
        assert services.registry.invoke("read_text_file", {"path": path}).code == ErrorCode.POLICY_VIOLATION
    names = {e["name"] for e in services.registry.invoke("list_directory", {}).data["entries"]}
    assert names == {"sub", "notes.txt"}
    matches = services.registry.invoke("search_files", {"pattern": "*"}).data["matches"]
    assert sorted(os.path.basename(m) for m in matches) == ["deep.md", "notes.txt"]


def test_missing_folder_and_file(services, approved):
    assert "does not exist" in services.registry.invoke("list_directory", {"path": "nope"}).error
    assert "not a folder" in services.registry.invoke("list_directory", {"path": "notes.txt"}).error
    assert "does not exist" in services.registry.invoke("read_text_file", {"path": "missing.txt"}).error


def test_large_file_is_bounded(services, approved):
    big = approved / "big.log"
    big.write_text("x" * 2_000_000)
    services.config_store.update(lambda c: setattr(c.tools, "max_read_bytes", 5_000))
    result = services.registry.invoke("read_text_file", {"path": "big.log"})
    assert result.ok and result.data["truncated"] and len(result.data["content"]) == 5_000
    assert result.data["size_bytes"] == 2_000_000 and "truncated" in result.summary


def test_result_limits(services, approved):
    for i in range(30):
        (approved / f"f{i}.txt").write_text("x")
    services.config_store.update(lambda c: setattr(c.tools, "max_results", 10))
    listing = services.registry.invoke("list_directory", {}).data
    assert len(listing["entries"]) == 10 and listing["truncated"]
    search = services.registry.invoke("search_files", {"pattern": "f*.txt"}).data
    assert len(search["matches"]) == 10 and search["truncated"]


def test_unsupported_file_type(services, approved):
    (approved / "photo.jpg").write_bytes(b"\xff\xd8")
    assert "Unsupported" in services.registry.invoke("read_text_file", {"path": "photo.jpg"}).error


# -- create_text_file ---------------------------------------------------------------------------------

def test_create_requires_confirmation_and_shows_target(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    confirm = Recorder(False)
    result = services.registry.invoke("create_text_file", {"filename": "plan.md", "content": "# Plan"}, confirm)
    assert result.code == ErrorCode.DECLINED and not (tmp_path / "plan.md").exists()
    request = confirm.requests[0]
    assert request.target == str((tmp_path / "plan.md").resolve()) and request.details == "# Plan"
    assert not request.destructive


def test_existing_file_not_overwritten_without_explicit_overwrite(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    (tmp_path / "plan.md").write_text("original")
    confirm = Recorder(True)
    result = services.registry.invoke("create_text_file", {"filename": "plan.md", "content": "new"}, confirm)
    assert result.code == ErrorCode.POLICY_VIOLATION and "already exists" in result.error
    assert not confirm.requests and (tmp_path / "plan.md").read_text() == "original"


def test_overwrite_needs_its_own_confirmation(services, tmp_path, app_paths):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    (tmp_path / "plan.md").write_text("original\n")
    args = {"filename": "plan.md", "content": "replacement\n", "overwrite": True}
    declined = Recorder(False)
    assert services.registry.invoke("create_text_file", args, declined).code == ErrorCode.DECLINED
    assert (tmp_path / "plan.md").read_text() == "original\n"
    request = declined.requests[0]
    assert request.destructive and request.details_are_diff and "-original" in request.details
    assert request.title == "Overwrite existing file?"
    result = services.registry.invoke("create_text_file", args, Recorder(True))
    assert result.ok and result.data["overwritten"] and (tmp_path / "plan.md").read_text() == "replacement\n"
    assert open(result.data["backup"]).read() == "original\n"


def test_overwrite_flag_on_new_file_just_creates(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    result = services.registry.invoke("create_text_file", {"filename": "n.txt", "content": "x", "overwrite": True},
                                      Recorder(True))
    assert result.ok and not result.data["overwritten"]


# -- open_website -------------------------------------------------------------------------------------

@pytest.fixture
def browser(services):
    opened = []
    services.registry.get("open_website")._opener = lambda url: opened.append(url) or True
    enable(services, "open_website", allowed_websites=["python.org"])
    return opened


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///C:/Windows/win.ini", "data:text/html,hi", "ftp://python.org/x",
    "vbscript:msgbox", "http://", "https://user:pw@python.org/", "https://python.org/a b",
    "https://python.org/\nx", "ms-settings:privacy", "\\\\server\\share",
])
def test_invalid_urls_rejected(services, browser, url):
    confirm = Recorder(True)
    result = services.registry.invoke("open_website", {"url": url}, confirm)
    assert not result.ok and result.code in (ErrorCode.POLICY_VIOLATION, ErrorCode.INVALID_ARGUMENTS), url
    assert not browser and not confirm.requests


def test_listed_site_opens_without_prompt(services, browser):
    confirm = Recorder(False)
    result = services.registry.invoke("open_website", {"url": "https://docs.python.org/3/"}, confirm)
    assert result.ok and browser == ["https://docs.python.org/3/"] and not confirm.requests
    assert result.summary == "Opened a page on docs.python.org"


def test_unlisted_or_ambiguous_sites_ask(services, browser):
    cases = {
        "https://example.com/": [],
        "http://python.org/": ["not encrypted"],
        "https://93.184.216.34/": ["raw IP"],
        "https://python.org:8443/": ["Unusual port"],
        "https://xn--pythn-mua.org/": ["International domain"],
    }
    for url, expected in cases.items():
        confirm = Recorder(False)
        result = services.registry.invoke("open_website", {"url": url}, confirm)
        assert result.code == ErrorCode.DECLINED, url
        warnings = " ".join(confirm.requests[0].warnings)
        for fragment in expected:
            assert fragment in warnings, (url, warnings)
    assert not browser


def test_unlisted_sites_can_be_forbidden(services, browser):
    services.config_store.update(lambda c: setattr(c.tools, "confirm_unlisted_websites", False))
    result = services.registry.invoke("open_website", {"url": "https://example.com"}, Recorder(True))
    assert result.code == ErrorCode.POLICY_VIOLATION and not browser


def test_browser_failure_reported(services, browser):
    services.registry.get("open_website")._opener = lambda url: False
    result = services.registry.invoke("open_website", {"url": "https://python.org"})
    assert not result.ok and result.code == ErrorCode.EXECUTION_FAILED


# -- open_application ---------------------------------------------------------------------------------

class FakeProcess:
    def __init__(self, code=None):
        self.code = code
        self.pid = 99

    def poll(self):
        return self.code


@pytest.fixture
def app_tool(services, tmp_path):
    program = tmp_path / "notepad.exe"
    program.write_text("")
    enable(services, "open_application", approved_applications={"Notepad": str(program)})
    calls = []
    tool = services.registry.get("open_application")
    tool._popen = lambda argv, **kw: calls.append((argv, kw)) or FakeProcess()
    return tool, program, calls


def test_application_allowlist(services, app_tool):
    tool, program, calls = app_tool
    for name in ("calc", "cmd.exe", "powershell", "notepad && del *", str(program)):
        result = services.registry.invoke("open_application", {"name": name}, Recorder(True))
        assert result.code in (ErrorCode.POLICY_VIOLATION, ErrorCode.INVALID_ARGUMENTS), name
    assert not calls
    confirm = Recorder(True)
    result = services.registry.invoke("open_application", {"name": "notepad"}, confirm)
    assert result.ok and calls[0][0] == [str(program)] and calls[0][1]["shell"] is False
    assert confirm.requests[0].target == str(program)


def test_application_extra_arguments_rejected(services, app_tool):
    result = services.registry.invoke("open_application", {"name": "Notepad", "arguments": ["/c", "calc"]}, Recorder(True))
    assert result.code == ErrorCode.INVALID_ARGUMENTS and not app_tool[2]


def test_application_launch_failure_reported(services, app_tool):
    tool, _, _ = app_tool
    tool._popen = lambda argv, **kw: FakeProcess(code=1)
    result = services.registry.invoke("open_application", {"name": "Notepad"}, Recorder(True))
    assert not result.ok and "exited immediately" in result.error

    def missing(argv, **kw):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    tool._popen = missing
    result = services.registry.invoke("open_application", {"name": "Notepad"}, Recorder(True))
    assert not result.ok and result.code == ErrorCode.EXECUTION_FAILED


# -- get_system_info ------------------------------------------------------------------------------------

def test_system_info_is_limited(services, fake_ollama, monkeypatch):
    monkeypatch.setenv("GREEN_TEST_SECRET", "s3cr3t-value-123")
    result = services.registry.invoke("get_system_info", {})
    assert result.ok
    data = result.data
    assert set(data) == {"operating_system", "architecture", "python_version", "green_version", "ollama"}
    assert data["python_version"] == platform.python_version() and data["green_version"] == "2.0.1"
    assert data["ollama"]["reachable"] is True and data["ollama"]["tool_calling"] == "supported"
    dumped = json.dumps(data)
    for private in ("s3cr3t-value-123", os.path.expanduser("~"), platform.node() or "\x00"):
        assert private not in dumped


def test_system_info_with_ollama_down(services_factory, closed_port_url):
    services = services_factory(closed_port_url)
    result = services.registry.invoke("get_system_info", {})
    assert result.ok and result.data["ollama"]["reachable"] is False


def test_windows_version_label(monkeypatch):
    from green.tools import system_info_tool

    monkeypatch.setattr(system_info_tool.sys, "platform", "win32")
    monkeypatch.setattr(system_info_tool.platform, "version", lambda: "10.0.22631")
    assert system_info_tool.windows_version() == "Windows 11 (build 22631)"
    monkeypatch.setattr(system_info_tool.platform, "version", lambda: "10.0.19045")
    monkeypatch.setattr(system_info_tool.platform, "release", lambda: "10")
    assert system_info_tool.windows_version() == "Windows 10 (build 19045)"


# -- independence from the model: a custom tool exercising every gate ---------------------------------

class EchoArgs(ToolArgs):
    text: str = Field(..., max_length=5)


def test_permission_gates_independent_of_model(services):
    ran = []

    class Echo(Tool):
        name = "echo_test"
        description = "test"
        category = ToolCategory.CONTROLLED
        Args = EchoArgs
        requires_confirmation = True

        def confirmation(self, args, prepared):
            from green.services.tool_registry import ConfirmationRequest
            return ConfirmationRequest(self.name, "Echo?", args.text)

        def prepare(self, args):
            if args.text == "bad":
                raise ToolError("blocked")

        def run(self, args, prepared):
            ran.append(args.text)
            return args.text

    services.registry.register(Echo())
    reg = services.registry
    assert reg.invoke("echo_test", {"text": "hi"}, Recorder(True)).code == ErrorCode.PERMISSION_DENIED
    enable(services, "echo_test")
    assert reg.invoke("echo_test", {"text": "toolong"}, Recorder(True)).code == ErrorCode.INVALID_ARGUMENTS
    assert reg.invoke("echo_test", {"text": "bad"}, Recorder(True)).code == ErrorCode.POLICY_VIOLATION
    assert reg.invoke("echo_test", {"text": "hi"}, Recorder(False)).code == ErrorCode.DECLINED
    assert reg.invoke("echo_test", {"text": "hi"}, None).code == ErrorCode.DECLINED
    assert reg.invoke("echo_test", {"text": "hi"}, Recorder(True)).ok
    assert ran == ["hi"]
