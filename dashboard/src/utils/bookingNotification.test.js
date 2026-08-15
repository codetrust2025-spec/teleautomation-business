import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildBookingNotification,
  notificationDate,
  notificationTime,
  showMailAlertNotification,
} from "./mailAlertSound.js";

/**
 * Every automatic booking used to arrive as "Auto Booked Candidate", so two
 * bookings for different people at different times were indistinguishable.
 * These pin what the notification actually says.
 */

const TEJAS = {
  event: "slot_auto_booked",
  status: "Auto Booked",
  candidate_name: "Tejas Shinde",
  company_name: "Mindstix",
  interview_date: "2026-08-04",
  start_time: "17:00",
  end_time: "18:00",
  timezone: "Asia/Kolkata",
  interview_round: "L2",
  booking_id: "6ac39d0a25",
  booking_url: "/daily-ops?bookingId=6ac39d0a25",
  candidate_id: "5e798ac0d7",
};

const CHARAN = {
  ...TEJAS,
  candidate_name: "Reddy Charan M S",
  company_name: "Capgemini",
  start_time: "11:30",
  end_time: "12:00",
  interview_round: "L1",
  booking_id: "8ef0f58c41",
  candidate_id: "dfea98052b",
};

describe("a booked interview says who, when and which round", () => {
  it("reads exactly as specified", () => {
    expect(buildBookingNotification(TEJAS)).toEqual({
      title: "Interview Auto-Booked",
      body: "Tejas Shinde · Mindstix\n4 Aug 2026 · 5:00 PM–6:00 PM\nRound: L2",
      tag: "interview-booking-6ac39d0a25",
    });
  });

  it("distinguishes two bookings that used to look identical", () => {
    const first = buildBookingNotification(TEJAS);
    const second = buildBookingNotification(CHARAN);
    expect(second.body).toBe(
      "Reddy Charan M S · Capgemini\n4 Aug 2026 · 11:30 AM–12:00 PM\nRound: L1",
    );
    expect(first.body).not.toBe(second.body);
    expect(first.tag).not.toBe(second.tag);
  });
});

describe("a rescheduled interview says what changed", () => {
  it("reads exactly as specified", () => {
    expect(buildBookingNotification({
      ...TEJAS, event: "interview_rescheduled", status: "Rescheduled",
      start_time: "11:30", end_time: "12:00",
    })).toEqual({
      title: "Interview Rescheduled",
      body: "Tejas Shinde · Mindstix\nChanged to 4 Aug 2026 · 11:30 AM–12:00 PM",
      tag: "interview-booking-6ac39d0a25",
    });
  });
});

describe("a blocked booking says why", () => {
  it("names the refused time and the reason", () => {
    expect(buildBookingNotification({
      ...TEJAS, event: "slot_booking_blocked", status: "Blocked",
      booking_id: null, booking_audit_id: "audit-1",
      block_reason: "No matching slot available",
    })).toEqual({
      title: "Automatic Booking Blocked",
      body: "Tejas Shinde · 4 Aug 2026 · 5:00 PM\nReason: No matching slot available",
      tag: "interview-booking-audit-1",
    });
  });

  it("still names the candidate when no reason reached the browser", () => {
    const note = buildBookingNotification({
      ...TEJAS, event: "slot_booking_blocked", block_reason: "",
    });
    expect(note.body).toBe("Tejas Shinde · 4 Aug 2026 · 5:00 PM");
    expect(note.body).not.toContain("Reason:");
  });
});

describe("missing pieces are omitted, never rendered empty", () => {
  it("drops the company rather than printing a stray separator", () => {
    const note = buildBookingNotification({ ...TEJAS, company_name: "" });
    expect(note.body).toBe("Tejas Shinde\n4 Aug 2026 · 5:00 PM–6:00 PM\nRound: L2");
  });

  it("drops the round line when no round is known", () => {
    const note = buildBookingNotification({ ...TEJAS, interview_round: null });
    expect(note.body).toBe("Tejas Shinde · Mindstix\n4 Aug 2026 · 5:00 PM–6:00 PM");
    expect(note.body).not.toContain("Round:");
  });

  it("shows a start time alone when the end is unknown", () => {
    const note = buildBookingNotification({ ...TEJAS, end_time: "" });
    expect(note.body).toContain("4 Aug 2026 · 5:00 PM");
    expect(note.body).not.toContain("–");
  });

  it("never prints undefined, null or a raw id", () => {
    const note = buildBookingNotification({
      event: "slot_auto_booked", candidate_name: "Solo Candidate",
      company_name: undefined, interview_date: null, start_time: undefined,
      end_time: undefined, interview_round: undefined, booking_id: "abc123",
    });
    expect(note.body).toBe("Solo Candidate");
    for (const bad of ["undefined", "null", "NaN", "abc123"]) {
      expect(note.body).not.toContain(bad);
    }
  });

  it("says nothing at all when there is no candidate to name", () => {
    expect(buildBookingNotification({ event: "slot_auto_booked" })).toBeNull();
  });

  it("ignores an unusable date or time rather than inventing one", () => {
    const note = buildBookingNotification({
      ...TEJAS, interview_date: "2026-13-40", start_time: "99:99", end_time: "nope",
    });
    expect(note.body).toBe("Tejas Shinde · Mindstix\nRound: L2");
  });
});

