import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import EarningsBreakdown from "./EarningsBreakdown.jsx";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fmt = (value) => `₹${Number(value).toLocaleString("en-IN")}`;

/**
 * The expanded summary strip has to add up on screen:
 *
 *   opening + earnings − recoveries − expenses = closing
 *
 * The backend computes closing as
 * `(commission + salary) − recoveries − paid_out + prior_balance`
 * (features/candidate_store.py). The strip used to print commission alone as
 * "Earnings" and omit recoveries entirely, so a referrer on a salary saw a row
 * that contradicted its own closing balance.
 */
async function renderStrip(performer) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", candidates: [] }) })),
  );
  render(
    <EarningsBreakdown
      stats={{ top_performers: [performer] }}
      month="2026-07"
      formatCurrency={fmt}
      apiBase="/api"
    />,
  );
  const nameCell = screen.getAllByText(performer.name).find((el) => el.closest("tr.earn-row"));
  fireEvent.click(nameCell.closest("tr"));
  await screen.findByText(/^Total \(/);
  return await waitFor(() => {
    const ledger = document.querySelector(".earn-ledger");
    expect(ledger).toBeInTheDocument();
    return ledger;
  });
}

function amountFor(ledger, label) {
  const row = within(ledger).getByText(label).closest(".earn-ledger-row");
  return row.querySelector(".earn-ledger-value").textContent;
}

function toNumber(text) {
  const negative = text.startsWith("−") || text.startsWith("-");
  const digits = Number(text.replace(/[^0-9]/g, "")) || 0;
  return negative ? -digits : digits;
}

describe("the handler calculation reconciles with the closing balance", () => {
  // Thrilok, Jul 2026: commission 27,000 + salary 15,000 = 42,000 owed,
  // 42,000 paid out, 5,000 carried in. Closing must stay +5,000.
  const SALARIED = {
    name: "Thrilok",
    count: 4,
    completed: 0,
    revenue_total: 54000,
    commission_total: 27000,
    salary_total: 15000,
    auto_earnings_total: 42000,
    paid_out_total: 42000,
    recoveries_total: 0,
    prior_balance: 5000,
    net_payable: 5000,
  };

  it("keeps salary in the sum so the arithmetic holds", async () => {
    const strip = await renderStrip(SALARIED);

    // Salary is now its own line rather than folded into a single "Earnings"
    // figure, but it must still be counted — dropping it was the original bug.
    expect(amountFor(strip, "Referral earnings")).toBe(fmt(27000));
    expect(amountFor(strip, "Salary")).toBe(fmt(15000));

    const opening = toNumber(amountFor(strip, "Opening balance"));
    const earnings = toNumber(amountFor(strip, "Referral earnings"));
    const salary = toNumber(amountFor(strip, "Salary"));
    const paidOut = toNumber(amountFor(strip, "Paid out"));
    const closing = toNumber(amountFor(strip, /^Closing balance/));

    expect(opening + earnings + salary + paidOut).toBe(closing);
    expect(closing).toBe(5000);
  });

  it("shows a zero salary line rather than hiding it", async () => {
    const strip = await renderStrip({
      ...SALARIED,
      salary_total: 0,
      auto_earnings_total: 27000,
      paid_out_total: 27000,
    });
    expect(amountFor(strip, "Salary")).toBe(fmt(0));
    expect(amountFor(strip, "Referral earnings")).toBe(fmt(27000));
  });

  it("shows recoveries as a deduction and still reconciles", async () => {
    const strip = await renderStrip({
      ...SALARIED,
      recoveries_total: 3000,
      // 5000 + 27000 + 15000 − 3000 − 42000
      net_payable: 2000,
    });

    expect(amountFor(strip, "Recoveries")).toBe("−₹3,000");

    const total =
      toNumber(amountFor(strip, "Opening balance")) +
      toNumber(amountFor(strip, "Referral earnings")) +
      toNumber(amountFor(strip, "Salary")) +
      toNumber(amountFor(strip, "Recoveries")) +
      toNumber(amountFor(strip, "Paid out"));
    expect(total).toBe(toNumber(amountFor(strip, /^Closing balance/)));
  });

  it("hides the recoveries line when there are none", async () => {
    const strip = await renderStrip(SALARIED);
    expect(within(strip).queryByText("Recoveries")).toBeNull();
  });

  it("still shows a zero opening balance when nothing was carried in", async () => {
    const strip = await renderStrip({
      ...SALARIED,
      prior_balance: 0,
      net_payable: 0,
    });
    expect(amountFor(strip, "Opening balance")).toBe(fmt(0));
    expect(within(strip).getByText("Settled")).toBeInTheDocument();
  });
});
