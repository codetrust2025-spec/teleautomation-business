import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PaymentReconciliationPanel from "./PaymentReconciliationPanel.jsx";
import BgvRegisterPanel from "./BgvRegisterPanel.jsx";

const RECON = {
  status: "ok",
  profiles_checked: 3,
  counts: { EXACT_MATCH: 1, GENUINE_MISMATCH: 1, LEGACY_INCOMPLETE_COVERAGE: 1 },
  records: [
    {
      candidate_id: "sak", candidate_name: "sakthivek",
      classification: "EXACT_MATCH",
      service_expected: 20000, bgv_expected: 30000,
      recorded_received: 30000, verified_transaction_total: 30000,
      service_allocation: 20000, bgv_allocation: 10000, outstanding: 20000,
      difference: 0, utrs: ["250859628039"], file_states: ["AVAILABLE"],
      notes: ["Contains 1 historical ADMIN_CONFIRMED_NOT_PAID transaction(s)."],
      recommended_action: "Recorded amount matches verified evidence exactly.",
    },
    {
      candidate_id: "uday", candidate_name: "Uday Kumar Rapolu",
      classification: "GENUINE_MISMATCH",
      service_expected: 20000, bgv_expected: 0,
      recorded_received: 20000, verified_transaction_total: 0,
      service_allocation: 20000, bgv_allocation: 0, outstanding: 0,
      difference: -20000, utrs: [], file_states: ["AVAILABLE"], notes: [],
      recommended_action: "Do not reduce without confirming the shortfall is real.",
    },
    {
      candidate_id: "leg", candidate_name: "Legacy Person",
      classification: "LEGACY_INCOMPLETE_COVERAGE",
      service_expected: 20000, bgv_expected: 0,
      recorded_received: 20000, verified_transaction_total: 0,
      service_allocation: 20000, bgv_allocation: 0, outstanding: 0,
      difference: -20000, utrs: [], file_states: [], notes: [],
      recommended_action: "Leave as is and upload the original receipt.",
    },
  ],
};

const BGV = {
  status: "ok",
  total_cases: 1, active_cases: 1, completed_cases: 0, cancelled_cases: 0,
  needs_review: 0, expected_total: 30000, collected_total: 10000,
  outstanding_total: 20000, paid_to_consultancy_total: 0,
  consultancy_payable_total: 10000,
  company_earning_total: 0, referral_earning_total: 0,
  cases: [{
    case_id: "bgv_4d264f6417d0", candidate_name: "sakthivek",
    consultancy: "BGV vendor", service_description: "Background verification",
    bgv_expected: 30000, bgv_collected: 10000, bgv_outstanding: 20000,
    paid_to_consultancy: 0, consultancy_payable: 10000, over_settled: 0,
    needs_adjustment: false, status: "PARTIALLY_COLLECTED",
    company_earning: 0, referral_earning: 0,
    collections: [{ collection_id: "col_1", amount: 10000, verified: true,
                    transaction_reference: "250859628039",
                    occurred_on: "2026-06-22", note: "BGV share" }],
    settlements: [],
    audit: [{ at: "2026-08-07T10:00:00Z", action: "case_created",
              actor: "administrator" }],
  }],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const path = String(url);
    // /bgv/cases/<id> returns one case; /bgv/dashboard returns the board.
    if (/\/bgv\/cases\/[^/]+$/.test(path)) {
      return { ok: true, json: async () => ({ status: "ok", case: BGV.cases[0] }) };
    }
    return { ok: true, json: async () => (path.includes("/bgv/") ? BGV : RECON) };
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Payment Reconciliation page", () => {
  it("shows the summary cards from the backend", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("Profiles checked")).toBeInTheDocument());
    const cards = document.querySelectorAll(".recon-card");
    expect(cards.length).toBeGreaterThan(5);
    expect(screen.getByText("Exact matches")).toBeInTheDocument();
    expect(screen.getByText("Genuine mismatches")).toBeInTheDocument();
    expect(screen.getByText("BGV allocation issues")).toBeInTheDocument();
  });

  it("renders backend figures verbatim rather than recomputing them", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("sakthivek")).toBeInTheDocument());
    const row = screen.getByText("sakthivek").closest("tr");
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toContain("₹20,000");
    expect(cells).toContain("₹30,000");
    expect(cells).toContain("₹10,000");
  });

  it("labels a genuine mismatch as the most serious tone", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("Genuine mismatch")).toBeInTheDocument());
    expect(document.querySelector(".recon-tag--bad")).toBeTruthy();
  });

  it("never offers to reduce a legacy record", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("Legacy Person")).toBeInTheDocument());
    expect(screen.getByText(/Leave as is/)).toBeInTheDocument();
  });

  it("filters by clicking a summary card", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("sakthivek")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Genuine mismatches").closest("button"));
    await waitFor(() => expect(screen.queryByText("sakthivek")).toBeNull());
    expect(screen.getByText("Uday Kumar Rapolu")).toBeInTheDocument();
  });

  it("searches by candidate and by UTR", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("sakthivek")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Search reconciliation"),
                     { target: { value: "250859628039" } });
    await waitFor(() => expect(screen.queryByText("Legacy Person")).toBeNull());
    expect(screen.getByText("sakthivek")).toBeInTheDocument();
  });

  it("offers a CSV export", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("Export CSV")).toBeInTheDocument());
    expect(screen.getByText("Export CSV").getAttribute("href"))
      .toContain("/payments/reconciliation.csv");
  });

  it("surfaces a historical note without making it the classification", async () => {
    render(<PaymentReconciliationPanel />);
    await waitFor(() => expect(screen.getByText("sakthivek")).toBeInTheDocument());
    const row = screen.getByText("sakthivek").closest("tr");
    expect(within(row).getByText("Exact match")).toBeInTheDocument();
    expect(within(row).getByText("1 note")).toBeInTheDocument();
  });
});

