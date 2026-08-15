import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import EarningsBreakdown from "./EarningsBreakdown.jsx";

function jsonResponse(payload) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(payload),
  });
}

function renderBreakdown(candidates, onViewPaymentProofs = vi.fn(), performerOverrides = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => jsonResponse({ status: "ok", candidates })),
  );
  render(
    <EarningsBreakdown
      stats={{
        top_performers: [
          {
            name: "Referrer One",
            count: candidates.length,
            commission_total: 10000,
            auto_earnings_total: 10000,
            paid_out_total: 0,
            net_payable: 10000,
            ...performerOverrides,
          },
        ],
      }}
      month="2026-06"
      formatCurrency={(value) => `₹${Number(value).toLocaleString("en-IN")}`}
      apiBase="/api"
      onViewPaymentProofs={onViewPaymentProofs}
    />,
  );
  const handlerName = screen
    .getAllByText("Referrer One")
    .find((element) => element.closest("tr.earn-row"));
  fireEvent.click(handlerName.closest("tr"));
  return onViewPaymentProofs;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EarningsBreakdown payment-proof action", () => {
  it("opens the shared viewer with the exact candidate and normalized proofs", async () => {
    const onView = renderBreakdown([
      {
        id: "candidate-ram",
        name: "Ram Charan M S",
        payment: 20000,
        payment_proofs: [
          { id: "proof-1", imageUrl: "/proofs/ram-1.jpg" },
          { id: "proof-2", signedUrl: "https://files.example/ram-2.jpg" },
        ],
      },
    ]);

    const button = await screen.findByRole("button", {
      name: "View payment proofs for Ram Charan M S",
    });
    expect(button).toHaveAttribute("title", "View 2 payment proofs");
    fireEvent.click(button);

    expect(onView).toHaveBeenCalledTimes(1);
    expect(onView).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "candidate-ram",
        name: "Ram Charan M S",
        payment_proofs: [
          expect.objectContaining({ id: "proof-1", url: "/proofs/ram-1.jpg" }),
          expect.objectContaining({
            id: "proof-2",
            url: "https://files.example/ram-2.jpg",
          }),
        ],
      }),
    );
  });

  it("keeps a count-backed action usable so the shared modal can fetch details", async () => {
    const onView = renderBreakdown([
      {
        id: "candidate-pavan",
        name: "Pavan Ravi",
        payment: 20000,
        proof_count: 2,
        payment_proofs: [],
      },
    ]);

    const button = await screen.findByRole("button", {
      name: "View payment proofs for Pavan Ravi",
    });
    fireEvent.click(button);

    expect(onView).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "candidate-pavan",
        name: "Pavan Ravi",
        payment_proofs: [],
      }),
    );
    expect(button).toHaveAttribute("title", "View 2 payment proofs");
  });

  it("hides the photo action when no proof source reports screenshots", async () => {
    renderBreakdown([
      {
        id: "candidate-empty",
        name: "No Proof Candidate",
        payment: 5000,
        payment_proofs: [],
        proof_count: 0,
      },
    ]);

    await screen.findByText(
      (_, element) =>
        element?.classList.contains("earn-breakdown-desc") &&
        element.textContent.includes("No Proof Candidate"),
    );
    expect(
      screen.queryByRole("button", {
        name: "View payment proofs for No Proof Candidate",
      }),
    ).not.toBeInTheDocument();
  });

  it("shows completed-profile complimentary earnings separately", async () => {
    renderBreakdown(
      [],
      vi.fn(),
      {
        commission_total: 15000,
        complimentary_total: 5000,
        admin_complimentary_total: 5000,
        admin_complimentary_count: 1,
        auto_earnings_total: 15000,
        net_payable: 15000,
      },
    );

    expect(await screen.findByText("incl. ₹5,000 complimentary")).toBeInTheDocument();
    expect(screen.getByText("Admin complimentary · 1 completed profile")).toBeInTheDocument();
  });
});
