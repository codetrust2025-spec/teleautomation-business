import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../context/ConfirmContext.jsx", () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}));
vi.mock("../context/AuthContext.jsx", () => ({
  useAuth: () => ({ role: "admin", reference: "", enabled: false }),
}));
vi.mock("../dailyOps/PendingWorksProvider.jsx", () => ({
  consumePendingWorkOpenIntent: () => null,
}));

import { CandidatesPanel } from "./candidatesModule.jsx";

const STATS = {
  status: "ok",
  stats: { pending_count: 0, top_performers: [], references: [] },
};

let calls;

function mockFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url) => {
      calls.push(String(url));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ ...STATS, candidates: [] }),
      });
    }),
  );
}

/** Every /candidates list request issued so far, as parsed query strings. */
function listQueries() {
  return calls
    .filter((u) => /\/candidates\?/.test(u) || /\/candidates$/.test(u))
    .map((u) => new URL(u, "http://localhost").searchParams);
}

async function renderPanel() {
  const view = render(<CandidatesPanel />);
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  return view;
}

beforeEach(() => {
  mockFetch();
  // The dropdown was gated on this flag, so the flag is on for these tests:
  // its absence must come from the code, not from the feature being disabled.
  window.__TA_AI_RECRUITMENT_ENABLED__ = true;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.__TA_AI_RECRUITMENT_ENABLED__;
});

/**
 * The toolbar used to carry an injected "All AI statuses" dropdown that
 * restated, in a second vocabulary, what the Stage column already showed.
 */
describe("Candidates toolbar — AI status filter removed", () => {
  it("renders no AI status dropdown even with AI recruitment enabled", async () => {
    await renderPanel();

    expect(
      document.querySelector("[data-ai-recruitment-filter]"),
    ).toBeNull();
    expect(
      screen.queryByLabelText("Filter by AI recruitment status"),
    ).toBeNull();
    const optionText = [...document.querySelectorAll("option")].map(
      (o) => o.textContent,
    );
    expect(optionText).not.toContain("All AI statuses");
    expect(optionText).not.toContain("Mailbox connected");
    expect(optionText).not.toContain("Potential conflict");
  });

  it("never sends ai_filter to the candidates API", async () => {
    await renderPanel();

    await act(async () => {
      await Promise.resolve();
    });
    expect(calls.some((u) => u.includes("ai_filter"))).toBe(false);
    for (const params of listQueries()) {
      expect(params.has("ai_filter")).toBe(false);
    }
  });
});

/**
 * Removing one control must not disturb the others: each remaining filter
 * still renders and still reaches the API under its own parameter name.
 */
describe("Candidates toolbar — surviving filters", () => {
  it("keeps every other filter control in the toolbar", async () => {
    await renderPanel();

    const toolbar = document.querySelector(".cand-toolbar");
    expect(toolbar).not.toBeNull();
    expect(
      toolbar.querySelector(".cand-input--search"),
    ).not.toBeNull();
    expect(
      toolbar.querySelector('[aria-label="Filter by service type"]'),
    ).not.toBeNull();
    expect(
      toolbar.querySelector('[aria-label="Filter by month"]'),
    ).not.toBeNull();
    expect(
      toolbar.querySelector('[aria-label="Filter by handler / reference"]'),
    ).not.toBeNull();
    expect(
      toolbar.querySelector('input[type="checkbox"]'),
    ).not.toBeNull();
    expect(screen.getByText(/Active list/)).toBeTruthy();
    // search + service + month + stage + reference, and nothing extra
    expect(toolbar.querySelectorAll("select.cand-input")).toHaveLength(4);
  });

  it("still filters by service type", async () => {
    await renderPanel();

    fireEvent.change(screen.getByLabelText("Filter by service type"), {
      target: { value: "round_wise" },
    });

    await waitFor(() =>
      expect(
        listQueries().some(
          (p) => p.get("service_type") === "round_wise" && !p.has("ai_filter"),
        ),
      ).toBe(true),
    );
  });

  it("still filters by month", async () => {
    await renderPanel();

    const monthSelect = screen.getByLabelText("Filter by month");
    fireEvent.change(monthSelect, { target: { value: "all" } });

    await waitFor(() =>
      expect(listQueries().some((p) => !p.has("month"))).toBe(true),
    );
  });

  it("still filters by pending only", async () => {
    const { container } = await renderPanel();

    fireEvent.click(
      container.querySelector('.cand-toolbar input[type="checkbox"]'),
    );

    await waitFor(() =>
      expect(listQueries().some((p) => p.get("pending_only") === "1")).toBe(
        true,
      ),
    );
  });

  it("still searches", async () => {
    await renderPanel();

    fireEvent.change(document.querySelector(".cand-input--search"), {
      target: { value: "Thrilok" },
    });

    await waitFor(
      () =>
        expect(
          listQueries().some((p) => p.get("search") === "Thrilok"),
        ).toBe(true),
      { timeout: 2000 },
    );
  });
});
