import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import EarningsBreakdown from "./EarningsBreakdown.jsx";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fmt = (v) => `₹${Number(v).toLocaleString("en-IN")}`;

const MONTH_OPTIONS = [
  { value: "all", label: "All months" },
  { value: "2026-08", label: "Aug 2026 · this month" },
  { value: "2026-07", label: "Jul 2026" },
];

const PERFORMER = {
  name: "Thrilok",
  count: 0,
  salary_total: 15000,
  auto_earnings_total: 15000,
  paid_out_total: 0,
  net_payable: 20000,
  prior_balance: 5000,
};

function renderBreakdown(props = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", candidates: [] }) })),
  );
  return render(
    <EarningsBreakdown
      stats={{ top_performers: [PERFORMER] }}
      month="2026-08"
      monthOptions={MONTH_OPTIONS}
      formatCurrency={fmt}
      apiBase="/api"
      {...props}
    />,
  );
}

/**
 * The page's filter bar already owns the month. The Earnings section used to
 * render a second selector bound to the same state and the same options, so
 * two controls looked like independent filters while neither was.
 */
describe("Earnings breakdown month control", () => {
  it("renders no month selector of its own", () => {
    const { container } = renderBreakdown();

    const selects = [...container.querySelectorAll("select")];
    const labels = selects.map((s) => s.closest("label")?.textContent || "");
    expect(labels.some((t) => /month/i.test(t))).toBe(false);
    // Sorting is genuinely local to this table and stays.
    expect(labels.some((t) => /sort by/i.test(t))).toBe(true);
    expect(selects).toHaveLength(1);
  });

  it("offers no way to pick a different month from inside the section", () => {
    renderBreakdown();

    const options = [...document.querySelectorAll("option")].map((o) => o.textContent);
    expect(options).not.toContain("Jul 2026");
    expect(options).not.toContain("All months");
  });

  it("shows which month the table is scoped to, without the duplicate suffix", () => {
    const { container } = renderBreakdown();

    expect(container.querySelector(".earn-scope").textContent).toBe("Aug 2026");
  });

  it("follows the month it is given", () => {
    const { container, rerender } = renderBreakdown();
    expect(container.querySelector(".earn-scope").textContent).toBe("Aug 2026");

    rerender(
      <EarningsBreakdown
        stats={{ top_performers: [PERFORMER] }}
        month="2026-07"
        monthOptions={MONTH_OPTIONS}
        formatCurrency={fmt}
        apiBase="/api"
      />,
    );

    expect(container.querySelector(".earn-scope").textContent).toBe("Jul 2026");
  });

  it("reacts to the data the global filter produced", () => {
    const { container, rerender } = renderBreakdown();
    expect(within(container).getAllByText("₹15,000").length).toBeGreaterThan(0);

    rerender(
      <EarningsBreakdown
        stats={{ top_performers: [{ ...PERFORMER, auto_earnings_total: 42000, salary_total: 0 }] }}
        month="2026-07"
        monthOptions={MONTH_OPTIONS}
        formatCurrency={fmt}
        apiBase="/api"
      />,
    );

    expect(within(container).getAllByText("₹42,000").length).toBeGreaterThan(0);
  });

  it("still names the month when the empty state is shown", () => {
    const { container } = renderBreakdown({ stats: { top_performers: [] } });

    expect(container.querySelector(".earn-scope").textContent).toBe("Aug 2026");
    expect(container.querySelectorAll("select")).toHaveLength(0);
  });
});
