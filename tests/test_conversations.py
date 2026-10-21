import pytest

from green.app.bootstrap import build_services
from green.services.conversation_service import DEFAULT_TITLE, ConversationNotFoundError


def test_create_list_rename_delete(services):
    conv = services.conversations
    a = conv.create()
    b = conv.create("Second")
    assert a.title == DEFAULT_TITLE
    assert [c.id for c in conv.list()][0] == b.id
    conv.rename(a.id, "  Renamed   chat ")
    assert conv.get(a.id).title == "Renamed chat"
    with pytest.raises(ValueError):
        conv.rename(a.id, "   ")
    conv.delete(a.id)
    with pytest.raises(ConversationNotFoundError):
        conv.get(a.id)
    with pytest.raises(ConversationNotFoundError):
        conv.delete(a.id)


def test_messages_persist_and_title_auto_set(services):
    conv = services.conversations
    c = conv.create()
    conv.add_message(c.id, "user", "How do I read a CSV file in Python with headers?")
    conv.add_message(c.id, "assistant", "Use the csv module.", metadata={"model": "x"})
    messages = conv.messages(c.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].metadata == {"model": "x"}
    assert conv.get(c.id).title.startswith("How do I read a CSV")
    assert len(conv.get(c.id).title) <= 48


def test_invalid_role_and_missing_conversation(services):
    conv = services.conversations
    c = conv.create()
    with pytest.raises(ValueError):
        conv.add_message(c.id, "system", "x")
    with pytest.raises(ConversationNotFoundError):
        conv.add_message("nope", "user", "x")


def test_delete_cascades_messages(services):
    conv = services.conversations
    c = conv.create()
    conv.add_message(c.id, "user", "hi")
    conv.delete(c.id)
    assert services.database.query_one("SELECT COUNT(*) FROM messages")[0] == 0


def test_recent_messages_window(services):
    conv = services.conversations
    c = conv.create()
    for i in range(10):
        conv.add_message(c.id, "user", f"m{i}")
    assert [m.content for m in conv.recent_messages(c.id, 3)] == ["m7", "m8", "m9"]


def test_conversations_survive_restart(services_factory, app_paths):
    first = services_factory()
    c = first.conversations.create()
    first.conversations.add_message(c.id, "user", "remember this chat")
    first.shutdown()
    second = build_services(app_paths, configure_logs=False)
    try:
        assert [m.content for m in second.conversations.messages(c.id)] == ["remember this chat"]
    finally:
        second.shutdown()
