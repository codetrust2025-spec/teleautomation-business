/**
 * Mail Alerts filters: two alert groups, plus a candidate filter.
 *
 * The screen used to offer all eighteen tracked classifications in one
 * dropdown, which is more choices than anyone filters by. It now offers
 * "Selection Related" and "Interview Related", and a second dropdown to narrow
 * to one candidate. The two combine.
 *
 * The filtering stays on the server. Doing it in the browser would have been
 * less code, but the table is paginated and the totals come from the same
 * query, so a client-side filter would show "129" above twenty filtered rows
 * and page through unfiltered data. These tests therefore assert the request
 * that goes out, not the rows that come back.
 *
 * The grouping itself is asserted as a partition. Every tracked classification
 * must belong to exactly one group, or an alert type added later would be
 * reachable under neither filter and quietly disappear from the screen with
 * nothing failing.
 */

import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  MailMonitoringNotifications,
  TRACKED_CLASSIFICATIONS,
  CLASSIFICATION_GROUPS,
} from "./MailMonitoringNotifications.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

const CANDIDATES = [
  { candidate_id: "cand-2", candidate_name: "Anita Rao", alert_count: 3 },
  { candidate_id: "cand-1", candidate_name: "Rahul Kumar", alert_count: 5 },
];

function response(body) { return Promise.resolve({ ok: true, json: () => Promise.resolve(body) }); }

let calls = [];
function install(candidates = CANDIDATES) {
  calls = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("fetch", vi.fn((url) => {
    const href = String(url);
    calls.push(href);
    if (href.includes("/mail-monitoring/candidates")) return response({ status: "ok", candidates });
    if (href.includes("/summary")) return response({ summary: { unread: 0 } });
    if (href.includes("/notifications")) return response({ notifications: [], total: 0 });
    return response({ status: "ok" });
  }));
}

const notificationCalls = () => calls.filter((c) => c.includes("/mail-monitoring/notifications?"));
const lastQuery = () => notificationCalls()[notificationCalls().length - 1] || "";

function renderScreen() {
  return render(<ConfirmProvider><MailMonitoringNotifications /></ConfirmProvider>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("alert type grouping", () => {
  it("partitions every tracked classification into exactly one group", () => {
    const grouped = CLASSIFICATION_GROUPS.flatMap((g) => g.classifications);
    expect([...grouped].sort()).toEqual([...TRACKED_CLASSIFICATIONS].sort());
    expect(new Set(grouped).size).toBe(grouped.length); // none in both
  });

  it("offers exactly two groups", () => {
    expect(CLASSIFICATION_GROUPS.map((g) => g.label)).toEqual([
      "Selection Related",
      "Interview Related",
    ]);
  });
});

describe("the alert type filter", () => {
  beforeEach(() => install());

  it("shows only the two groups plus an unfiltered option", async () => {
    renderScreen();
    const select = await screen.findByLabelText("Alert type filter");
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "All alert types",
      "Selection Related",
      "Interview Related",
    ]);
  });

  it("asks the server for the group rather than filtering in the browser", async () => {
    renderScreen();
    const select = await screen.findByLabelText("Alert type filter");
    fireEvent.change(select, { target: { value: "interview" } });
    await waitFor(() => expect(lastQuery()).toContain("classification_group=interview"));
  });

  it("drops the parameter entirely when set back to all", async () => {
    renderScreen();
    const select = await screen.findByLabelText("Alert type filter");
    fireEvent.change(select, { target: { value: "selection" } });
    await waitFor(() => expect(lastQuery()).toContain("classification_group=selection"));
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => expect(lastQuery()).not.toContain("classification_group"));
  });
});

describe("the candidate filter", () => {
  beforeEach(() => install());

  it("lists the candidates that have alerts, defaulting to all", async () => {
    renderScreen();
    const select = await screen.findByLabelText("Candidate filter");
    await waitFor(() => expect(select.options.length).toBe(3));
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "All candidates",
      "Anita Rao",
      "Rahul Kumar",
    ]);
    expect(select.value).toBe("");
  });

  it("filters by candidate_id, which is what the server matches on", async () => {
    renderScreen();
    const select = await screen.findByLabelText("Candidate filter");
    await waitFor(() => expect(select.options.length).toBe(3));
    fireEvent.change(select, { target: { value: "cand-1" } });
    await waitFor(() => expect(lastQuery()).toContain("candidate_id=cand-1"));
  });

  it("still renders when the candidate list cannot be loaded", async () => {
    // The dropdown degrades to "All candidates" rather than blocking the table.
    install([]);
    renderScreen();
    const select = await screen.findByLabelText("Candidate filter");
    expect(select.options.length).toBe(1);
  });
});

describe("the two filters together", () => {
  beforeEach(() => install());

  it("sends both in one request", async () => {
    renderScreen();
    const candidate = await screen.findByLabelText("Candidate filter");
    await waitFor(() => expect(candidate.options.length).toBe(3));
    fireEvent.change(candidate, { target: { value: "cand-2" } });
    fireEvent.change(await screen.findByLabelText("Alert type filter"), {
      target: { value: "interview" },
    });
    await waitFor(() => {
      const query = lastQuery();
      expect(query).toContain("candidate_id=cand-2");
      expect(query).toContain("classification_group=interview");
    });
  });

  it("returns to the first page when either filter changes", async () => {
    // Staying on page 4 of an unfiltered list after filtering would show an
    // empty table that looks like "no alerts".
    renderScreen();
    fireEvent.change(await screen.findByLabelText("Alert type filter"), {
      target: { value: "selection" },
    });
    await waitFor(() => expect(lastQuery()).toContain("offset=0"));
  });
});

describe("a long candidate list", () => {
  const many = Array.from({ length: 40 }, (_, i) => ({
    candidate_id: `cand-${i}`, candidate_name: `Candidate ${i}`, alert_count: 1,
  }));

  beforeEach(() => install(many));

  it("switches to type-ahead instead of a forty-option dropdown", async () => {
    const { container } = renderScreen();
    // The control starts as a <select> and is replaced once the candidate list
    // arrives, so the node must be re-queried rather than held across the wait.
    await waitFor(() => expect(screen.getByLabelText("Candidate filter").tagName).toBe("INPUT"));
    const input = screen.getByLabelText("Candidate filter");
    expect(input.getAttribute("list")).toBe("mail-candidate-options");
    expect(container.querySelectorAll("#mail-candidate-options option").length).toBe(40);
  });

  it("resolves a typed name to that candidate's id", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByLabelText("Candidate filter").tagName).toBe("INPUT"));
    fireEvent.change(screen.getByLabelText("Candidate filter"), { target: { value: "Candidate 7" } });
    await waitFor(() => expect(lastQuery()).toContain("candidate_id=cand-7"));
  });

  it("clears the filter when the text matches nobody", async () => {
    // Half-typed text must not silently keep the previous candidate applied.
    renderScreen();
    await waitFor(() => expect(screen.getByLabelText("Candidate filter").tagName).toBe("INPUT"));
    const input = screen.getByLabelText("Candidate filter");
    fireEvent.change(input, { target: { value: "Candidate 7" } });
    await waitFor(() => expect(lastQuery()).toContain("candidate_id=cand-7"));
    fireEvent.change(input, { target: { value: "Candid" } });
    await waitFor(() => expect(lastQuery()).not.toContain("candidate_id"));
  });
});
