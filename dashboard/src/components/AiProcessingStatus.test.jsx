import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import AiProcessingStatus, { AI_STAGE_MESSAGES } from "./AiProcessingStatus.jsx";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubReducedMotion(reduce) {
  vi.stubGlobal("matchMedia", (query) => ({
    matches: reduce && query.includes("prefers-reduced-motion"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

describe("AiProcessingStatus", () => {
  it("announces status politely rather than interrupting", () => {
    render(<AiProcessingStatus state="processing" title="Verifying screenshot" />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status.getAttribute("aria-label")).toMatch(/Verifying screenshot/);
  });

  it("drops aria-busy once work has finished", () => {
    render(<AiProcessingStatus state="success" />);
    expect(screen.getByRole("status")).not.toHaveAttribute("aria-busy");
  });

  it.each([
    ["queued", /Waiting for an AI slot/i],
    ["retrying", /Trying again/i],
    ["success", /finished successfully/i],
    ["timeout", /did not answer in time/i],
    ["error", /could not complete/i],
  ])("renders human copy for the %s state", (state, expected) => {
    render(<AiProcessingStatus state={state} />);
    expect(screen.getByRole("status").textContent).toMatch(expected);
  });

  it("rotates the stage messages while processing", () => {
    stubReducedMotion(false);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    render(<AiProcessingStatus state="processing" />);
    expect(screen.getByRole("status").textContent).toContain(AI_STAGE_MESSAGES[0]);
    act(() => { vi.advanceTimersByTime(2600); });
    expect(screen.getByRole("status").textContent).toContain(AI_STAGE_MESSAGES[1]);
    act(() => { vi.advanceTimersByTime(2600); });
    expect(screen.getByRole("status").textContent).toContain(AI_STAGE_MESSAGES[2]);
  });

  it("holds the message still when reduced motion is requested", () => {
    stubReducedMotion(true);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    render(<AiProcessingStatus state="processing" />);
    const before = screen.getByRole("status").textContent;
    act(() => { vi.advanceTimersByTime(10000); });
    expect(screen.getByRole("status").textContent).toBe(before);
    expect(screen.getByRole("status").className).toContain("aips--still");
  });

  it("never invents progress when none was supplied", () => {
    render(<AiProcessingStatus state="processing" />);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByRole("status").textContent).not.toMatch(/\d+\s*%/);
  });

  it("shows a meter only for real progress, clamped to 0-100", () => {
    const { rerender } = render(<AiProcessingStatus state="processing" progress={42} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    rerender(<AiProcessingStatus state="processing" progress={180} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("offers Retry and Cancel only after a failure", () => {
    const onRetry = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <AiProcessingStatus state="processing" onRetry={onRetry} onCancel={onCancel} />,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

    rerender(<AiProcessingStatus state="timeout" onRetry={onRetry} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("renders a checkmark on success and a title only in card mode", () => {
    const { container, rerender } = render(
      <AiProcessingStatus state="success" variant="card" title="Verifying screenshot" />,
    );
    expect(container.querySelector(".aips__check")).toBeTruthy();
    expect(screen.getByText("Verifying screenshot")).toBeInTheDocument();

    rerender(<AiProcessingStatus state="success" variant="inline" title="Verifying screenshot" />);
    // Inline mode is for buttons and form rows: the label stays in aria only.
    expect(screen.queryByText("Verifying screenshot")).not.toBeInTheDocument();
    expect(screen.getByRole("status").className).toContain("aips--inline");
  });

  it("lets a caller override the message", () => {
    render(<AiProcessingStatus state="processing" message="Reading the invite…" />);
    expect(screen.getByRole("status").textContent).toContain("Reading the invite…");
  });
});

describe("AiProcessingStatus mode badge", () => {
  it("shows OCR + AI when that is the active mode", () => {
    render(<AiProcessingStatus state="processing" mode="ocr+ai" />);
    expect(screen.getByText("OCR + AI")).toBeInTheDocument();
  });

  it("shows AI only when OCR is globally off", () => {
    render(<AiProcessingStatus state="processing" mode="ai" />);
    expect(screen.getByText("AI only")).toBeInTheDocument();
  });

  it("omits the badge when no mode is supplied", () => {
    render(<AiProcessingStatus state="processing" />);
    expect(screen.queryByText("OCR + AI")).not.toBeInTheDocument();
    expect(screen.queryByText("AI only")).not.toBeInTheDocument();
  });

  it("includes the mode in the accessible label", () => {
    render(<AiProcessingStatus state="processing" mode="ai" title="Reading invite" />);
    expect(screen.getByRole("status").getAttribute("aria-label")).toMatch(/AI only/);
  });
});

describe("elapsed time", () => {
  it("counts up while the work is in flight", () => {
    vi.useFakeTimers();
    render(<AiProcessingStatus state="processing" title="Reading invite" />);

    expect(screen.getByText("0.0s")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(4200);
    });

    expect(screen.getByText("4.2s")).toBeTruthy();
  });

  it("keeps the final number after the work finishes, so it can still be read", () => {
    vi.useFakeTimers();
    const view = render(<AiProcessingStatus state="processing" title="Reading invite" />);

    act(() => {
      vi.advanceTimersByTime(3500);
    });
    view.rerender(<AiProcessingStatus state="success" title="Reading invite" />);

    // Frozen, not cleared and not still ticking.
    expect(screen.getByText("3.5s")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(5500);
    });
    expect(screen.getByText("3.5s")).toBeTruthy();
  });

  it("keeps counting through a retry, because the wait is what matters", () => {
    vi.useFakeTimers();
    const view = render(<AiProcessingStatus state="processing" title="Reading invite" />);

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    view.rerender(<AiProcessingStatus state="retrying" title="Reading invite" />);
    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByText("5.0s")).toBeTruthy();
  });

  it("reads minutes and seconds once past a minute", () => {
    vi.useFakeTimers();
    render(<AiProcessingStatus state="processing" title="Reading invite" />);

    act(() => {
      vi.advanceTimersByTime(171000);
    });

    expect(screen.getByText("2m 51s")).toBeTruthy();
  });

  it("can be turned off by the caller", () => {
    vi.useFakeTimers();
    render(<AiProcessingStatus state="processing" title="Reading invite" showElapsed={false} />);
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.queryByText(/\ds$/)).toBeNull();
  });

  it("ticks in whole seconds when motion is reduced", () => {
    stubReducedMotion(true);
    vi.useFakeTimers();
    render(<AiProcessingStatus state="processing" title="Reading invite" />);

    // A tenth of a second must not redraw the number under reduced motion.
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(screen.getByText("0.0s")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(screen.getByText("1.0s")).toBeTruthy();
  });

  it("includes the elapsed time in the accessible label", () => {
    vi.useFakeTimers();
    render(<AiProcessingStatus state="processing" title="Reading invite" />);
    act(() => {
      vi.advanceTimersByTime(6000);
    });

    expect(screen.getByRole("status").getAttribute("aria-label")).toContain("6.0s");
  });
});

describe("caller-supplied elapsed time", () => {
  it("survives being swapped for a different element", () => {
    // The real defect: the processing card and the success strip are separate
    // elements, so the card unmounts and any timer inside it dies exactly when
    // the number becomes worth reading. A supplied value has no such problem.
    const view = render(
      <AiProcessingStatus variant="card" state="processing" title="Reading invite" elapsedMs={4200} />,
    );
    expect(screen.getByText("4.2s")).toBeTruthy();

    view.rerender(
      <AiProcessingStatus
        variant="inline"
        state="success"
        title="Reading invite"
        message="AI reading completed"
        elapsedMs={4200}
      />,
    );

    expect(screen.getByText("AI reading completed")).toBeTruthy();
    expect(screen.getByText("4.2s")).toBeTruthy();
  });

  it("is shown on a failure too, so a slow timeout is visible", () => {
    render(
      <AiProcessingStatus state="error" title="Reading invite" message="Nope" elapsedMs={31500} />,
    );
    expect(screen.getByText("31.5s")).toBeTruthy();
  });

  it("does not run its own clock when a value is supplied", () => {
    vi.useFakeTimers();
    render(<AiProcessingStatus state="processing" title="Reading invite" elapsedMs={1000} />);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    // Still the supplied figure — the caller owns the clock.
    expect(screen.getByText("1.0s")).toBeTruthy();
  });

  it("falls back to timing itself when no value is supplied", () => {
    vi.useFakeTimers();
    render(<AiProcessingStatus state="processing" title="Reading invite" />);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByText("2.0s")).toBeTruthy();
  });
});
