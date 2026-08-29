"""An event must reach the browser whichever process published it.

publish() fans out through `_connections`, a dict in the publishing process's
own memory, guarded by `if loop and loop.is_running()`. Any publisher that is
not the API process - an operational rescan script, a cron, a second uvicorn
worker - has no loop, so the event was written to mail_realtime_events and
delivered to nobody. The browser saw it only if it happened to reconnect and
replay.

That is how the August recovery looked like a delivery failure: the alert was
created, notification_created was persisted with the right payload, the socket
was open at 101, and no frame ever arrived. Nothing logged, because skipping
the broadcast was the normal path.

The fix reuses the durable table clients already replay from, tailed
continuously inside the API process.
"""

from __future__ import annotations

import asyncio

import pytest

from core import recruitment_realtime as rt


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def clean_state():
    rt._connections.clear()
    rt._delivered.clear()
    yield
    rt._connections.clear()
    rt._delivered.clear()


def rows(*ids):
    return [
        {"id": i, "event_type": "notification_created",
         "payload": {"classification": "joining_confirmed", "candidate_name": "Gopichand"},
         "created_at": "2026-08-29T12:00:00+00:00"}
        for i in ids
    ]


class TestTheTailerDelivers:
    def test_an_event_from_another_process_reaches_a_connected_client(self, monkeypatch):
        socket = FakeSocket()
        rt._connections[socket] = {"username": "operator"}
        served = [rows("evt-1"), []]

        def fake_list(*, after_id=None, limit=100):
            # limit=1 is the tailer establishing its starting position: it must
            # begin at the present, not replay history the client already has.
            if limit == 1:
                return []
            return served.pop(0) if served else []

        from core import recruitment_mail_store as store
        monkeypatch.setattr(store, "list_realtime_events", fake_list)

        async def run():
            task = asyncio.get_running_loop().create_task(rt._tail_events(poll_seconds=0.01))
            await asyncio.sleep(0.08)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert [p["event_id"] for p in socket.sent] == ["evt-1"]
        assert socket.sent[0]["event"] == "notification_created"
        assert socket.sent[0]["classification"] == "joining_confirmed"

    def test_an_event_already_sent_locally_is_not_sent_twice(self, monkeypatch):
        """publish() broadcasts directly when it owns the loop; the tailer reads
        the same table and must not duplicate it."""
        socket = FakeSocket()
        rt._connections[socket] = {"username": "operator"}
        rt._mark_delivered("evt-1")
        served = [rows("evt-1"), []]

        def fake_list(*, after_id=None, limit=100):
            # limit=1 is the tailer establishing its starting position: it must
            # begin at the present, not replay history the client already has.
            if limit == 1:
                return []
            return served.pop(0) if served else []

        from core import recruitment_mail_store as store
        monkeypatch.setattr(store, "list_realtime_events", fake_list)

        async def run():
            task = asyncio.get_running_loop().create_task(rt._tail_events(poll_seconds=0.01))
            await asyncio.sleep(0.08)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert socket.sent == []

    def test_a_failing_read_does_not_kill_the_tailer(self, monkeypatch):
        """A tailer that dies takes live delivery with it, silently."""
        socket = FakeSocket()
        rt._connections[socket] = {"username": "operator"}
        calls = {"n": 0}

        def fake_list(*, after_id=None, limit=100):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("database blipped")
            return rows("evt-after-failure")

        from core import recruitment_mail_store as store
        monkeypatch.setattr(store, "list_realtime_events", fake_list)

        async def run():
            task = asyncio.get_running_loop().create_task(rt._tail_events(poll_seconds=0.01))
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        assert any(p["event_id"] == "evt-after-failure" for p in socket.sent)


class TestPublishStillWorksInProcess:
    def test_publish_marks_what_it_sent_so_the_tailer_skips_it(self, monkeypatch):
        from core import recruitment_mail_store as store
        monkeypatch.setattr(store, "record_realtime_event",
                            lambda t, p: {"id": "evt-local", "created_at": "now"})

        async def run():
            rt.configure_loop(asyncio.get_running_loop())
            rt.publish("notification_created", classification="offer_received")
            await asyncio.sleep(0)

        asyncio.run(run())
        assert rt._already_delivered("evt-local")

    def test_publish_without_a_loop_still_persists(self, monkeypatch):
        """The durable write is what lets the tailer, or a reconnect, recover."""
        written = {}

        from core import recruitment_mail_store as store

        def record(event_type, payload):
            written["type"] = event_type
            return {"id": "evt-no-loop", "created_at": "now"}

        monkeypatch.setattr(store, "record_realtime_event", record)
        rt.configure_loop(None)
        envelope = rt.publish("notification_created", classification="joining_confirmed")
        assert written["type"] == "notification_created"
        assert envelope["event_id"] == "evt-no-loop"
        assert not rt._already_delivered("evt-no-loop"), (
            "an undelivered event must stay undelivered so the tailer sends it"
        )
