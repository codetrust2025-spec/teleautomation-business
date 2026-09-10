from core import recruitment_mail_store as store

from datetime import datetime, timezone
from services.calendar_recovery_discovery import classify_records


def classify(*, slots=(), audits=(), extra=(), date='20260911', method='REQUEST'):
    row = dict(mailbox_message_id='m', mailbox_id='box', canonical_candidate_id='alias')
    cal = dict(row, extracted_text=f'BEGIN:VCALENDAR\nMETHOD:{method}\nBEGIN:VEVENT\nUID:uid\nSEQUENCE:0\nDTSTART:{date}T090000Z\nDTEND:{date}T100000Z\nEND:VEVENT\nEND:VCALENDAR')
    return classify_records([row], calendars=[cal, *extra], slots=slots, audits=audits,
                            links={'alias': 'person'}, now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))['records'][0]


def test_audit_alone_does_not_prove_a_persisted_booking():
    row = classify(audits=[dict(booking_id='missing', candidate_id='person', auto_booked=True)])
    assert row['discovery_state'] == 'RECOVERY_CANDIDATE'
    assert row['canonical_candidate_id'] == 'person'
    assert row['start_ist'] == '2026-09-11T14:30:00+05:30'


def test_persisted_slot_must_match_uid_and_person():
    slots = [dict(id='s', slot_confirmed=True, interview_calendar_uid='uid', date='2026-09-11', time='14:30', time_end='15:30')]
    assert classify(slots=slots, audits=[dict(booking_id='s', candidate_id='alias')])['discovery_state'] == 'ALREADY_REPRESENTED'
    assert classify(slots=slots, audits=[dict(booking_id='s', candidate_id='other')])['discovery_state'] == 'RECOVERY_CANDIDATE'


def test_matching_uid_but_wrong_persisted_schedule_is_not_already_represented():
    slots = [dict(id='s', slot_confirmed=True, interview_calendar_uid='uid', date='2026-09-11', time='12:00', time_end='13:00')]
    assert classify(slots=slots, audits=[dict(booking_id='s', candidate_id='alias')])['discovery_state'] == 'RECOVERY_CANDIDATE'


def test_past_source_calendar_is_not_a_recovery_candidate():
    assert classify(date='20260909')['discovery_state'] == 'STALE_OR_CANCELLED'


def test_unprocessed_source_cancellation_supersedes_ignored_confirmation():
    cancellation = dict(mailbox_message_id='cancel', mailbox_id='box', extracted_text='BEGIN:VCALENDAR\nMETHOD:CANCEL\nBEGIN:VEVENT\nUID:uid\nSEQUENCE:1\nEND:VEVENT\nEND:VCALENDAR')
    assert classify(extra=[cancellation])['discovery_state'] == 'STALE_OR_CANCELLED'
    assert classify(method='CANCEL')['discovery_state'] == 'STALE_OR_CANCELLED'


def test_other_mailbox_cancellation_does_not_supersede_persons_invite():
    cancellation = dict(mailbox_message_id='cancel', mailbox_id='other-box', extracted_text='BEGIN:VCALENDAR\nMETHOD:CANCEL\nBEGIN:VEVENT\nUID:uid\nSEQUENCE:1\nEND:VEVENT\nEND:VCALENDAR')
    assert classify(extra=[cancellation])['discovery_state'] == 'RECOVERY_CANDIDATE'


def test_calendar_recovery_discovery_is_read_only_without_postgres(monkeypatch):
    monkeypatch.setattr(store, "use_postgres", lambda: False)

    report = store.calendar_invite_recovery_discovery()

    assert report == {
        "summary": {
            "total": 0,
            "recovery_candidates": 0,
            "already_represented": 0,
            "stale_or_cancelled": 0,
            "already_assessed": 0,
        },
        "records": [],
    }