describe("date and time formatting", () => {
  it.each([
    ["2026-08-04", "4 Aug 2026"],
    ["2026-12-31", "31 Dec 2026"],
    ["", ""],
    ["not-a-date", ""],
    ["2026-13-01", ""],
  ])("formats the date %s", (input, expected) => {
    expect(notificationDate(input)).toBe(expected);
  });

  it.each([
    ["17:00", "5:00 PM"],
    ["11:30", "11:30 AM"],
    ["00:05", "12:05 AM"],
    ["12:00", "12:00 PM"],
    ["23:59", "11:59 PM"],
    ["5:00 PM", "5:00 PM"],
    ["", ""],
    ["24:00", ""],
  ])("formats the time %s", (input, expected) => {
    expect(notificationTime(input)).toBe(expected);
  });
});

describe("delivery", () => {
  const originals = {};
  function stubNotification(permission = "granted") {
    const created = [];
    class FakeNotification {
      static permission = permission;
      static requestPermission = vi.fn(() => Promise.resolve(permission));
      constructor(title, options) {
        this.title = title;
        Object.assign(this, options);
        created.push(this);
      }
      close() { this.closed = true; }
    }
    vi.stubGlobal("Notification", FakeNotification);
    return created;
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    Object.assign(globalThis, originals);
  });

  it("delivers the booking wording, not the old generic line", () => {
    const created = stubNotification();
    showMailAlertNotification(TEJAS);
    expect(created).toHaveLength(1);
    expect(created[0].title).toBe("📅 Interview Auto-Booked");
    expect(created[0].body).toContain("4 Aug 2026 · 5:00 PM–6:00 PM");
  });

  it("reuses the booking tag so a retry replaces rather than repeats", () => {
    const created = stubNotification();
    showMailAlertNotification(TEJAS);
    showMailAlertNotification(TEJAS);
    expect(created).toHaveLength(2);
    expect(created[0].tag).toBe(created[1].tag);
    expect(created[0].tag).toBe("interview-booking-6ac39d0a25");
  });

  it("opens the booked interview when clicked", () => {
    const created = stubNotification();
    const seen = [];
    const listener = (e) => seen.push(e.detail);
    window.addEventListener("teleautomation:navigate", listener);
    showMailAlertNotification(TEJAS);
    created[0].onclick();
    window.removeEventListener("teleautomation:navigate", listener);
    expect(seen).toEqual([{
      view: "daily-ops", bookingId: "6ac39d0a25", candidateId: "5e798ac0d7",
    }]);
  });

  it("falls back to the candidate when nothing was booked", () => {
    const created = stubNotification();
    const seen = [];
    const listener = (e) => seen.push(e.detail);
    window.addEventListener("teleautomation:navigate", listener);
    showMailAlertNotification({
      ...TEJAS, event: "slot_booking_blocked", booking_id: null,
      block_reason: "No matching slot available",
    });
    created[0].onclick();
    window.removeEventListener("teleautomation:navigate", listener);
    expect(seen).toEqual([{ view: "candidates", candidateId: "5e798ac0d7" }]);
  });

  it("leaves non-booking mail alerts on the existing wording", () => {
    const created = stubNotification();
    showMailAlertNotification({
      event: "notification_created", status: "Offer Received",
      candidate_name: "Rahul", company_name: "Infosys", notification_id: "n1",
    });
    expect(created[0].title).toBe("📬 Offer Received");
    expect(created[0].tag).toBe("mail-alert-n1");
  });

  it("asks for permission once and delivers nothing until granted", () => {
    const created = stubNotification("default");
    showMailAlertNotification(TEJAS);
    expect(Notification.requestPermission).toHaveBeenCalledTimes(1);
    expect(created).toHaveLength(0);
  });

  it("stays silent when permission was denied", () => {
    const created = stubNotification("denied");
    showMailAlertNotification(TEJAS);
    expect(created).toHaveLength(0);
  });
});
