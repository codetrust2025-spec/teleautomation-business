import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import EarningsBreakdown from "./EarningsBreakdown.jsx";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fmt = (v) => `₹${Number(v).toLocaleString("en-IN")}`;

const MONTHS = [{ value: "2026-07", label: "Jul 2026" }];

/**
 * Every handler row must satisfy the one published formula:
 *
 *   opening + referral earnings + salary − recoveries − paid out = closing
 *
 * These figures are the real Jul 2026 Production values, checked against
 * `stats()` before this screen was changed.
 */
const THRILOK = {
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
  prior_owed: 50000,
  prior_paid: 45000,
  prior_months: ["2026-06"],
  net_payable: 5000,
};

const PAVAN = {
  name: "Pavan Kalyan",
  count: 2,
  completed: 0,
  revenue_total: 10000,
  commission_total: 5000,
  salary_total: 0,
  auto_earnings_total: 5000,
  paid_out_total: 6000,
  recoveries_total: 0,
  prior_balance: 12000,
  prior_months: ["2026-06"],
  net_payable: 11000,
};

function renderRows(performers) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", candidates: [] }) })),
  );
  return render(
    <EarningsBreakdown
      stats={{ top_performers: performers }}
      month="2026-07"
      monthOptions={MONTHS}
      formatCurrency={fmt}
      apiBase="/api"
    />,
  );
}

async function expand(name) {
  const cell = screen.getAllByText(name).find((el) => el.closest("tr.earn-row"));
  fireEvent.click(cell.closest("tr"));
  return await waitFor(() => {
    const ledger = document.querySelector(".earn-ledger");
    expect(ledger).toBeInTheDocument();
    return ledger;
  });
}

function line(ledger, label) {
  const row = within(ledger).getByText(label).closest(".earn-ledger-row");
  return row.querySelector(".earn-ledger-value").textContent;
}

function toNumber(text) {
  const negative = text.startsWith("−") || text.startsWith("-");
  const digits = Number(String(text).replace(/[^0-9]/g, "")) || 0;
  return negative ? -digits : digits;
}

function assertLedgerAddsUp(ledger) {
  const opening = toNumber(line(ledger, "Opening balance"));
  const earnings = toNumber(line(ledger, "Referral earnings"));
  const salary = toNumber(line(ledger, "Salary"));
  const paid = toNumber(line(ledger, "Paid out"));
  const recoveries = within(ledger).queryByText("Recoveries")
    ? toNumber(line(ledger, "Recoveries"))
    : 0;
  const closing = toNumber(line(ledger, /^Closing balance/));
  expect(opening + earnings + salary + recoveries + paid).toBe(closing);
  return closing;
}

