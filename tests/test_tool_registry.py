import json

import pytest

from green.services.tool_registry import ConfirmationRequest, Tool, ToolCategory


def enable(services, *names, **tool_settings):
    def mutate(config):
        config.tools.enabled_tools = {**config.tools.enabled_tools, **{n: True for n in names}}
        for key, value in tool_settings.items():
            setattr(config.tools, key, value)
    services.config_store.update(mutate)


def last_audit(services):
    return services.audit.recent(1)[0]


def test_enabled_by_default_only_safe_tools(services):
    names = {s["function"]["name"] for s in services.registry.model_schemas()}
    assert names == {"get_datetime", "get_app_info", "get_system_info"}


def test_disabled_tool_is_denied_and_audited(services):
    result = services.registry.invoke("read_text_file", {"path": "x.txt"})
    assert not result.ok and result.decision == "denied"
    entry = last_audit(services)
    assert (entry.tool_name, entry.decision, entry.outcome) == ("read_text_file", "denied", "not_run")


def test_unknown_and_malformed_names(services):
    for name in ["run_shell", None, 42, {"x": 1}]:
        result = services.registry.invoke(name, {})
        assert not result.ok and result.decision == "rejected"


@pytest.mark.parametrize("args", ["{not json", "[1, 2]", 7, {"path": 5}, {"path": "a", "extra": "field"}])
def test_invalid_arguments_rejected(services, tmp_path, args):
    enable(services, "read_text_file", approved_directories=[str(tmp_path)])
    result = services.registry.invoke("read_text_file", args)
    assert not result.ok and result.decision == "rejected"
    assert "Invalid arguments" in result.error


def test_string_json_arguments_accepted(services):
    result = services.registry.invoke("get_datetime", "{}")
    assert result.ok and "local_datetime" in result.data
    assert services.registry.invoke("get_datetime", "").ok


def test_restricted_and_unconfirmed_write_tools_cannot_register(services):
    class Shell(Tool):
        name = "run_shell"
        description = "x"
        category = ToolCategory.RESTRICTED

    class SneakyWrite(Tool):
        name = "sneaky"
        description = "x"
        category = ToolCategory.CONTROLLED
        requires_confirmation = False

    with pytest.raises(ValueError):
        services.registry.register(Shell())
    with pytest.raises(ValueError):
        services.registry.register(SneakyWrite())


def test_file_access_outside_approved_dirs_denied(services, tmp_path):
    approved = tmp_path / "ok"
    approved.mkdir()
    (approved / "a.txt").write_text("inside")
    (tmp_path / "secret.txt").write_text("outside")
    enable(services, "read_text_file", "list_directory", "search_files", approved_directories=[str(approved)])
    reg = services.registry
    assert reg.invoke("read_text_file", {"path": "a.txt"}).data["content"] == "inside"
    for path in [str(tmp_path / "secret.txt"), "../secret.txt"]:
        result = reg.invoke("read_text_file", {"path": path})
        assert not result.ok and "outside" in result.error and result.decision == "denied"
    assert not reg.invoke("list_directory", {"path": str(tmp_path)}).ok
    found = reg.invoke("search_files", {"pattern": "*.txt"}).data["matches"]
    assert found == [str((approved / "a.txt").resolve())]


def test_file_contents_not_written_to_audit_log(services, tmp_path):
    (tmp_path / "notes.txt").write_text("VERY-PRIVATE-CONTENT")
    enable(services, "read_text_file", approved_directories=[str(tmp_path)])
    assert services.registry.invoke("read_text_file", {"path": "notes.txt"}).ok
    for entry in services.audit.recent():
        assert "VERY-PRIVATE-CONTENT" not in entry.detail


def test_write_requires_confirmation(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))
    reg = services.registry
    seen: list[ConfirmationRequest] = []

    declined = reg.invoke("create_text_file", {"filename": "a.txt", "content": "hi"}, confirm=lambda r: seen.append(r) or False)
    assert not declined.ok and declined.decision == "declined"
    assert not (tmp_path / "a.txt").exists()
    assert seen[0].target == str((tmp_path / "a.txt").resolve()) and seen[0].details == "hi"

    no_callback = reg.invoke("create_text_file", {"filename": "a.txt", "content": "hi"})
    assert no_callback.decision == "declined" and not (tmp_path / "a.txt").exists()

    approved = reg.invoke("create_text_file", {"filename": "a.txt", "content": "hi"}, confirm=lambda r: True)
    assert approved.ok and (tmp_path / "a.txt").read_text() == "hi"
    assert last_audit(services).decision == "approved_by_user"

    again = reg.invoke("create_text_file", {"filename": "a.txt", "content": "new"}, confirm=lambda r: True)
    assert not again.ok and "already exists" in again.error
    assert (tmp_path / "a.txt").read_text() == "hi"


