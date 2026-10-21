import pytest

from green.models import MemoryCategory
from green.services.memory_service import MemoryNotFoundError, keywords


def test_add_edit_search_delete(services):
    memory = services.memory
    rec = memory.add("Prefers dark theme in editors", "preference", ["UI", "ui", " editor "])
    assert rec.tags == ["ui", "editor"]
    assert rec.category is MemoryCategory.PREFERENCE
    updated = memory.update(rec.id, "Prefers dark themes everywhere", MemoryCategory.PREFERENCE, ["ui"])
    assert updated.content == "Prefers dark themes everywhere"
    assert updated.updated_at >= rec.updated_at
    memory.add("Project Falcon uses PostgreSQL 16", "project_note", ["falcon"])
    assert [r.content for r in memory.search("postgres")] == ["Project Falcon uses PostgreSQL 16"]
    assert len(memory.search(category="preference")) == 1
    assert memory.search("100%_") == []  # LIKE wildcards are escaped
    memory.delete(rec.id)
    with pytest.raises(MemoryNotFoundError):
        memory.get(rec.id)


def test_validation(services):
    with pytest.raises(ValueError):
        services.memory.add("   ", "fact")
    with pytest.raises(ValueError):
        services.memory.add("x", "not-a-category")
    with pytest.raises(ValueError):
        services.memory.add("x" * 5000, "fact")


def test_clear_all(services):
    for i in range(3):
        services.memory.add(f"fact {i}", "fact")
    assert services.memory.clear_all() == 3
    assert services.memory.count() == 0


def test_relevant_only_returns_matching_and_limits(services):
    memory = services.memory
    memory.add("My dog is called Biscuit", "fact")
    memory.add("Project Falcon deploys with Docker", "project_note", ["falcon"])
    memory.add("Always answer in British English", "instruction")
    memory.add("Favourite language is Rust", "preference")
    found = memory.relevant("How should I deploy the Falcon project?", limit=5)
    assert [r.content for r in found] == ["Project Falcon deploys with Docker"]
    assert memory.relevant("tell me", limit=5) == []  # only stopwords
    assert memory.relevant("falcon docker rust dog", limit=2).__len__() == 2
    assert memory.relevant("falcon", limit=0) == []


def test_source_conversation_set_null_on_delete(services):
    conv = services.conversations.create()
    rec = services.memory.add("summary", "summary", source_conversation_id=conv.id)
    assert rec.source_conversation_id == conv.id
    services.conversations.delete(conv.id)
    assert services.memory.get(rec.id).source_conversation_id is None
    assert services.memory.add("x", "fact", source_conversation_id="missing").source_conversation_id is None


def test_keywords():
    assert keywords("What is the deploy process for Falcon?") == ["deploy", "process", "falcon"]
