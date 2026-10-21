from datetime import datetime, timedelta, timezone

import pytest


def test_reminder_lifecycle(services):
    rem = services.reminders
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    a = rem.create("Stand-up", past, "daily")
    b = rem.create("Dentist", future)
    assert [r.id for r in rem.due()] == [a.id]
    rem.mark_notified(a.id)
    assert rem.due() == []
    rem.update(a.id, "Stand-up", past - timedelta(minutes=1))
    assert [r.id for r in rem.due()] == [a.id]  # rescheduling re-arms
    rem.set_completed(a.id)
    assert rem.due() == []
    assert [r.id for r in rem.list(include_completed=False)] == [b.id]
    rem.delete(b.id)
    with pytest.raises(LookupError):
        rem.get(b.id)


def test_naive_datetimes_are_local(services):
    local = datetime.now().replace(microsecond=0) + timedelta(hours=2)
    r = services.reminders.create("Call", local)
    assert r.due_at.astimezone().replace(tzinfo=None) == local


def test_validation(services):
    with pytest.raises(ValueError):
        services.reminders.create("   ", datetime.now(timezone.utc))
