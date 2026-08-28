"""Rescan a date range of candidate mail and recover missed Selection alerts.

Run inside the Operations container, where DATABASE_URL and the Ollama routing
are the ones production uses:

    docker compose exec api python -m scripts.rescan_selection_alerts \
        --range-start 2026-08-01 --range-end 2026-08-28 --actor admin

Defaults to the current month up to today, which is what "rescan this month"
means. `--report-only` skips queueing and reports the range as it stands now,
which is the safe way to see where a previous run got to.

The rescan itself is the pipeline's own historical rescan job, one per mailbox:
the same Gmail fetch, the same Ollama classification, the same alert and
real-time event writes as a live message. This script queues them, waits for the
AI queue to drain, then verifies -- per alert -- that a `notification_created`
event exists, because that event is what makes the browser play the sound.

It exits non-zero when any new alert could not be given one, or when the range
did not finish. A rescan that leaves an alert silent has not done its job, and
saying so in the exit status keeps that from being read as success.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from core import recruitment_mail_store as store
from services import selection_rescan

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("selection_rescan_cli")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Dates must use YYYY-MM-DD: {value!r}") from exc


def _report_only(range_start: date, range_end: date, actor: str) -> dict:
    """Report the range without queueing anything.

    The baseline is deliberately empty so every selection alert in the range is
    listed and checked for a sound event. This is how a previous run's silent
    alerts are found and given one.
    """
    started_at = datetime.now(timezone.utc)
    return selection_rescan.build_report(
        range_start=range_start,
        range_end=range_end,
        baseline=set(),
        queue_result={"queued": [], "failed": []},
        wait_result={"jobs": 0, "outstanding": 0, "timed_out": False, "by_status": {}},
        started_at=started_at,
        actor=actor,
    )


def _print_summary(report: dict) -> None:
    totals = report["totals"]
    print("\n================ AUGUST-STYLE SELECTION RESCAN ================")
    print(f"Range                            {report['range_start']} .. {report['range_end']}")
    print(f"Candidates scanned               {totals['candidates_scanned']}")
    print(f"Mailboxes rescanned              {totals['mailboxes_rescanned']}")
    print(f"Emails scanned                   {totals['emails_scanned']}")
    print(f"Selection Related detected       {totals['selection_emails_detected']}")
    print(f"New alerts created               {totals['new_alerts_created']}")
    print(f"Duplicates skipped               {totals['duplicates_skipped']}")
    print(f"Non-selection emails rejected    {totals['non_selection_emails_rejected']}")
    print(f"Sound notifications triggered    {totals['sound_notifications_triggered']}")
    print(f"Sound notification failures      {totals['sound_notification_failures']}")
    print(f"Could not be classified          {totals['unclassified_emails']}")
    print(f"Interview alerts left untouched  {totals['interview_alerts_in_range_untouched']}")

    if report["created_alerts"]:
        print("\n--- Created alerts (candidate | subject | detected status | sound) ---")
        for alert in report["created_alerts"]:
            print(
                f"  {alert['candidate_name'] or alert['candidate_id']} | "
                f"{(alert['email_subject'] or '')[:70]} | "
                f"{alert['detected_status']} | {alert['sound']}"
            )

    if report["sound_failures"]:
        print("\n--- NOTIFICATION DELIVERY INCOMPLETE (alert created, no sound) ---")
        for entry in report["sound_failures"]:
            print(
                f"  {entry.get('candidate_name') or entry.get('candidate_id')} | "
                f"{(entry.get('email_subject') or '')[:70]} | {entry.get('error', 'no event recorded')}"
            )

    if report["unclassified_emails"]:
        print("\n--- Could not be classified confidently ---")
        for row in report["unclassified_emails"]:
            print(
                f"  {row['mailbox']} | {(row['email_subject'] or '')[:70]} | "
                f"{row['processing_status']} | {row['reason'] or ''}"
            )

    screen = report["selection_screen"]
    print(
        f"\nMail Alerts -> Selection Related: {screen['visible']}/{screen['expected']} "
        f"recovered alerts visible -- {'CONFIRMED' if screen['confirmed'] else 'NOT CONFIRMED'}"
    )
    print(f"Run successful: {report['successful']}")


def main(argv: list[str] | None = None) -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--range-start", type=_parse_date, default=today.replace(day=1))
    parser.add_argument("--range-end", type=_parse_date, default=today)
    parser.add_argument("--actor", default="system")
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--poll-seconds", type=int, default=15)
    parser.add_argument(
        "--report-only", action="store_true",
        help="Do not queue a rescan; report the range as it stands and deliver any missing sounds.",
    )
    parser.add_argument("--out", type=Path, default=Path("selection_rescan_report.json"))
    args = parser.parse_args(argv)

    if args.range_start > args.range_end:
        parser.error("--range-start must not be after --range-end")
    if not store.use_postgres():
        parser.error("DATABASE_URL is not configured; run this inside the Operations container")

    if args.report_only:
        report = _report_only(args.range_start, args.range_end, args.actor)
    else:
        report = selection_rescan.run(
            range_start=args.range_start,
            range_end=args.range_end,
            actor=args.actor,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )

    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _print_summary(report)
    print(f"\nFull report written to {args.out}")
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    sys.exit(main())
