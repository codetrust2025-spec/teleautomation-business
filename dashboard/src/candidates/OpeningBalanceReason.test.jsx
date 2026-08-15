import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import EarningsBreakdown from "./EarningsBreakdown.jsx";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fmt = (value) => `₹${Number(value).toLocaleString("en-IN")}`;

// Thrilok, Jul 2026: 45,000 earned and 40,000 paid before July leaves 5,000
// carried in, on top of a July that nets to zero.
const THRILOK = {
  name: "Thrilok",
  count: 4,
  commission_total: 27000,
  salary_total: 15000,
  auto_earnings_total: 42000,
  paid_out_total: 42000,
  recoveries_total: 0,
  prior_balance: 5000,
  prior_owed: 45000,
  prior_paid: 40000,
  prior_recoveries: 0,
  prior_months: ["2026-06"],
  net_payable: 5000,
};

async function openRow(performer) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", candidates: [] }) }),
    ),
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
  // The opening balance and its explanation now live in the calculation
  // ledger below the candidate list rather than in the old summary strip.
  const ledger = await waitFor(() => {
    const el = document.querySelector(".earn-ledger");
    expect(el).toBeInTheDocument();
    return el;
  });
  return { ledger, opening: ledger };
}

describe("opening balance states its reason", () => {
  it("names the month the balance was carried from", async () => {
    const { opening } = await openRow(THRILOK);
    expect(opening.textContent).toContain("unpaid from Jun 2026");
  });

  it("shows the earned/paid split behind the figure", async () => {
    const { opening } = await openRow(THRILOK);
    const note = opening.querySelector(".earn-ledger-note");
    expect(note.getAttribute("title")).toBe("Earned ₹45,000 · paid ₹40,000 before Jul 2026");
  });

  it("names a range when several months contributed", async () => {
    const { opening } = await openRow({
      ...THRILOK,
      prior_months: ["2026-03", "2026-06"],
    });
    expect(opening.textContent).toContain("unpaid from Mar 2026 – Jun 2026");
  });

  it("includes recoveries in the explanation when there were any", async () => {
    const { opening } = await openRow({
      ...THRILOK,
      prior_recoveries: 3000,
      prior_balance: 2000,
      net_payable: 2000,
    });
    const note = opening.querySelector(".earn-ledger-note");
    expect(note.getAttribute("title")).toContain("recovered ₹3,000");
  });

  it("says overpaid when the referrer carried a negative balance", async () => {
    const { opening } = await openRow({
      ...THRILOK,
      prior_balance: -5000,
      prior_paid: 50000,
      net_payable: -5000,
    });
    expect(opening.textContent).toContain("overpaid in Jun 2026");
  });

  it("still explains itself when the backend sent no months", async () => {
    const { opening } = await openRow({ ...THRILOK, prior_months: [] });
    expect(opening.textContent).toContain("unpaid from earlier months");
  });

  it("keeps the collapsed row to the amount alone", async () => {
    const { opening } = await openRow(THRILOK);
    const chip = document.querySelector(".earn-carry-fwd");

    // The reason needs room to be read, so it lives in the expanded
    // calculation only — the collapsed row states the figure and nothing else.
    expect(chip.textContent).toBe("Opening balance: +₹5,000");
    expect(chip.getAttribute("title")).toBeNull();
    expect(opening.textContent).toContain("unpaid from Jun 2026");
  });
});

