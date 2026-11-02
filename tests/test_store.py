from app.store import InMemoryHistoryStore


def test_doppeltes_event_wird_nur_einmal_gespeichert(make_entry):
    store = InMemoryHistoryStore()
    assert store.add(make_entry("e1")) is True
    assert store.add(make_entry("e1")) is False
    assert len(store.list_for_user("user-a")) == 1


def test_history_ist_pro_benutzer_getrennt(make_entry):
    store = InMemoryHistoryStore()
    store.add(make_entry("e1", owner="user-a"))
    store.add(make_entry("e2", owner="user-b"))
    assert [e.event_id for e in store.list_for_user("user-a")] == ["e1"]


def test_neueste_zuerst_auch_ueber_zeitzonen(make_entry):
    store = InMemoryHistoryStore()
    store.add(make_entry("alt", occurred_at="2026-10-01T10:00:00+02:00"))  # 08:00 UTC
    store.add(make_entry("neu", occurred_at="2026-10-01T09:00:00.000Z"))   # 09:00 UTC
    assert [e.event_id for e in store.list_for_user("user-a")] == ["neu", "alt"]