describe("BGV Consultancy page", () => {
  it("states that BGV earns nothing", async () => {
    render(<BgvRegisterPanel />);
    await waitFor(() => expect(screen.getByText("BGV Consultancy")).toBeInTheDocument());
    expect(screen.getByText(/earns the company, the referrer and the handler nothing/))
      .toBeInTheDocument();
  });

  it("shows the collected and outstanding balances", async () => {
    render(<BgvRegisterPanel />);
    await waitFor(() => expect(document.querySelector(".bgv-cards")).toBeTruthy());
    // "Collected" is also a table column, so read the summary cards only.
    const cards = document.querySelector(".bgv-cards");
    expect(within(cards).getByText("Collected")).toBeInTheDocument();
    expect(within(cards).getByText("Still to collect")).toBeInTheDocument();
    expect(within(cards).getByText("Remaining payable")).toBeInTheDocument();
    // Collected and Remaining payable are both Rs 10,000 here, so read each
    // card's own value rather than searching the whole group.
    const valueOf = (label) =>
      within(cards).getByText(label).closest(".bgv-card")
        .querySelector(".bgv-card-value").textContent;
    expect(valueOf("Collected")).toBe("₹10,000");
    expect(valueOf("Still to collect")).toBe("₹20,000");
    expect(valueOf("Remaining payable")).toBe("₹10,000");
  });

  it("lists the case with its balances", async () => {
    render(<BgvRegisterPanel />);
    await waitFor(() => expect(screen.getByText("BGV vendor")).toBeInTheDocument());
    const row = screen.getByText("BGV vendor").closest("tr");
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toContain("₹30,000");
    expect(cells).toContain("₹10,000");
    expect(cells).toContain("₹20,000");
    expect(within(row).getByText("Partially collected")).toBeInTheDocument();
  });

  it("opens the case detail with balances, collections and audit", async () => {
    render(<BgvRegisterPanel />);
    await waitFor(() => expect(screen.getByText("Open")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Open"));
    await waitFor(() => expect(document.querySelector(".bgv-detail")).toBeTruthy());
    expect(screen.getByText("Consultancy payable")).toBeInTheDocument();
    expect(screen.getByText("Company earning")).toBeInTheDocument();
    expect(screen.getByText("250859628039")).toBeInTheDocument();
    expect(screen.getByText("case_created")).toBeInTheDocument();
  });

  it("shows zero company and referral earning on the case", async () => {
    render(<BgvRegisterPanel />);
    await waitFor(() => expect(screen.getByText("Open")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Open"));
    await waitFor(() => expect(screen.getByText("Company earning")).toBeInTheDocument());
    const balances = document.querySelector(".bgv-balances");
    const company = within(balances).getByText("Company earning").closest("div");
    expect(within(company).getByText("₹0")).toBeInTheDocument();
  });

  it("offers a CSV export", async () => {
    render(<BgvRegisterPanel />);
    await waitFor(() => expect(screen.getByText("Export CSV")).toBeInTheDocument());
    expect(screen.getByText("Export CSV").getAttribute("href")).toContain("/bgv/cases.csv");
  });
});
