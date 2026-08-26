"""Gmail push watches must renew themselves before Google expires them.

A watch lasts seven days.  Every existing call site that registered one was a
request handler - connect a mailbox, update it, or an operator pressing renew -
and the only scheduled service in the app was the interview reminder loop.  So
in a steady state nothing re-registered a watch, and push delivery stopped a
week after each connect.

Nothing surfaced that.  `core.gmail_watch` keeps the polling fallback running,
so mail kept arriving on the scheduler path and no error was ever raised; the
system silently degraded to polling-only.  Verified against production before
this change - all 15 connected mailboxes carried watches expiring on the same
day, and no scheduled renewal existed anywhere in the codebase.

The first test is the actual regression: it asserts a *scheduled* caller
exists.  The renewal function was already correct and already reachable through
its HTTP endpoint; what was missing was anything calling it on a timer.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import gmail_watch
from services import gmail_watch_renewal_loop

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
MAIN_PY = Path(__file__).resolve().parent.parent / "main.py"
TOPIC = "projects/p/topics/t"


def _mailbox(mailbox_id: str, **over) -> dict:
    return {
        "id": mailbox_id,
        "credential_ciphertext": "cipher-" + mailbox_id,
        "provider_history_id": "",
        "sync_cursor": "",
        **over,
    }


def _provider(result=None, fails_for=()):
    """A stand-in for GmailMailboxProvider keyed on the ciphertext it is given."""

    class Provider:
        def __init__(self, cipher):
            self.cipher = cipher

        def start_watch(self, topic):
            if self.cipher in fails_for:
                raise RuntimeError("invalid_grant")
            return result or {"historyId": "900", "expiration": "1790000000000"}

    return Provider


def _handler_calls(func_name: str) -> set[str]:
    """Names called inside main.py's `func_name`, read from the source tree.

    Deliberately an AST assertion rather than an import: the property under test
    is a wiring one - does the startup path invoke the starter at all - and the
    bug was precisely that a correct function had no caller.  Importing main.py
    would run migrations and build the entire app to learn the same thing.
    """
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == func_name:
            return {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
    raise AssertionError("main.py has no %s() handler" % func_name)


def test_something_actually_starts_the_renewal_on_a_schedule():
    """The regression. A renewal nothing calls is the bug, not the fix."""
    assert "start_gmail_watch_renewal_loop" in _handler_calls("startup")


def test_the_loop_is_stopped_on_shutdown():
    """Otherwise the task outlives the app and logs into a closed loop."""
    assert "stop_gmail_watch_renewal_loop" in _handler_calls("shutdown")


def test_a_watch_is_renewed_before_it_expires_not_after(monkeypatch):
    """Renewal must lead expiry. A watch renewed only once it has already
    lapsed has, by definition, already dropped notifications."""
    seen: dict = {}
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)

    def capture(*, before, limit):
        seen["before"] = before
        seen["limit"] = limit
        return []

    monkeypatch.setattr(gmail_watch.store, "mailboxes_due_for_watch_renewal", capture)
    gmail_watch.renew_due_watches(now=NOW)

    assert seen["before"] > NOW, "must select watches that have not expired yet"
    assert seen["before"] == NOW + gmail_watch.RENEWAL_LEAD
    # A day of lead against a seven-day lifetime: room to retry, no churn.
    assert timedelta(hours=1) < gmail_watch.RENEWAL_LEAD < gmail_watch.WATCH_LIFETIME


def test_one_revoked_mailbox_does_not_block_the_rest(monkeypatch):
    """The point of a fleet-wide renewal: the other mailboxes must not be held
    hostage by the one whose owner revoked access."""
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)
    monkeypatch.setattr(
        gmail_watch.store,
        "mailboxes_due_for_watch_renewal",
        lambda **_kwargs: [_mailbox("good-1"), _mailbox("revoked"), _mailbox("good-2")],
    )
    monkeypatch.setattr(
        gmail_watch, "GmailMailboxProvider", _provider(fails_for=("cipher-revoked",))
    )
    renewed: list[str] = []
    monkeypatch.setattr(
        gmail_watch.store, "update_mailbox", lambda mid, values: renewed.append(mid) or {}
    )

    result = gmail_watch.renew_due_watches(now=NOW)

    assert result == {"due": 3, "renewed": 2, "failed": 1}
    assert renewed == ["good-1", "good-2"]


def test_a_failed_renewal_leaves_the_polling_fallback_alone(monkeypatch):
    """Push is an optimisation over polling. A mailbox whose watch cannot be
    renewed must keep syncing, so renewal may never touch its health fields."""
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)
    monkeypatch.setattr(
        gmail_watch.store,
        "mailboxes_due_for_watch_renewal",
        lambda **_kwargs: [
            _mailbox("revoked", monitoring_enabled=True, connection_status="CONNECTED")
        ],
    )
    monkeypatch.setattr(
        gmail_watch, "GmailMailboxProvider", _provider(fails_for=("cipher-revoked",))
    )
    monkeypatch.setattr(
        gmail_watch.store,
        "update_mailbox",
        lambda *a, **k: pytest.fail("a failed renewal must not write to the mailbox"),
    )

    assert gmail_watch.renew_due_watches(now=NOW) == {"due": 1, "renewed": 0, "failed": 1}


def test_renewal_does_not_move_an_active_sync_cursor(monkeypatch):
    """Gmail returns a historyId on every watch call. Adopting it would rewind
    or fast-forward a mailbox that is already syncing, and skip messages."""
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)
    written: dict = {}
    monkeypatch.setattr(
        gmail_watch,
        "GmailMailboxProvider",
        _provider({"historyId": "999999", "expiration": "1790000000000"}),
    )
    monkeypatch.setattr(
        gmail_watch.store, "update_mailbox", lambda mid, values: written.update(values) or {}
    )

    gmail_watch.renew_watch(_mailbox("mb1", sync_cursor="12345", provider_history_id="12345"))

    assert written["sync_cursor"] == "12345", "an established cursor must survive renewal"
    assert written["provider_history_id"] == "12345"
    assert written["gmail_watch_topic"] == TOPIC
    assert written["gmail_watch_expiration"] == datetime.fromtimestamp(
        1790000000, tz=timezone.utc
    )


def test_a_mailbox_with_no_cursor_is_seeded_from_the_watch(monkeypatch):
    """The other half of the same rule: a new mailbox has nothing to preserve,
    so Gmail's historyId is the correct starting point."""
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)
    written: dict = {}
    monkeypatch.setattr(
        gmail_watch,
        "GmailMailboxProvider",
        _provider({"historyId": "555", "expiration": "1790000000000"}),
    )
    monkeypatch.setattr(
        gmail_watch.store, "update_mailbox", lambda mid, values: written.update(values) or {}
    )

    gmail_watch.renew_watch(_mailbox("fresh"))

    assert written["sync_cursor"] == "555"