def test_create_file_policy(services, tmp_path):
    enable(services, "create_text_file")
    result = services.registry.invoke("create_text_file", {"filename": "a.txt", "content": "x"}, confirm=lambda r: True)
    assert not result.ok and "No approved" in result.error
    enable(services, output_directory=str(tmp_path / "out"))
    (tmp_path / "out").mkdir()
    for name in ["../escape.txt", ".env", "run.exe"]:
        r = services.registry.invoke("create_text_file", {"filename": name, "content": "x"}, confirm=lambda r: True)
        assert not r.ok, name
    assert not (tmp_path / "escape.txt").exists()


def test_confirmation_callback_exception_counts_as_decline(services, tmp_path):
    enable(services, "create_text_file", output_directory=str(tmp_path))

    def boom(request):
        raise RuntimeError("dialog crashed")

    result = services.registry.invoke("create_text_file", {"filename": "b.txt", "content": "x"}, confirm=boom)
    assert result.decision == "declined" and not (tmp_path / "b.txt").exists()


def test_open_application_requires_approved_name(services, tmp_path):
    launched = []

    class FakeProcess:
        pid = 4321

        def poll(self):
            return None  # still running

    def fake_popen(args, **kwargs):
        launched.append((args, kwargs))
        return FakeProcess()

    services.registry.get("open_application")._popen = fake_popen
    program = tmp_path / "editor.exe"
    program.write_text("")
    enable(services, "open_application", approved_applications={"Editor": str(program)})
    reg = services.registry
    assert not reg.invoke("open_application", {"name": "cmd"}, confirm=lambda r: True).ok
    assert not reg.invoke("open_application", {"name": "Editor", "args": "/c del"}, confirm=lambda r: True).ok
    result = reg.invoke("open_application", {"name": "editor"}, confirm=lambda r: True)
    assert result.ok and result.data == {"opened": "Editor", "process_id": 4321}
    assert launched == [([str(program)], launched[0][1])] and launched[0][1]["shell"] is False


def test_propose_file_change_shows_diff_and_backs_up(services, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("print('old')\n")
    services.config_store.update(lambda c: setattr(c.workspace, "project_directory", str(project)))
    enable(services, "propose_file_change")
    requests = []
    reg = services.registry
    declined = reg.invoke("propose_file_change", {"path": "app.py", "new_content": "print('new')\n"},
                          confirm=lambda r: requests.append(r) or False)
    assert not declined.ok and (project / "app.py").read_text() == "print('old')\n"
    assert "-print('old')" in requests[0].details and "+print('new')" in requests[0].details
    assert requests[0].details_are_diff and requests[0].destructive

    applied = reg.invoke("propose_file_change", {"path": "app.py", "new_content": "print('new')\n"}, confirm=lambda r: True)
    assert applied.ok and (project / "app.py").read_text() == "print('new')\n"
    backup = applied.data["backup"]
    assert backup and open(backup).read() == "print('old')\n"
    assert not reg.invoke("propose_file_change", {"path": "../outside.py", "new_content": "x"}, confirm=lambda r: True).ok
    assert not reg.invoke("propose_file_change", {"path": ".env", "new_content": "x"}, confirm=lambda r: True).ok


def test_workspace_tools(services, tmp_path):
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "core.py").write_text("def compute_total(x):\n    return x\n")
    (project / ".env").write_text("SECRET=1")
    reg = services.registry
    enable(services, "workspace_list_files", "workspace_read_file", "workspace_search_text")
    assert "No project workspace" in reg.invoke("workspace_list_files", {}).error
    services.config_store.update(lambda c: setattr(c.workspace, "project_directory", str(project)))
    assert reg.invoke("workspace_list_files", {}).data["files"] == ["src/core.py"]
    matches = reg.invoke("workspace_search_text", {"query": "compute_total"}).data["matches"]
    assert matches == [{"path": "src/core.py", "line": 1, "text": "def compute_total(x):"}]
    assert not reg.invoke("workspace_read_file", {"path": ".env"}).ok
    assert "compute_total" in reg.invoke("workspace_read_file", {"path": "src/core.py"}).data["content"]


def test_memory_and_reminder_tools_need_approval(services):
    enable(services, "save_memory", "create_reminder")
    reg = services.registry
    assert reg.invoke("save_memory", {"content": "likes tea", "category": "preference"}).decision == "declined"
    assert services.memory.count() == 0
    assert reg.invoke("save_memory", {"content": "likes tea", "category": "preference"}, confirm=lambda r: True).ok
    assert services.memory.count() == 1
    assert not reg.invoke("create_reminder", {"title": "x", "due": "tomorrow"}, confirm=lambda r: True).ok
    assert reg.invoke("create_reminder", {"title": "Call", "due": "2030-01-01T09:30"}, confirm=lambda r: True).ok
    assert services.reminders.list()[0].title == "Call"


def test_result_serialisation():
    from green.services.tool_registry import ToolResult

    payload = json.loads(ToolResult("t", False, error="nope", code="policy_violation").to_model_content())
    assert payload == {"tool": "t", "ok": False, "error": {"code": "policy_violation", "message": "nope"}}
