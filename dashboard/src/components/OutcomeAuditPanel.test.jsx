import React from "react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../context/ConfirmContext.jsx", () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}));

import { OutcomeAuditPanel } from "./OutcomeAuditPanel.jsx";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const SELECTION_SUMMARY = {
  mode: "SELECTION",
  total_connected_mailboxes: 18,
  mailboxes_scanned: 18,
  mailboxes_failed: 0,
  candidates_verified_offer_letters: 1,
  candidates_offer_indication: 1,
  candidates_rejected: 8,
  candidates_manual_review: 5,
  candidates_final_selection: 0,
  candidates_joining_confirmed: 0,
  candidates_background_verification: 0,
  candidates_shortlisted: 10,
  candidates_next_round: 10,
  candidates_no_outcome: 3,
  pipeline_gaps_total: 286,
  excluded_findings: 500,
  latest_run: {
    id: "run-1", started_at: "2026-08-05T15:21:00Z", status: "COMPLETED",
    mode: "REPORT_ONLY", messages_examined: 7455,
  },
};

const INTERVIEW_SUMMARY = {
  mode: "INTERVIEW",
  total_connected_mailboxes: 18,
  mailboxes_scanned: 18,
  candidates_with_interview_invites: 17,
  candidates_auto_booked: 8,
  candidates_booking_blocked: 3,
  candidates_slot_conflict: 1,
  candidates_interview_rescheduled: 4,
  candidates_missed_invites: 0,
  pipeline_gaps_total: 230,
  latest_run: SELECTION_SUMMARY.latest_run,
};

const CANDIDATE = {
  canonical_candidate_id: "8b52fe4c3d",
  candidate_name: "Lekkala swathi",
  email_address: "swathilekkala515@gmail.com",
  monitoring_status: "MONITORING_ACTIVE",
  scan_status: "SCANNED",
  strongest_outcome: "VERIFIED_OFFER_LETTER",
  strongest_confidence: 92,
  strongest_authenticity: "PARTIAL",
  system_status: "Profile Active",
  status_mismatch: true,
  mismatch_detail: "Mail evidence supports 'Offer Received'.",
  companies: ["kaivale.com", "deccanexperts.ai", "innovexis.in"],
  manual_review_required: true,
  messages_examined: 120,
  relevant_messages: 7,
  last_successful_sync_at: "2026-08-05T15:09:00Z",
  recommended_action: "Review and, if correct, approve the status update.",
};

const INTERVIEW_CANDIDATE = {
  ...CANDIDATE,
  strongest_outcome: "INTERVIEW_AUTO_BOOKED",
  status_mismatch: false,
  manual_review_required: false,
};

const APPLICATIONS = [
  {
    application_key: "kaivale.com:engineer",
    company: "Kaivale Technologies",
    role: "Sr. Software Engineer",
    latest_verified_state: "VERIFIED_OFFER_LETTER",
    evidence_strength: "STRONG",
    latest_message_at: "2026-07-16T12:10:00Z",
    strongest_finding_id: "f-kaivale",
    approval: { eligible: true, blockers: [], message: "" },
  },
];

const BLOCKED_APPLICATIONS = [
  {
    ...APPLICATIONS[0],
    approval: {
      eligible: false, blockers: ["Not the hiring company."],
      message: "Needs manual review — evidence is insufficient for a status change.",
    },
  },
];

const FINDINGS = [
  {
    id: "f-kaivale",
    outcome: "VERIFIED_OFFER_LETTER",
    confidence: 92,
    received_at: "2026-07-16T12:10:00Z",
    subject: "Your offer letter",
    sender_email: "vanshika@kaivale.com",
    rationale: "Offer document contains genuine offer details.",
    evidence: [{ source: "ATTACHMENT", meaning: "OFFER", text: "Annual CTC is INR 6,00,000" }],
    attachment_evidence: [{ filename: "Offer.pdf", extraction_status: "EXTRACTED" }],
    authenticity: "PARTIAL",
    source_type: "COMPANY",
    evidence_strength: "STRONG",
    pipeline_outcome: "JOINING_CONFIRMED",
    pipeline_agreement: "PIPELINE_STRONGER",
    provider_message_id: "19f6b02d5051d006",
  },
];