def test_an_unparseable_expiration_does_not_abort_the_renewal(monkeypatch):
    """Gmail sends expiration as a string of epoch millis. If that ever changes
    shape, the watch is still registered - only its recorded expiry is unknown,
    which the next tick treats as due."""
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)
    written: dict = {}
    monkeypatch.setattr(
        gmail_watch, "GmailMailboxProvider", _provider({"historyId": "5", "expiration": "soon"})
    )
    monkeypatch.setattr(
        gmail_watch.store, "update_mailbox", lambda mid, values: written.update(values) or {}
    )

    gmail_watch.renew_watch(_mailbox("mb1"))

    assert written["gmail_watch_expiration"] is None
    assert written["gmail_watch_topic"] == TOPIC


def test_nothing_is_queried_when_push_is_not_configured(monkeypatch):
    """Without a topic there is no push to keep alive, and no reason to hit the
    database once an hour forever."""
    monkeypatch.delenv("GMAIL_PUBSUB_TOPIC", raising=False)
    monkeypatch.setattr(
        gmail_watch.store,
        "mailboxes_due_for_watch_renewal",
        lambda **_kwargs: pytest.fail("must not query without a configured topic"),
    )

    assert gmail_watch.renew_due_watches(now=NOW) == {"due": 0, "renewed": 0, "failed": 0}


def test_renew_watch_is_a_no_op_without_a_credential(monkeypatch):
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)
    assert gmail_watch.renew_watch({"id": "mb1", "credential_ciphertext": None}) is None


def test_the_endpoint_and_the_loop_share_one_implementation():
    """Two renewal code paths would drift. The HTTP endpoint must delegate to
    the same module the loop uses."""
    from core import recruitment_mail_api

    assert recruitment_mail_api.gmail_watch is gmail_watch


def test_the_loop_does_not_start_when_disabled(monkeypatch):
    monkeypatch.setenv("GMAIL_WATCH_RENEWAL_ENABLED", "0")
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)

    async def scenario():
        gmail_watch_renewal_loop.start_gmail_watch_renewal_loop()
        assert gmail_watch_renewal_loop._task is None

    asyncio.run(scenario())


def test_the_loop_does_not_start_without_a_topic(monkeypatch):
    monkeypatch.delenv("GMAIL_WATCH_RENEWAL_ENABLED", raising=False)
    monkeypatch.delenv("GMAIL_PUBSUB_TOPIC", raising=False)

    async def scenario():
        gmail_watch_renewal_loop.start_gmail_watch_renewal_loop()
        assert gmail_watch_renewal_loop._task is None

    asyncio.run(scenario())


def test_the_loop_starts_and_stops_cleanly(monkeypatch):
    monkeypatch.delenv("GMAIL_WATCH_RENEWAL_ENABLED", raising=False)
    monkeypatch.setenv("GMAIL_PUBSUB_TOPIC", TOPIC)

    async def scenario():
        gmail_watch_renewal_loop.start_gmail_watch_renewal_loop()
        assert gmail_watch_renewal_loop._task is not None
        await gmail_watch_renewal_loop.stop_gmail_watch_renewal_loop()
        assert gmail_watch_renewal_loop._task is None

    asyncio.run(scenario())


def test_a_failing_tick_does_not_kill_the_loop(monkeypatch):
    """One bad hour must not end renewal for the lifetime of the process -
    that would reproduce the original bug with extra steps."""
    monkeypatch.setattr(gmail_watch_renewal_loop, "STARTUP_DELAY_SEC", 0.0)
    monkeypatch.setattr(gmail_watch_renewal_loop, "CHECK_INTERVAL_SEC", 0.01)
    ticks: list[int] = []

    async def flaky():
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("transient")
        return {"due": 0, "renewed": 0, "failed": 0}

    monkeypatch.setattr(gmail_watch_renewal_loop, "run_renewal_tick", flaky)

    async def scenario():
        task = asyncio.create_task(gmail_watch_renewal_loop.gmail_watch_renewal_loop())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(ticks) >= 3:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert len(ticks) >= 3, "the loop stopped after the failing tick"