// A profile closure grants the closure admin a complimentary amount, sometimes
// on another handler's candidate. Calling that "unpaid commission" tells the
// wrong story, so the label names it.
describe("opening balance made of an unpaid closure complimentary", () => {
  const CLOSURE = {
    ...THRILOK,
    prior_owed: 50000,
    prior_paid: 45000,
    prior_balance: 5000,
    prior_complimentary: 5000,
    prior_complimentary_count: 1,
  };

  it("calls it a profile-closure complimentary, not generic arrears", async () => {
    const { opening } = await openRow(CLOSURE);
    expect(opening.textContent).toContain("unpaid profile-closure complimentary from Jun 2026");
    expect(opening.textContent).not.toMatch(/^.*\bunpaid from\b/);
  });

  it("names the complimentary amount in the hover detail", async () => {
    const { opening } = await openRow(CLOSURE);
    expect(opening.querySelector(".earn-ledger-note").getAttribute("title")).toBe(
      "Earned ₹50,000 · incl. ₹5,000 profile-closure complimentary · paid ₹45,000 before Jul 2026",
    );
  });

  it("marks it visually as a different kind of debt", async () => {
    const { opening } = await openRow(CLOSURE);
    expect(opening.querySelector(".earn-summary-note--complimentary")).not.toBeNull();
  });

  it("counts multiple closures in the detail", async () => {
    const { opening } = await openRow({
      ...CLOSURE,
      prior_complimentary: 10000,
      prior_complimentary_count: 2,
      prior_owed: 55000,
      prior_balance: 10000,
      net_payable: 10000,
    });
    expect(opening.querySelector(".earn-ledger-note").getAttribute("title")).toContain(
      "incl. ₹10,000 profile-closure complimentary (2 closures)",
    );
  });

  it("stays generic when the balance exceeds the complimentary granted", async () => {
    const { opening } = await openRow({
      ...CLOSURE,
      prior_owed: 70000,
      prior_balance: 25000,
      net_payable: 25000,
    });
    expect(opening.textContent).toContain("unpaid from Jun 2026");
    expect(opening.textContent).not.toContain("profile-closure complimentary from");
    // The complimentary is still disclosed, just not claimed as the whole
    // reason — and it is disclosed on the row, because leaving it in a tooltip
    // made the bonus look like something only the closure admin received.
    expect(opening.querySelector(".earn-ledger-note").getAttribute("title")).toContain(
      "incl. ₹5,000 profile-closure complimentary",
    );
    expect(opening.textContent).toContain("Includes ₹5,000 profile-closure complimentary");
  });

  it("does not call an overpayment a complimentary", async () => {
    const { opening } = await openRow({
      ...CLOSURE,
      prior_paid: 55000,
      prior_balance: -5000,
      net_payable: -5000,
    });
    expect(opening.textContent).toContain("overpaid in Jun 2026");
  });
});

// A referrer's complimentary arrives alongside their commission, so it is
// never the whole balance. Naming it only in a tooltip made it look as though
// the closure admin was the only one who received the bonus.
describe("opening balance mixing commission and closure complimentary", () => {
  // Pavan Kalyan: ₹11,000 commission + ₹5,000 complimentary earned before
  // July, ₹4,000 paid, leaving ₹12,000 carried in.
  const REFERRER = {
    ...THRILOK,
    name: "Pavan Kalyan",
    prior_owed: 16000,
    prior_paid: 4000,
    prior_balance: 12000,
    prior_complimentary: 5000,
    prior_complimentary_count: 1,
    prior_months: ["2026-06", "2026-07"],
    net_payable: 12000,
  };

  it("says the balance is unpaid arrears, since it is not only a bonus", async () => {
    const { opening } = await openRow(REFERRER);
    expect(opening.textContent).toContain("unpaid from Jun 2026 – Jul 2026");
  });

  it("still names the complimentary on the row, not only on hover", async () => {
    const { opening } = await openRow(REFERRER);
    expect(opening.textContent).toContain(
      "Includes ₹5,000 profile-closure complimentary",
    );
  });

  it("marks the complimentary line as its own kind of debt", async () => {
    const { opening } = await openRow(REFERRER);
    const note = [...opening.querySelectorAll(".earn-summary-note--complimentary")]
      .find((el) => el.textContent.startsWith("Includes"));
    expect(note).not.toBeUndefined();
  });

  it("counts the closures when a handler earned more than one", async () => {
    const { opening } = await openRow({
      ...REFERRER, prior_complimentary: 10000, prior_complimentary_count: 2,
    });
    expect(opening.textContent).toContain(
      "Includes ₹10,000 profile-closure complimentary (2 closures)",
    );
  });

  it("does not repeat itself when the balance is complimentary all the way", async () => {
    // The line above already says the balance is nothing else.
    const { opening } = await openRow({
      ...THRILOK,
      prior_owed: 50000, prior_paid: 45000, prior_balance: 5000,
      prior_complimentary: 5000, prior_complimentary_count: 1,
    });
    expect(opening.textContent).toContain("unpaid profile-closure complimentary");
    expect(opening.textContent).not.toContain("Includes ₹5,000");
  });

  it("says nothing extra when no complimentary was earned", async () => {
    const { opening } = await openRow(THRILOK);
    expect(opening.textContent).not.toContain("Includes");
    expect(opening.textContent).not.toContain("profile-closure complimentary");
  });

  it("leaves the figures alone", async () => {
    const { ledger } = await openRow(REFERRER);
    // Opening balance and every derived subtotal are untouched by the label.
    expect(ledger.textContent).toContain("+₹12,000");
  });
});