const OLLAMA_REVIEWS = {
  "f-kaivale": {
    model: "qwen2.5:7b",
    suggested_outcome: "JOINING_CONFIRMED",
    restricted_outcome: "JOINING_CONFIRMED",
    derived_agreement: "DISAGREES",
    normalized_confidence: 95,
    verified: true,
    quoted_evidence: "Thanks for accepting the offer letter.",
    reasoning: "The thread shows the offer was accepted.",
    cited_message_id: "19f6b02d5051d006",
    approval_state: "Needs manual review — deterministic evidence and the AI disagree.",
  },
};

let calls;
let applicationsFixture = APPLICATIONS;

const jsonResponse = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });

function mockFetch(overrides = {}) {
  calls = [];
  applicationsFixture = overrides.applications ?? APPLICATIONS;
  vi.stubGlobal(
    "fetch",
    vi.fn((url, options) => {
      const target = String(url);
      calls.push({ path: target, options });
      const interview = target.includes("mode=INTERVIEW");
      if (target.includes("/summary"))
        return jsonResponse({
          status: "ok", summary: interview ? INTERVIEW_SUMMARY : SELECTION_SUMMARY });
      if (target.includes("/candidates/"))
        return jsonResponse({
          status: "ok", candidate: CANDIDATE, findings: FINDINGS,
          applications: applicationsFixture, bookings: [], gaps: [], approvals: [],
          ollama_reviews: OLLAMA_REVIEWS,
        });
      if (target.includes("/candidates"))
        return jsonResponse({
          status: "ok",
          candidates: overrides.candidates ?? [interview ? INTERVIEW_CANDIDATE : CANDIDATE],
        });
      if (target.includes("/gaps")) return jsonResponse({ status: "ok", gaps: [] });
      if (target.includes("/excluded"))
        return jsonResponse({
          status: "ok",
          excluded: [{
            id: "x1", canonical_candidate_id: "8b52fe4c3d", candidate_name: "Lekkala swathi",
            outcome: "INTERVIEW_INVITE", subject: "Interview invitation",
            suppression_reason: "WRONG_AUDIT_MODE",
            suppression_detail: "Counted in the Interview Slot Audit instead.",
            suppressed_at: "2026-08-05T10:00:00Z",
          }],
        });
      if (target.includes("/approve"))
        return jsonResponse({
          status: "ok",
          approval: { status: "Offer Received", candidate_id: "8b52fe4c3d" } });
      return jsonResponse({ status: "ok" });
    }),
  );
  vi.stubGlobal("open", vi.fn());
}

beforeEach(() => mockFetch());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderPanel() {
  const view = render(<OutcomeAuditPanel />);
  await screen.findByText("Lekkala swathi");
  return view;
}

const evidenceButton = () =>
  screen.getAllByRole("button", { name: "Evidence" })[0];

const openDrawer = async () => {
  await renderPanel();
  fireEvent.click(evidenceButton());
  return screen.findByText("Recommended action");
};

// ── 1. Simplified header ────────────────────────────────────────────────────

describe("Header", () => {
  it("shows the title, last audit time and one primary action", async () => {
    await renderPanel();
    expect(screen.getByRole("heading", { name: "Candidate Mail Audit" })).toBeTruthy();
    expect(screen.getByText(/Last audit/)).toBeTruthy();
    expect(screen.getByText("Run audit")).toBeTruthy();
  });

  it("hides Export and Refresh behind More actions", async () => {
    await renderPanel();
    expect(screen.queryByText("Export CSV")).toBeNull();
    expect(screen.queryByText("Refresh")).toBeNull();
    fireEvent.click(screen.getByLabelText("More actions"));
    expect(screen.getByText("Export CSV")).toBeTruthy();
    expect(screen.getByText("Refresh")).toBeTruthy();
    expect(screen.getByText("Audit technical details")).toBeTruthy();
  });

  it("exports from the menu with the current mode and filters", async () => {
    await renderPanel();
    fireEvent.click(screen.getByLabelText("More actions"));
    fireEvent.click(screen.getByText("Export CSV"));
    expect(window.open.mock.calls[0][0]).toContain("mode=SELECTION");
    expect(window.open.mock.calls[0][0]).toContain("/mail-outcome-audit/export");
  });

  it("keeps technical details out of the way until asked", async () => {
    await renderPanel();
    expect(screen.queryByText("Messages examined")).toBeNull();
    fireEvent.click(screen.getByLabelText("More actions"));
    fireEvent.click(screen.getByText("Audit technical details"));
    expect(screen.getByText("Messages examined")).toBeTruthy();
    expect(screen.getByText("7455")).toBeTruthy();
  });
});