describe("handler payment calculation", () => {
  it("shows the full sum for a handler with salary and an opening balance", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");

    expect(line(ledger, "Opening balance")).toBe("+₹5,000");
    expect(line(ledger, "Referral earnings")).toBe("₹27,000");
    expect(line(ledger, "Salary")).toBe("₹15,000");
    expect(line(ledger, "Paid out")).toBe("−₹42,000");
    expect(assertLedgerAddsUp(ledger)).toBe(5000);
  });

  it("states plainly who has to pay whom", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");

    expect(ledger.querySelector(".earn-ledger-outcome").textContent).toBe(
      "Company needs to pay Thrilok ₹5,000.",
    );
  });

  it("shows a zero salary rather than omitting the line", async () => {
    renderRows([PAVAN]);
    const ledger = await expand("Pavan Kalyan");

    expect(line(ledger, "Opening balance")).toBe("+₹12,000");
    expect(line(ledger, "Referral earnings")).toBe("₹5,000");
    expect(line(ledger, "Salary")).toBe("₹0");
    expect(line(ledger, "Paid out")).toBe("−₹6,000");
    expect(assertLedgerAddsUp(ledger)).toBe(11000);
  });

  it("adds up for a settled handler", async () => {
    renderRows([{ ...PAVAN, name: "Settled One", paid_out_total: 17000, net_payable: 0 }]);
    const ledger = await expand("Settled One");

    expect(assertLedgerAddsUp(ledger)).toBe(0);
    expect(within(ledger).getByText("Settled")).toBeInTheDocument();
    expect(ledger.querySelector(".earn-ledger-outcome").textContent).toMatch(/fully settled/i);
  });

  it("adds up for an overpaid handler and says so", async () => {
    renderRows([
      { ...PAVAN, name: "Over One", prior_balance: 0, paid_out_total: 10000, net_payable: -5000 },
    ]);
    const ledger = await expand("Over One");

    expect(assertLedgerAddsUp(ledger)).toBe(-5000);
    expect(within(ledger).getByText("Overpaid")).toBeInTheDocument();
    expect(ledger.querySelector(".earn-ledger-outcome").textContent).toMatch(
      /paid ₹5,000 more than earned/i,
    );
  });

  it("adds up for a handler with no salary and no opening balance", async () => {
    renderRows([
      {
        name: "Venugopal",
        count: 3,
        commission_total: 20000,
        salary_total: 0,
        auto_earnings_total: 20000,
        paid_out_total: 0,
        recoveries_total: 0,
        prior_balance: 0,
        net_payable: 20000,
      },
    ]);
    const ledger = await expand("Venugopal");

    expect(line(ledger, "Opening balance")).toBe("₹0");
    expect(assertLedgerAddsUp(ledger)).toBe(20000);
  });

  it("keeps recoveries in the sum when there are any", async () => {
    renderRows([{ ...THRILOK, name: "Rec One", recoveries_total: 3000, net_payable: 2000 }]);
    const ledger = await expand("Rec One");

    expect(line(ledger, "Recoveries")).toBe("−₹3,000");
    expect(assertLedgerAddsUp(ledger)).toBe(2000);
  });
});

describe("terminology and status", () => {
  it("uses the renamed column headings", () => {
    const { container } = renderRows([THRILOK]);
    const heads = [...container.querySelectorAll("th")].map((th) => th.textContent);

    expect(heads.some((h) => h.startsWith("Current payable"))).toBe(true);
    expect(heads.some((h) => h.startsWith("Closing balance"))).toBe(true);
    expect(heads).not.toContain("Total Owed");
    expect(heads).not.toContain("Balance");
  });

  it("spells out the opening balance in the collapsed row instead of 'c/f'", () => {
    const { container } = renderRows([THRILOK]);
    const chip = container.querySelector(".earn-carry-fwd");

    expect(chip.textContent).toBe("Opening balance: +₹5,000");
    expect(container.textContent).not.toMatch(/c\/f/);
  });

  it("labels a positive closing balance 'To pay', never 'Owe'", () => {
    const { container } = renderRows([THRILOK]);

    expect(within(container).getAllByText("To pay").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/\bOwe\b/);
  });

  it("explains the closing balance formula on hover", () => {
    const { container } = renderRows([THRILOK]);
    const head = [...container.querySelectorAll("th")].find((th) =>
      th.textContent.startsWith("Closing balance"));

    expect(head.getAttribute("title")).toBe(
      "Opening balance + earnings + salary − paid out",
    );
    expect(head.querySelector(".earn-info")).not.toBeNull();
  });

  it("says 'Paid out' in the ledger, not 'Expenses'", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");

    expect(within(ledger).getByText("Paid out")).toBeInTheDocument();
    expect(ledger.textContent).not.toMatch(/Expenses/);
  });
});

