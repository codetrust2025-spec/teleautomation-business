"""Read-only discovery, not permission to replay historical mail."""
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from services.calendar_invite_parser import parse_calendar
from services.recruitment_identity import resolve


def classify_records(records, *, calendars, slots, audits, links, now=None):
    now = now or datetime.now(timezone.utc)
    owners = {a['booking_id']: resolve(a['candidate_id'], links) for a in audits if a.get('booking_id')}
    parsed = []
    for source in calendars:
        calendar = parse_calendar(source.get('extracted_text') or '')
        if calendar:
            parsed.append((source, calendar))
    output = []
    for source in records:
        row = dict(source)
        person = resolve(row.get('canonical_candidate_id') or '', links)
        row['canonical_candidate_id'] = person
        matches = [cal for m, cal in parsed if m['mailbox_message_id'] == row['mailbox_message_id']]
        row['discovery_state'] = 'UNRESOLVED'
        row['reason'] = 'Calendar evidence missing or ambiguous; no automatic recovery authorized.'
        if len(matches) == 1:
            cal = matches[0]
            uid = str(cal.get('uid') or '').casefold()
            start, end = cal.get('start'), cal.get('end')
            row.update(calendar_uid=cal.get('uid'), calendar_sequence=cal.get('sequence'),
                       start_ist=start.astimezone(ZoneInfo('Asia/Kolkata')).isoformat() if start else None,
                       end_ist=end.astimezone(ZoneInfo('Asia/Kolkata')).isoformat() if end else None)
            cancelled = cal.get('method') == 'CANCEL' or cal.get('status') == 'CANCELLED'
            superseded = any(
                uid and str(other.get('uid') or '').casefold() == uid
                and m['mailbox_id'] == row['mailbox_id']
                and (other['sequence'] > cal['sequence'] or
                     other['sequence'] == cal['sequence'] and
                     (other.get('method') == 'CANCEL' or other.get('status') == 'CANCELLED'))
                for m, other in parsed
            )
            same_interview = [s for s in slots if s.get('slot_confirmed')
                         and owners.get(s['id'], resolve(s.get('canonical_candidate_id') or s['id'], links)) == person
                         and uid and str(s.get('interview_calendar_uid') or '').casefold() == uid]
            def version(slot):
                try:
                    return int(slot.get('interview_calendar_sequence') or 0)
                except (TypeError, ValueError):
                    return -1  # ambiguous metadata is never proof of persistence
            superseded = superseded or any(version(s) > cal['sequence'] for s in same_interview)
            local_start = start.astimezone(ZoneInfo('Asia/Kolkata')) if start else None
            local_end = end.astimezone(ZoneInfo('Asia/Kolkata')) if end else None
            persisted = [s for s in same_interview if version(s) == cal['sequence'] and local_start and local_end
                         and str(s.get('date') or '')[:10] == local_start.date().isoformat()
                         and str(s.get('time') or '')[:5] == local_start.strftime('%H:%M')
                         and str(s.get('time_end') or '')[:5] == local_end.strftime('%H:%M')]
            if cancelled or superseded:
                row.update(discovery_state='STALE_OR_CANCELLED', reason='Cancelled or superseded calendar revision.')
            elif persisted:
                row.update(discovery_state='ALREADY_REPRESENTED', booking_ids=[s['id'] for s in persisted],
                           reason='Confirmed persisted slot matches person, UID, revision and exact schedule.')
            elif start and start <= now:
                row.update(discovery_state='STALE_OR_CANCELLED', reason='Interview start is in the past.')
            elif start and end and uid:
                row.update(discovery_state='RECOVERY_CANDIDATE',
                           reason='Future calendar not represented by a confirmed slot; AI, payment and lifecycle checks still required.')
        output.append(row)
    counts = Counter(r['discovery_state'] for r in output)
    return {'summary': {'total': len(output), 'recovery_candidates': counts['RECOVERY_CANDIDATE'],
                       'already_represented': counts['ALREADY_REPRESENTED'],
                       'stale_or_cancelled': counts['STALE_OR_CANCELLED'],
                       'already_assessed': 0, 'unresolved': counts['UNRESOLVED']}, 'records': output}


def load_report(*, limit=500):
    from core.db.connection import get_connection
    from core.recruitment_mail_store import _CALENDAR_FALSE_IGNORE_REASONS, _rows
    from features import candidate_store
    slots = list(candidate_store._load(force=True).get('candidates') or [])
    cap = max(1, min(limit, 2000))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cur.execute('SELECT alias_candidate_id,canonical_candidate_id FROM candidate_identity_links')
        links = dict(cur.fetchall())
        cur.execute("""SELECT m.id AS mailbox_message_id,m.mailbox_id,m.provider_message_id,
                 m.subject,m.sent_at,m.ignore_reason,m.processing_status,
                 b.candidate_id AS canonical_candidate_id
            FROM mailbox_messages m JOIN candidate_mailboxes b ON b.id=m.mailbox_id
            WHERE m.processing_status IN ('AUTO_IGNORE','IGNORED_NOT_OFFER_RELATED','IGNORED_LOW_CONFIDENCE')
              AND m.ignore_reason=ANY(%s)
              AND EXISTS(SELECT 1 FROM mailbox_attachments a WHERE a.mailbox_message_id=m.id
                         AND lower(COALESCE(a.filename,'')) LIKE '%%.ics')
            ORDER BY m.sent_at,m.id LIMIT %s""", (list(_CALENDAR_FALSE_IGNORE_REASONS), cap + 1))
        records = _rows(cur)
        complete = len(records) <= cap
        records = records[:cap]
        # Include source cancellations/reschedules, even if AI ignored those too.
        cur.execute("""SELECT m.id AS mailbox_message_id,m.mailbox_id,c.extracted_text
            FROM mailbox_messages m JOIN mailbox_attachments a ON a.mailbox_message_id=m.id
            JOIN mailbox_attachment_cache c ON c.checksum=a.checksum
            WHERE m.mailbox_id=ANY(%s) AND lower(COALESCE(a.filename,'')) LIKE '%%.ics'""",
            (list({r['mailbox_id'] for r in records}),))
        calendars = _rows(cur)
        cur.execute('SELECT booking_id,candidate_id FROM interview_auto_booking_audit WHERE booking_id=ANY(%s)',
                    ([s['id'] for s in slots if s.get('slot_confirmed')],))
        audits = _rows(cur)
    report = classify_records(records, calendars=calendars, slots=slots, audits=audits, links=links)
    report['coverage'] = {'complete': complete}
    return report