// ── 2. One navigation bar ───────────────────────────────────────────────────

describe("Navigation", () => {
  it("offers exactly three short sections", async () => {
    await renderPanel();
    const nav = within(screen.getByLabelText("Audit sections"));
    expect(nav.getByText("Selection")).toBeTruthy();
    expect(nav.getByText("Interviews")).toBeTruthy();
    expect(nav.getByText("Pipeline")).toBeTruthy();
    expect(nav.getAllByRole("button")).toHaveLength(3);
  });

  it("no longer duplicates the Notifications / Mail Audit tabs", async () => {
    await renderPanel();
    expect(screen.queryByLabelText("AI mail monitoring sections")).toBeNull();
  });

  it("keeps Excluded as a small link rather than a tab", async () => {
    await renderPanel();
    const nav = within(screen.getByLabelText("Audit sections"));
    expect(nav.queryByText(/Excluded/)).toBeNull();
    fireEvent.click(screen.getByText(/findings excluded from this audit/));
    expect(await screen.findByText("Counted in the Interview Slot Audit instead.")).toBeTruthy();
  });

  it("switches to the interview audit", async () => {
    await renderPanel();
    fireEvent.click(screen.getByText("Interviews"));
    await waitFor(() =>
      expect(calls.some((c) => c.path.includes("mode=INTERVIEW"))).toBe(true));
  });
});

// ── 3. Fewer metrics up front ───────────────────────────────────────────────

describe("Metrics", () => {
  it("shows five headline metrics for selection", async () => {
    const { container } = await renderPanel();
    const row = within(container.querySelector(".audit-metrics__row"));
    expect(row.getByText("Verified offers")).toBeTruthy();
    expect(row.getByText("Offer indications")).toBeTruthy();
    expect(row.getByText("Rejected")).toBeTruthy();
    expect(row.getByText("Needs review")).toBeTruthy();
    expect(row.getByText("Pipeline issues")).toBeTruthy();
    expect(container.querySelectorAll(".audit-metrics__row .audit-metric")).toHaveLength(5);
  });

  it("hides the remaining metrics until expanded", async () => {
    const { container } = await renderPanel();
    const secondary = () => container.querySelector(".audit-metrics__row--secondary");
    expect(secondary()).toBeNull();
    fireEvent.click(screen.getByText(/View all metrics/));
    const row = within(secondary());
    expect(row.getByText("Shortlisted")).toBeTruthy();
    expect(row.getByText("Connected mailboxes")).toBeTruthy();
    expect(row.getByText("Failed to scan")).toBeTruthy();
  });

  it("shows interview headline metrics in interview mode", async () => {
    const { container } = await renderPanel();
    fireEvent.click(screen.getByText("Interviews"));
    await screen.findByText("Invitations");
    const row = within(container.querySelector(".audit-metrics__row"));
    expect(row.getByText("Automatically booked")).toBeTruthy();
    expect(row.getByText("Slot conflicts")).toBeTruthy();
    expect(row.queryByText("Verified offers")).toBeNull();
  });
});

// ── 4. Simplified filters ───────────────────────────────────────────────────

