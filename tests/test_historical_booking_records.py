"""Historical booking notes: recorded, idempotent, and never a live booking."""
import pytest

from features import candidate_store as cs
from features import historical_booking_records as hbr


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORICAL_BOOKING_RECORDS_FILE", str(tmp_path / "h.json"))
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)


CHARAN = dict(
    candidate_id="694ad25a72",
    candidate_name="Ram Charan M S",
    disposition=hbr.HISTORICAL_CONFLICT_NOT_RESTORED,
    occurred_on="2026-08-06",
    scheduled_time="17:30-18:00 Asia/Kolkata",
    company="Capgemini",
    summary="L1 React interview cancelled in error and never restored.",
    reason_not_restored=(
        "Restoring would have double-booked the interviewer against Surekha G, "
        "who was auto-assigned the freed slot 55 minutes later. The administrator "
        "chose to hold both rather than bypass the conflict gate, and the "
        "interview time passed."
    ),
    reviewer="administrator",
    calendar_uids={
        "cancelled_by": "CDVCAPGB@50250631444abd548e03c35cff206722",
        "affected": "0400...096F06FC596A12409713ABBDB5CE1ADC",
    },
    booking_ids={"affected": "694ad25a72", "conflicting": "b5f34bd4d7"},
    gmail_message_ids={"invite": "19fd1ef5e524a901", "cancellation": "19fd1ef9112ba499"},
    conflicts_with={"candidate": "Surekha G", "booking_id": "b5f34bd4d7"},
)


def test_a_historical_record_captures_the_full_story():
    out = hbr.record(**CHARAN)
    assert out["created"] is True
    entry = out["record"]
    assert entry["disposition"] == "HISTORICAL_CONFLICT_NOT_RESTORED"
    assert entry["calendar_uids"]["cancelled_by"].startswith("CDVCAPGB@")
    assert entry["booking_ids"]["affected"] == "694ad25a72"
    assert entry["gmail_message_ids"]["cancellation"] == "19fd1ef9112ba499"
    assert entry["conflicts_with"]["candidate"] == "Surekha G"
    assert "double-booked" in entry["reason_not_restored"]


def test_recording_the_same_incident_twice_is_a_no_op():
    first = hbr.record(**CHARAN)
    second = hbr.record(**CHARAN)
    assert first["created"] is True
    assert second["created"] is False
    assert len(hbr.records()) == 1


def test_an_unknown_disposition_is_refused():
    with pytest.raises(ValueError, match="Unknown disposition"):
        hbr.record(**{**CHARAN, "disposition": "MADE_UP"})


def test_records_can_be_filtered_by_candidate():
    hbr.record(**CHARAN)
    hbr.record(**{**CHARAN, "candidate_id": "other", "candidate_name": "Someone",
                  "idempotency_key": "other-key"})
    assert len(hbr.records(candidate_id="694ad25a72")) == 1
    assert len(hbr.records()) == 2


def test_recording_history_creates_no_booking_and_touches_no_candidate():
    surekha = cs.create_candidate({"name": "Surekha G", "phone": "9000000201"})
    data = cs._load()
    for item in data["candidates"]:
        if item["id"] == surekha["id"]:
            item["date"] = "2026-08-06"
            item["time"] = "17:30"
            item["time_end"] = "18:00"
            item["slot_confirmed"] = True
    cs._save(data)
    before = cs._load()

    hbr.record(**CHARAN)

    after = cs._load()
    assert after == before, "a historical note must not alter any candidate row"
    still = cs.get_candidate(surekha["id"])
    assert still["date"] == "2026-08-06"
    assert still["time"] == "17:30"
    assert still["slot_confirmed"] is True


def test_no_confirmed_slot_is_created_for_the_recorded_incident():
    hbr.record(**CHARAN)
    rows = cs._load().get("candidates") or []
    assert not [r for r in rows if r.get("id") == "694ad25a72"], (
        "the historical record must not resurrect the cancelled booking row"
    )