describe("totals row agrees with the rows above it", () => {
  it("sums the closing balances including opening balances", () => {
    const { container } = renderRows([THRILOK, PAVAN]);
    const footCells = [...container.querySelectorAll(".earn-foot td")];
    const closing = footCells[footCells.length - 2].querySelector("strong").textContent;

    // 5,000 + 11,000 — the old footer showed 19,000 by dropping both openings.
    expect(closing).toBe(fmt(16000));
  });

  it("shows the combined opening balance under the total", () => {
    const { container } = renderRows([THRILOK, PAVAN]);

    expect(container.querySelector(".earn-foot .earn-carry-fwd").textContent).toBe(
      "Opening balance: +₹17,000",
    );
  });

  it("keeps the candidate breakdown inside the expanded row", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              status: "ok",
              candidates: [
                { id: "c1", name: "raju", payment: 6000, handler_commission: 3000, logged_date: "2026-07-30" },
              ],
            }),
        }),
      ),
    );
    render(
      <EarningsBreakdown
        stats={{ top_performers: [THRILOK] }}
        month="2026-07"
        monthOptions={MONTHS}
        formatCurrency={fmt}
        apiBase="/api"
      />,
    );
    const cell = screen.getAllByText("Thrilok").find((el) => el.closest("tr.earn-row"));
    fireEvent.click(cell.closest("tr"));

    const item = await screen.findByText(/raju/);
    expect(item.textContent).toContain("₹6,000 received");
    expect(item.textContent).toContain("₹3,000 referral");
    expect(item.textContent).toMatch(/30 Jul 2026/);
  });
});

/**
 * The calculation used to render as a narrow card pinned to the right of the
 * expanded row, leaving the area between the Total row and the bottom border
 * empty. It must sit in normal flow, immediately after the Total row, at full
 * width.
 */
describe("expanded row layout", () => {
  it("places the summary immediately after the Total row in document flow", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");
    const list = document.querySelector(".candidate-list");
    const total = document.querySelector(".candidate-total");

    expect(list).toBeInTheDocument();
    expect(total.closest("ul")).toBe(list);
    // The summary is the element right after the candidate list.
    expect(list.nextElementSibling).toBe(ledger);
    expect(ledger).toHaveClass("earnings-calculation-summary");
  });

  it("carries no rule that pins it to the right or reserves empty height", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");
    const style = getComputedStyle(ledger);

    expect(style.position).not.toBe("absolute");
    expect(style.position).not.toBe("fixed");
    expect(style.float).not.toBe("right");
    expect(style.marginLeft).not.toBe("auto");
    expect(["", "none", "auto", "0px"]).toContain(style.minHeight || "");
    expect(["", "none"]).toContain(style.maxWidth || "");
  });

  it("stays inside the expanded cell rather than escaping it", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");

    expect(ledger.closest("td")).not.toBeNull();
    expect(ledger.closest(".earn-detail")).not.toBeNull();
  });

  it("shows the referral earnings total on the Total row", async () => {
    renderRows([THRILOK]);
    await expand("Thrilok");
    const total = document.querySelector(".candidate-total");

    expect(total.textContent).toContain("Total (4 candidates)");
    expect(total.textContent).toContain("Referral earnings");
    expect(total.querySelector(".earn-breakdown-total-earnings strong").textContent)
      .toBe(fmt(27000));
  });

  it("puts the opening-balance reason under its own label", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");
    const openingRow = within(ledger).getByText("Opening balance").closest(".earn-ledger-row");

    expect(openingRow.querySelector(".earn-ledger-note").textContent).toMatch(
      /profile-closure complimentary from Jun 2026|unpaid from Jun 2026/,
    );
  });

  it("keeps every calculation item as a sibling in one band", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");
    const band = ledger.querySelector(".earn-ledger-rows");
    // Text nodes only, so the info glyph inside the label is ignored.
    const labels = [...band.querySelectorAll(".earn-ledger-row .earn-ledger-label")].map((el) =>
      [...el.childNodes]
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => n.textContent)
        .join("")
        .trim(),
    );

    expect(labels).toEqual([
      "Opening balance",
      "Referral earnings",
      "Salary",
      "Total earned",
      "Gross payable",
      "Paid out",
      "Closing balance",
    ]);
    // The outcome sentence sits outside the band, below it.
    expect(ledger.querySelector(".earn-ledger-outcome").parentElement).toBe(ledger);
  });
});

describe("the reason stays a sentence fragment in the data", () => {
  it("is not capitalised in the DOM, so tooltips can reuse it mid-sentence", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");
    const note = ledger.querySelector(".earn-ledger-note");

    // Capitalisation is applied by ::first-letter; the text itself is unchanged.
    expect(note.textContent.startsWith("unpaid")).toBe(true);
    expect(note.getAttribute("title")).toMatch(/^Earned /);
  });
});