describe("Filters", () => {
  it("shows only search, outcome, needs review and More filters", async () => {
    await renderPanel();
    expect(screen.getByLabelText("Search candidate")).toBeTruthy();
    expect(screen.getByLabelText("Filter by outcome")).toBeTruthy();
    expect(screen.getByLabelText(/needs review/i, { selector: "input" })).toBeTruthy();
    expect(screen.getByText("More filters")).toBeTruthy();
    expect(screen.queryByLabelText("Filter by company")).toBeNull();
    expect(screen.queryByLabelText("Filter by authenticity")).toBeNull();
  });

  it("reveals the rest under More filters", async () => {
    await renderPanel();
    fireEvent.click(screen.getByText("More filters"));
    expect(screen.getByLabelText("Filter by company")).toBeTruthy();
    expect(screen.getByLabelText("Filter by authenticity")).toBeTruthy();
    expect(screen.getByLabelText("Filter by mailbox sync status")).toBeTruthy();
    expect(screen.getByLabelText("Filter by minimum confidence")).toBeTruthy();
    expect(screen.getByLabelText("Evidence from date")).toBeTruthy();
    expect(screen.getByText("Status mismatch")).toBeTruthy();
  });

  it("offers Clear filters only when something is filtered", async () => {
    await renderPanel();
    expect(screen.queryByText(/Clear filters/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Search candidate"), { target: { value: "swathi" } });
    expect(await screen.findByText(/Clear filters \(1\)/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Clear filters/));
    await waitFor(() => expect(screen.queryByText(/Clear filters/)).toBeNull());
  });

  it("still sends every filter to the API", async () => {
    await renderPanel();
    fireEvent.click(screen.getByText("More filters"));
    fireEvent.change(screen.getByLabelText("Filter by company"), { target: { value: "kaivale" } });
    await waitFor(() => expect(calls.some((c) => c.path.includes("company=kaivale"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Filter by authenticity"),
      { target: { value: "SUSPICIOUS" } });
    await waitFor(() =>
      expect(calls.some((c) => c.path.includes("authenticity=SUSPICIOUS"))).toBe(true));
  });
});

// ── 5 & 6. Table and badges ─────────────────────────────────────────────────

describe("Table", () => {
  it("shows only the six decision columns", async () => {
    await renderPanel();
    const headers = screen.getAllByRole("columnheader").map((el) => el.textContent.trim());
    expect(headers).toEqual([
      "Candidate", "Strongest outcome", "Company", "System status", "Last updated", "Evidence",
    ]);
  });

  it("keeps the candidate id and mailbox status out of the main row", async () => {
    await renderPanel();
    const row = screen.getByText("Lekkala swathi").closest("tr");
    expect(row.textContent).not.toContain("8b52fe4c3d");
    expect(row.textContent).not.toContain("Monitoring active");
    // Gmail sits with the name, as one column.
    expect(within(row).getByText("swathilekkala515@gmail.com")).toBeTruthy();
  });

  it("shows one outcome badge plus small secondary text", async () => {
    await renderPanel();
    const row = screen.getByText("Lekkala swathi").closest("tr");
    expect(within(row).getAllByText(/Verified offer letter/)).toHaveLength(1);
    expect(within(row).getByText(/92% confidence/)).toBeTruthy();
    expect(row.querySelectorAll(".audit-badge")).toHaveLength(1);
  });

  it("shows one warning icon and one mismatch indicator", async () => {
    await renderPanel();
    const row = screen.getByText("Lekkala swathi").closest("tr");
    expect(within(row).getAllByLabelText("Needs review")).toHaveLength(1);
    expect(within(row).getAllByText("Mismatch")).toHaveLength(1);
  });

  it("reveals hidden detail when the row is expanded", async () => {
    await renderPanel();
    const toggle = screen.getByRole("button", { name: /Lekkala swathi/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Candidate ID")).toBeTruthy();
    expect(screen.getByText("8b52fe4c3d")).toBeTruthy();
    expect(screen.getByText("Messages examined")).toBeTruthy();
  });

  it("summarises a long company list instead of printing every domain", async () => {
    await renderPanel();
    const row = screen.getByText("Lekkala swathi").closest("tr");
    expect(within(row).getByText("+2 more")).toBeTruthy();
    expect(row.textContent).not.toContain("innovexis.in");
  });
});

// ── 7. Evidence drawer ──────────────────────────────────────────────────────

describe("Evidence drawer", () => {
  it("opens with the recommended action first", async () => {
    await openDrawer();
    expect(screen.getByText("Recommended action")).toBeTruthy();
    expect(screen.getByText(/approve the status update/)).toBeTruthy();
  });

  it("shows one candidate-level action, not one per email", async () => {
    await openDrawer();
    expect(screen.getAllByText("Review status update")).toHaveLength(1);
    expect(screen.getAllByText("Mark reviewed")).toHaveLength(1);
    expect(screen.queryByText("Approve status update")).toBeNull();
  });

  it("approves against the eligible application", async () => {
    await openDrawer();
    fireEvent.click(screen.getByText("Review status update"));
    await waitFor(() =>
      expect(calls.some((c) => c.path.includes("/findings/f-kaivale/approve"))).toBe(true));
  });

  it("offers no status update when no application qualifies", async () => {
    mockFetch({ applications: BLOCKED_APPLICATIONS });
    render(<OutcomeAuditPanel />);
    await screen.findByText("Lekkala swathi");
    fireEvent.click(evidenceButton());
    await screen.findByText("Recommended action");
    expect(screen.queryByText("Review status update")).toBeNull();
    expect(screen.getByText(/No application meets the bar/)).toBeTruthy();
  });

  it("keeps technical detail collapsed by default", async () => {
    await openDrawer();
    expect(screen.queryByText("19f6b02d5051d006")).toBeNull();
    fireEvent.click(screen.getByText(/Technical details/));
    // Appears twice: the message id and the AI's citation of the same message.
    expect(screen.getAllByText("19f6b02d5051d006").length).toBeGreaterThan(0);
    expect(screen.getByText("qwen2.5:7b")).toBeTruthy();
  });

  it("keeps the AI comparison collapsed by default", async () => {
    await openDrawer();
    expect(screen.queryByText(/Thanks for accepting the offer letter/)).toBeNull();
    fireEvent.click(screen.getByText(/AI audit comparison/));
    expect(screen.getByText(/Thanks for accepting the offer letter/)).toBeTruthy();
    expect(screen.getByText(/deterministic evidence and the AI disagree/)).toBeTruthy();
  });

  it("shows the company and application timeline", async () => {
    await openDrawer();
    expect(screen.getByText("Companies and applications")).toBeTruthy();
    expect(screen.getByText("Kaivale Technologies")).toBeTruthy();
    expect(screen.getByText(/A result from one company never affects another/)).toBeTruthy();
  });
});

// ── 8. Accessibility and layout ─────────────────────────────────────────────

describe("Accessibility", () => {
  it("marks the active section for assistive technology", async () => {
    await renderPanel();
    expect(screen.getByText("Selection").getAttribute("aria-current")).toBe("page");
  });

  it("gives every expander an aria-expanded state", async () => {
    await renderPanel();
    const more = screen.getByText("More filters");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(more.getAttribute("aria-expanded")).toBe("true");
  });

  it("exposes the more-actions menu as a menu", async () => {
    await renderPanel();
    const trigger = screen.getByLabelText("More actions");
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");
    fireEvent.click(trigger);
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
  });

  it("keeps the drawer a labelled modal dialog", async () => {
    await openDrawer();
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("Candidate mail evidence");
  });

  it("uses table headers with scope", async () => {
    await renderPanel();
    screen.getAllByRole("columnheader").forEach((header) => {
      expect(header.getAttribute("scope")).toBe("col");
    });
  });
});

describe("Responsive layout", () => {
  const css = () => fs.readFileSync(path.join(SRC, "outcomeAudit.css"), "utf8");

  it("scrolls wide tables inside their own container", () => {
    expect(css()).toMatch(/\.audit-table-wrap\s*\{[^}]*overflow-x:\s*auto/s);
  });

  it("has breakpoints from small phones to 4K", () => {
    const text = css();
    expect(text).toContain("@media (max-width: 380px)");
    expect(text).toContain("@media (max-width: 599px)");
    expect(text).toContain("@media (max-width: 900px)");
    expect(text).toContain("@media (min-width: 2000px)");
  });

  it("uses only three badge tones", () => {
    const tones = [...css().matchAll(/\.audit-badge--([a-z]+)/g)].map((m) => m[1]);
    expect(new Set(tones)).toEqual(new Set(["good", "warn", "bad"]));
  });
});

// ── 9. Nothing lost ─────────────────────────────────────────────────────────

describe("Existing behaviour is preserved", () => {
  it("still runs a report-only audit", async () => {
    await renderPanel();
    fireEvent.click(screen.getByText("Run audit"));
    await waitFor(() => expect(calls.some((c) => c.path.includes("/run"))).toBe(true));
    const call = calls.find((c) => c.path.includes("/run"));
    expect(JSON.parse(call.options.body)).toEqual({ incremental: false });
  });

  it("keeps selection and interview results separate", async () => {
    await renderPanel();
    fireEvent.click(screen.getByText("Interviews"));
    await screen.findByText("Invitations");
    expect(screen.queryByText("Verified offers")).toBeNull();
    // The mismatch filter is selection-only and disappears with the mode.
    fireEvent.click(screen.getByText("More filters"));
    expect(screen.queryByText("Status mismatch")).toBeNull();
  });

  it("does not reach into the interview auto-booking feature", () => {
    const source = fs.readFileSync(
      path.join(SRC, "components", "OutcomeAuditPanel.jsx"), "utf8");
    for (const token of ["execute_auto_booking", "interview_auto_booking",
                         "assign_interview_slot"]) {
      expect(source).not.toContain(token);
    }
  });
});
