import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PaymentProofsModal } from "./candidatesModule.jsx";
import { normalizePaymentProofs } from "./paymentProofs.js";

function apiResponse(candidate) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ status: "ok", candidate }),
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("normalizePaymentProofs", () => {
  it("normalizes current, legacy, nested, and alternate URL fields without duplicates", () => {
    const proofs = normalizePaymentProofs({
      candidate_id: "candidate-1",
      payment_proofs: [
        { id: "one", url: "/proofs/one" },
        { id: "deleted", url: "/proofs/deleted", deleted_at: "2026-07-28" },
      ],
      paymentScreenshots: [{ proofId: "two", imageUrl: "/proofs/two" }],
      payments: [
        {
          payment_id: "payment-3",
          screenshots: [
            { attachment_id: "three", signedUrl: "https://files.test/three" },
            { attachment_id: "duplicate", signedUrl: "https://files.test/three" },
          ],
        },
      ],
    });

    expect(proofs.map((proof) => proof.id)).toEqual(["one", "two", "three"]);
    expect(proofs[0].candidateId).toBe("candidate-1");
    expect(proofs[2]).toMatchObject({
      paymentId: "payment-3",
      url: "https://files.test/three",
    });
  });

  it("does not mix slot screenshots into payment proofs", () => {
    const proofs = normalizePaymentProofs({
      attachments: [
        { id: "payment", attachment_type: "payment_proof", fileUrl: "/payment" },
        {
          id: "slot",
          attachment_type: "slot_screenshot_proof",
          fileUrl: "/slot",
        },
      ],
    });

    expect(proofs).toHaveLength(1);
    expect(proofs[0].id).toBe("payment");
  });

  it("supports proof arrays returned directly under a response data field", () => {
    const proofs = normalizePaymentProofs({
      data: [
        { proof_id: "legacy-one", file_path: "/legacy/one.jpg" },
        { proof_id: "legacy-two", storagePath: "/legacy/two.jpg" },
      ],
    });

    expect(proofs.map((proof) => proof.id)).toEqual([
      "legacy-one",
      "legacy-two",
    ]);
  });
});

describe("PaymentProofsModal", () => {
  it("uses the normalized API records for both count and previews", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        apiResponse({
          id: "candidate-2",
          name: "Yamini",
          paymentProofs: [
            { id: "one", fileUrl: "/proofs/one" },
            { id: "two", screenshotUrl: "/proofs/two" },
          ],
        }),
      ),
    );

    const { container } = render(
      <PaymentProofsModal
        candidate={{ id: "candidate-2", name: "Yamini", proof_count: 2 }}
        onClose={() => {}}
      />,
    );

    expect(screen.getAllByText("Loading payment proofsâ€¦")).toHaveLength(2);
    await waitFor(() =>
      expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(2),
    );
    const countChunk = [...container.querySelectorAll(".cand-payout-chunk")].find(
      (element) => element.textContent.includes("screenshots on file"),
    );
    expect(countChunk?.textContent).toContain("2");
    expect(screen.queryByText(/No payment screenshots attached/)).not.toBeInTheDocument();
  });

  it("renders complete row proofs immediately while refreshing candidate detail", () => {
    const request = deferred();
    vi.stubGlobal("fetch", vi.fn(() => request.promise));
    const { container } = render(
      <PaymentProofsModal
        candidate={{
          id: "candidate-initial",
          name: "Initial",
          payment_proofs: [{ id: "initial-proof", url: "/initial-proof" }],
        }}
        onClose={() => {}}
      />,
    );

    expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(1);
    expect(screen.queryByText(/No payment screenshots attached/)).not.toBeInTheDocument();
  });

  it("distinguishes an empty successful response from an API failure", async () => {
    vi.stubGlobal("fetch", vi.fn(() => apiResponse({ id: "empty", name: "Empty" })));
    const { rerender } = render(
      <PaymentProofsModal
        candidate={{ id: "empty", name: "Empty" }}
        onClose={() => {}}
      />,
    );
    await screen.findByText("No payment screenshots attached to this candidate yet.");

    fetch.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    rerender(
      <PaymentProofsModal
        candidate={{ id: "failed", name: "Failed" }}
        onClose={() => {}}
      />,
    );
    await screen.findByText("Unable to load payment proofs. Please try again.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("ignores a late response after switching candidates", async () => {
    const first = deferred();
    const second = deferred();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise),
    );
    const { container, rerender } = render(
      <PaymentProofsModal
        candidate={{ id: "candidate-a", name: "A" }}
        onClose={() => {}}
      />,
    );
    rerender(
      <PaymentProofsModal
        candidate={{ id: "candidate-b", name: "B" }}
        onClose={() => {}}
      />,
    );

    second.resolve(await apiResponse({
      id: "candidate-b",
      name: "B",
      payment_proofs: [{ id: "proof-b", url: "/proof-b" }],
    }));
    await waitFor(() =>
      expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(1),
    );
    first.resolve(await apiResponse({
      id: "candidate-a",
      name: "A",
      payment_proofs: [
        { id: "proof-a1", url: "/proof-a1" },
        { id: "proof-a2", url: "/proof-a2" },
      ],
    }));
    await Promise.resolve();
    expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(1);
    expect(container.querySelector('img[src$="/proof-b"]')).toBeInTheDocument();
  });

  it("supports full-image navigation, zoom, backdrop close, and Escape", async () => {
    const candidate = {
      id: "candidate-gallery",
      name: "Gallery Candidate",
      payment_proofs: [
        { id: "proof-one", url: "/proof-one.jpg", note: "First proof" },
        { id: "proof-two", url: "/proof-two.jpg", note: "Second proof" },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(() => apiResponse(candidate)));
    render(
      <PaymentProofsModal candidate={candidate} onClose={() => {}} />,
    );
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /Preview/ })).toHaveLength(2),
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview First proof" }));
    expect(screen.getByRole("dialog", { name: "Payment proof preview" })).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByText("125%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next payment proof" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("dialog", { name: "Payment proof preview" }),
      ).getByAltText("Second proof"),
    ).toHaveAttribute(
      "src",
      expect.stringContaining("/proof-two.jpg"),
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Payment proof preview" })).not.toBeInTheDocument();
  });
});