/**
 * The band now carries two running subtotals, so the chain can be followed
 * step by step instead of the reader adding four figures in their head:
 *
 *   earnings + salary            = total earned
 *   opening  + total earned      = gross payable
 *   gross payable − paid out     = closing balance
 */
describe("running subtotals", () => {
  function chain(ledger) {
    const n = (label) => toNumber(line(ledger, label));
    return {
      opening: n("Opening balance"),
      earnings: n("Referral earnings"),
      salary: n("Salary"),
      totalEarned: n("Total earned"),
      gross: n("Gross payable"),
      paid: n("Paid out"),
      recoveries: within(ledger).queryByText("Recoveries") ? n("Recoveries") : 0,
      closing: n(/^Closing balance/),
    };
  }

  it("shows the reported Thrilok figures", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");

    expect(line(ledger, "Total earned")).toBe(fmt(42000));
    expect(line(ledger, "Gross payable")).toBe("+₹47,000");
  });

  it("keeps each step of the chain consistent", async () => {
    renderRows([THRILOK]);
    const c = chain(await expand("Thrilok"));

    expect(c.earnings + c.salary).toBe(c.totalEarned);
    expect(c.opening + c.totalEarned).toBe(c.gross);
    expect(c.gross + c.paid + c.recoveries).toBe(c.closing);
  });

  it("matches Current payable in the collapsed row", async () => {
    const { container } = renderRows([THRILOK]);
    const ledger = await expand("Thrilok");
    const cells = [...container.querySelectorAll("tr.earn-row td")].map((td) => td.textContent);

    // Total earned is the same figure the Current payable column reports.
    expect(cells).toContain(fmt(42000));
    expect(line(ledger, "Total earned")).toBe(fmt(42000));
  });

  it("holds for a handler with no salary", async () => {
    renderRows([PAVAN]);
    const c = chain(await expand("Pavan Kalyan"));

    expect(c.totalEarned).toBe(5000);
    expect(c.gross).toBe(17000);
    expect(c.closing).toBe(11000);
    expect(c.earnings + c.salary).toBe(c.totalEarned);
    expect(c.opening + c.totalEarned).toBe(c.gross);
    expect(c.gross + c.paid).toBe(c.closing);
  });

  it("holds when the handler is overpaid", async () => {
    renderRows([
      { ...PAVAN, name: "Over One", prior_balance: 0, paid_out_total: 10000, net_payable: -5000 },
    ]);
    const c = chain(await expand("Over One"));

    expect(c.gross).toBe(5000);
    expect(c.closing).toBe(-5000);
    expect(c.gross + c.paid).toBe(c.closing);
  });

  it("keeps recoveries after paid out and still balances", async () => {
    renderRows([{ ...THRILOK, name: "Rec One", recoveries_total: 3000, net_payable: 2000 }]);
    const ledger = await expand("Rec One");
    const c = chain(ledger);

    expect(c.totalEarned).toBe(42000);
    expect(c.gross).toBe(47000);
    expect(c.gross + c.paid + c.recoveries).toBe(c.closing);

    const order = [...ledger.querySelectorAll(".earn-ledger-row")].map((el) =>
      el.querySelector(".earn-ledger-label").textContent.replace(/i$/, "").trim(),
    );
    expect(order.indexOf("Recoveries")).toBeGreaterThan(order.indexOf("Paid out"));
    expect(order.indexOf("Recoveries")).toBeLessThan(order.indexOf("Closing balance"));
  });

  it("marks the subtotals as results rather than inputs", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");

    for (const label of ["Total earned", "Gross payable"]) {
      const row = within(ledger).getByText(label).closest(".earn-ledger-row");
      expect(row).toHaveClass("earn-ledger-row--subtotal");
    }
  });

  it("explains how each subtotal is reached", async () => {
    renderRows([THRILOK]);
    const ledger = await expand("Thrilok");

    expect(within(ledger).getByText("Total earned").getAttribute("title"))
      .toBe("Referral earnings + salary");
    expect(within(ledger).getByText("Gross payable").getAttribute("title"))
      .toBe("Opening balance + total earned");
  });
});
