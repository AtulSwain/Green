import json
import threading

from green.services.ollama_service import CancelToken

from .fake_ollama import text_chunks, tool_call_chunks


def start_conversation(services, text):
    conv = services.conversations.create()
    services.conversations.add_message(conv.id, "user", text)
    return conv.id


def test_simple_turn_is_persisted(services, fake_ollama):
    fake_ollama.chat_script = [text_chunks("<think>hmm</think>Paris is the capital.")]
    conv = start_conversation(services, "Capital of France?")
    updates = []
    result = services.engine.generate(conv, CancelToken(), on_text=updates.append)
    assert result.status == "complete" and result.text == "Paris is the capital."
    assert updates and updates[-1] == "Paris is the capital."
    stored = services.conversations.messages(conv)
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[1].content == "Paris is the capital." and stored[1].metadata["model"] == "qwen3:8b"
    sent = fake_ollama.requests[-1]["body"]["messages"]
    assert sent[0]["role"] == "system" and sent[-1] == {"role": "user", "content": "Capital of France?"}


def test_tool_call_round_trip(services, fake_ollama):
    fake_ollama.chat_script = [tool_call_chunks("get_datetime", {}), text_chunks("It is Tuesday.")]
    conv = start_conversation(services, "What day is it?")
    result = services.engine.generate(conv, CancelToken())
    assert result.status == "complete" and result.text == "It is Tuesday."
    record = result.tools_used[0]
    assert (record["tool"], record["ok"], record["decision"]) == ("get_datetime", True, "allowed")
    assert record["description"] == "Checking the current date and time"
    second = fake_ollama.requests[-1]["body"]["messages"]
    tool_msg = second[-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["ok"] is True
    chat_requests = [r for r in fake_ollama.requests if r["path"] == "/api/chat"]
    assert {t["function"]["name"] for t in chat_requests[0]["body"]["tools"]} == {"get_datetime", "get_app_info", "get_system_info", "calculate", "create_reminder", "save_memory"}


def test_malformed_tool_calls_do_not_crash(services, fake_ollama):
    bad_calls = [
        {"message": {"content": "", "tool_calls": ["not a dict", {"function": "nope"},
                                                    {"function": {"name": "get_datetime", "arguments": "{broken"}},
                                                    {"function": {"name": "rm_rf", "arguments": {}}},
                                                    {"function": {"name": "read_text_file", "arguments": {"path": "C:/x"}}}]},
         "done": False},
        {"message": {"content": ""}, "done": True},
    ]
    fake_ollama.chat_script = [bad_calls, text_chunks("Sorry, I could not do that.")]
    conv = start_conversation(services, "do things")
    result = services.engine.generate(conv, CancelToken())
    assert result.status == "complete"
    assert all(not t["ok"] for t in result.tools_used)
    decisions = [t["decision"] for t in result.tools_used]
    assert "denied" in decisions and "rejected" in decisions
    tool_messages = [m for m in fake_ollama.requests[-1]["body"]["messages"] if m["role"] == "tool"]
    assert {json.loads(m["content"])["error"]["code"] for m in tool_messages} >= {"unknown_tool", "permission_denied", "invalid_arguments"}
    assert len(tool_messages) == 5


def test_tool_step_limit(services, fake_ollama):
    services.config_store.update(lambda c: setattr(c.context, "max_tool_rounds", 1))
    fake_ollama.chat_script = [tool_call_chunks("get_datetime", {}), text_chunks("It is Monday.")]
    conv = start_conversation(services, "loop forever")
    result = services.engine.generate(conv, CancelToken())
    assert result.status == "complete" and result.text.startswith("It is Monday.") and "limit" in result.text
    chats = [r["body"] for r in fake_ollama.requests if r["path"] == "/api/chat"]
    assert len(chats) == 2 and "tools" not in chats[1]  # final turn gets no tools


def test_write_tool_uses_confirmation_callback(services, fake_ollama, tmp_path):
    services.config_store.update(lambda c: (
        c.tools.enabled_tools.__setitem__("create_text_file", True),
        setattr(c.tools, "output_directory", str(tmp_path)),
    ))
    fake_ollama.chat_script = [tool_call_chunks("create_text_file", {"filename": "n.txt", "content": "x"}), text_chunks("Done.")]
    conv = start_conversation(services, "make a file")
    asked = []
    result = services.engine.generate(conv, CancelToken(), confirm=lambda r: asked.append(r) or False)
    assert asked and not (tmp_path / "n.txt").exists()
    assert result.tools_used[0]["decision"] == "declined"


def test_model_without_tool_support_falls_back(services, fake_ollama):
    fake_ollama.chat_script = [("error", 400, "registry.ollama.ai/library/gemma does not support tools"), text_chunks("Plain answer")]
    conv = start_conversation(services, "hi")
    result = services.engine.generate(conv, CancelToken())
    assert result.status == "complete" and result.text == "Plain answer"
    assert "tools" not in fake_ollama.requests[-1]["body"]


def test_cancellation_saves_partial(services, fake_ollama):
    fake_ollama.chat_script = [("slow", text_chunks("word " * 200, size=5), 0.01)]
    conv = start_conversation(services, "long answer please")
    cancel = CancelToken()

    def on_text(text):
        if len(text) > 20:
            cancel.cancel()

    result = services.engine.generate(conv, cancel, on_text=on_text)
    assert result.status == "cancelled" and result.text
    stored = services.conversations.messages(conv)[-1]
    assert stored.role == "assistant" and stored.status == "cancelled"


def test_connection_error_reports_and_keeps_user_message(services_factory, closed_port_url):
    services = services_factory(closed_port_url)
    conv = start_conversation(services, "hello?")
    result = services.engine.generate(conv, CancelToken())
    assert result.status == "error" and "Cannot reach Ollama" in result.error
    assert [m.role for m in services.conversations.messages(conv)] == ["user"]


def test_only_relevant_memories_are_sent(services, fake_ollama):
    services.memory.add("The Falcon project deploys with Docker", "project_note")
    services.memory.add("My cat is named Pixel", "fact")
    conv = start_conversation(services, "How do I deploy Falcon?")
    services.engine.generate(conv, CancelToken())
    system = fake_ollama.requests[-1]["body"]["messages"][0]["content"]
    assert "Falcon project deploys with Docker" in system
    assert "Pixel" not in system


def test_history_is_limited(services, fake_ollama):
    services.config_store.update(lambda c: setattr(c.context, "history_messages", 4))
    conv = services.conversations.create()
    for i in range(10):
        services.conversations.add_message(conv.id, "user" if i % 2 == 0 else "assistant", f"message {i}")
    services.engine.generate(conv.id, CancelToken())
    sent = fake_ollama.requests[-1]["body"]["messages"]
    assert [m["content"] for m in sent[1:]] == ["message 6", "message 7", "message 8", "message 9"]


def test_summarize(services, fake_ollama):
    fake_ollama.chat_script = [text_chunks("- Decided to use SQLite")]
    conv = start_conversation(services, "Let's use SQLite")
    services.conversations.add_message(conv, "assistant", "Sounds good")
    assert services.engine.summarize(conv, CancelToken()) == "- Decided to use SQLite"


def test_generation_runs_off_thread(services, fake_ollama):
    """The engine has no Qt dependency and can run on any thread."""
    conv = start_conversation(services, "hi")
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("r", services.engine.generate(conv, CancelToken())))
    thread.start()
    thread.join(10)
    assert out["r"].status == "complete"


def test_follow_up_request_contains_only_well_formed_tool_calls(services, fake_ollama):
    bad = [{"message": {"content": "", "tool_calls": ["junk", {"function": {"name": "get_datetime", "arguments": "{oops"}}]}, "done": True}]
    fake_ollama.chat_script = [bad, text_chunks("ok")]
    conv = start_conversation(services, "x")
    services.engine.generate(conv, CancelToken())
    assistant = [m for m in fake_ollama.requests[-1]["body"]["messages"] if m["role"] == "assistant"][-1]
    for call in assistant["tool_calls"]:
        assert isinstance(call["function"]["name"], str) and isinstance(call["function"]["arguments"], dict)
