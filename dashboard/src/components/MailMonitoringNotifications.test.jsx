import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MailMonitoringNotifications, MailNotificationBell, mailStatusTone, blockingReason } from "./MailMonitoringNotifications.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

const notification = {
  id: "notification-1", candidate_id: "candidate-1", candidate_name: "Rahul Kumar",
  ai_recruitment_event_id: "event-1", gmail_message_id: "gmail-message-1",
  candidate_email: "rahul@example.com", company_name: "Infosys", job_role: "Software Engineer",
  classification: "offer_received", candidate_status: "Offer Received", priority: "high",
  email_subject: "Formal employment offer", sender_name: "Recruiter", sender_email: "hr@infosys.example",
  ai_confidence: 0.94, ai_summary: "A formal offer was issued.", ai_reason: "Employment terms are confirmed.",
  recommended_action: "Verify the offer with the candidate.", is_read: false, is_reviewed: false,
  email_received_at: "2026-07-15T04:55:00Z",
  created_at: "2026-07-15T05:00:00Z",
  interview_date: "2026-07-23", interview_time: "17:30", interview_timezone: "Asia/Kolkata",
};

function response(body) { return Promise.resolve({ ok: true, json: () => Promise.resolve(body) }); }
function renderNotifications() {
  return render(<ConfirmProvider><MailMonitoringNotifications /></ConfirmProvider>);
}

describe("mail monitoring notifications", () => {
  it("uses semantic colors for booking outcomes", () => {
    expect(mailStatusTone({ candidate_status: "Interview Automatically Booked" })).toBe("success");
    expect(mailStatusTone({ candidate_status: "Automatic Booking Blocked" })).toBe("warning");
    expect(mailStatusTone({ booking_status: "Processing Failed" })).toBe("danger");
    expect(mailStatusTone({ candidate_status: "Needs Review" })).toBe("review");
    expect(mailStatusTone({ candidate_status: "Already Booked — Duplicate Ignored" })).toBe("success");
    expect(mailStatusTone({ candidate_status: "Historical Interview — Review Only" })).toBe("review");
    expect(mailStatusTone({ candidate_status: "Historical Interview Skipped" })).toBe("neutral");
  });

  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/config")) return response({ enabled: true });
      if (String(url).includes("/summary")) return response({ summary: { unread: 1, new_offers: 1, selections: 0, joining_confirmations: 0, needs_review: 0 } });
      if (String(url).includes("/api/ai-recruitment/events/event-1")) return response({
        event: {
          received_email: {
            subject: "Formal employment offer",
            sender_name: "Recruiter",
            sender_email: "hr@infosys.example",
            recipient_email: "rahul@example.com",
            sent_at: "2026-07-15T04:55:00Z",
            body: "Dear Rahul,\nWe are pleased to offer you the role.",
          },
        },
      });
      if (String(url).includes("/notifications")) return response({ notifications: [notification], total: 1 });
      return response({ status: "ok" });
    }));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the persisted unread count and latest notification", async () => {
    render(<MailNotificationBell />);
    await waitFor(() => expect(screen.getByLabelText("1 unread mail monitoring notifications")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("1 unread mail monitoring notifications"));
    expect(await screen.findByText(/Rahul Kumar · Infosys/)).toBeInTheDocument();
    expect(screen.getByText("Offer Received")).toBeInTheDocument();
  });

  it("renders summary, filters, pagination and manual review actions", async () => {
    renderNotifications();
    expect(await screen.findByRole("heading", { name: "Mail Monitoring Notifications" })).toBeInTheDocument();
    expect(await screen.findByText("Formal employment offer")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Mail received" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Tool detected" })).toBeInTheDocument();
    expect(screen.getByLabelText("Classification filter")).toBeInTheDocument();
    expect(screen.queryByLabelText("Candidate status filter")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Priority filter")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Open email notification: Formal employment offer"));
    expect(await screen.findByText(/We are pleased to offer you the role/)).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/mail-monitoring/notifications/notification-1/read",
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByText("23 Jul 2026, 5:30 pm IST")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Re-run AI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start payment follow-up" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save correction" })).toBeInTheDocument();
  });

  it("clears the complete notification list after confirmation", async () => {
    renderNotifications();
    const button = await screen.findByRole("button", { name: "Clear all notifications" });
    fireEvent.click(button);
    expect(await screen.findByText("Clear all mail notifications?")).toBeInTheDocument();
    expect(screen.getByText("Email evidence")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Clear notifications"));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/mail-monitoring/notifications/clear-all",
      expect.objectContaining({ method: "POST" }),
    ));
  });
});

describe("blocked booking reasons", () => {
  const blocked = {
    ...notification,
    id: "notification-blocked",
    classification: "interview_confirmed",
    candidate_status: "Automatic Booking Blocked",
    booking_status: "Blocked",
    booking_block_reason: "No available slot matches the invite time (3 Aug 2026, 4:30 PM)",
    booking_block_reason_code: "NO_MATCHING_SLOT",
    booking_failure_code: "SLOT_CONFLICT",
  };

  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/config")) return response({ enabled: true });
      if (String(url).includes("/summary")) return response({ summary: { unread: 0, new_offers: 0, selections: 0, joining_confirmations: 0, needs_review: 1 } });
      if (String(url).includes("/notifications")) return response({ notifications: [blocked], total: 1 });
      return response({ status: "ok" });
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("takes the reason from the backend rather than inferring it", () => {
    // A status of "Blocked" says nothing about which check failed, so the
    // component must never manufacture a reason of its own.
    expect(blockingReason({ candidate_status: "Automatic Booking Blocked" })).toBeNull();
    expect(blockingReason(blocked)).toEqual({
      text: "No available slot matches the invite time (3 Aug 2026, 4:30 PM)",
      code: "NO_MATCHING_SLOT",
      internal: "SLOT_CONFLICT",
    });
  });

  it("falls back to manual review when only a code arrives", () => {
    expect(blockingReason({ booking_block_reason_code: "MANUAL_REVIEW_REQUIRED" })).toEqual({
      text: "Booking requires manual review",
      code: "MANUAL_REVIEW_REQUIRED",
      internal: "",
    });
  });

  it("shows the reason in the row without opening the notification", async () => {
    renderNotifications();
    const reason = await screen.findByText(/^Reason: No available slot matches the invite time/);
    expect(reason).toBeInTheDocument();
    // Same cell as the badge, so the two are read together.
    expect(reason.closest("td")).toContainElement(screen.getByText("Automatic Booking Blocked"));
  });

  it("offers the technical codes as a tooltip", async () => {
    renderNotifications();
    const reason = await screen.findByText(/^Reason: No available slot matches/);
    expect(reason).toHaveAttribute(
      "title",
      "No available slot matches the invite time (3 Aug 2026, 4:30 PM) (NO_MATCHING_SLOT / SLOT_CONFLICT)",
    );
  });

  it("clamps a long reason instead of stretching the table", async () => {
    renderNotifications();
    const reason = await screen.findByText(/^Reason: No available slot matches/);
    expect(reason).toHaveClass("mail-status__reason");
  });

  it("repeats the reason, both codes and the attempted result in the detail view", async () => {
    renderNotifications();
    fireEvent.click(await screen.findByText("Automatic Booking Blocked"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Blocking reason");
    expect(dialog).toHaveTextContent("No available slot matches the invite time (3 Aug 2026, 4:30 PM)");
    expect(dialog).toHaveTextContent("NO_MATCHING_SLOT");
    expect(dialog).toHaveTextContent("SLOT_CONFLICT");
    expect(dialog).toHaveTextContent("Attempted booking");
    // Detected candidate, round and schedule stay visible alongside the reason.
    expect(dialog).toHaveTextContent("Rahul Kumar");
  });

  it("shows no reason row for a booking that succeeded", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/config")) return response({ enabled: true });
      if (String(url).includes("/summary")) return response({ summary: { unread: 0, new_offers: 0, selections: 0, joining_confirmations: 0, needs_review: 0 } });
      if (String(url).includes("/notifications")) return response({
        notifications: [{ ...blocked, candidate_status: "Interview Automatically Booked", booking_status: "Auto Booked", booking_block_reason: null, booking_block_reason_code: null, booking_failure_code: null }],
        total: 1,
      });
      return response({ status: "ok" });
    }));
    renderNotifications();
    await screen.findByText("Interview Automatically Booked");
    expect(screen.queryByText(/^Reason:/)).toBeNull();
  });
});
