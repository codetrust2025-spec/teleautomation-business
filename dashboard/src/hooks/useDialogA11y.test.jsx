import React, { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useDialogA11y, focusableWithin } from "./useDialogA11y.js";

afterEach(cleanup);

/** A dialog shaped like the hand-written ones in this app. */
function Harness({ onClose = () => {}, withFields = true, closeOnEscape }) {
  const [open, setOpen] = useState(false);
  const close = () => { setOpen(false); onClose(); };
  const ref = useDialogA11y(open, close, closeOnEscape === undefined ? {} : { closeOnEscape });
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>Open payout</button>
      <button type="button">Background control</button>
      {open && (
        <div ref={ref} role="dialog" aria-modal="true" aria-label="Payout">
          {withFields && <input aria-label="Amount" />}
          {withFields && <input aria-label="Note" />}
          <button type="button" onClick={close}>Close</button>
        </div>
      )}
    </div>
  );
}

const openDialog = () => fireEvent.click(screen.getByText("Open payout"));

describe("focus moves into the dialog", () => {
  it("focuses the first focusable control when it opens", async () => {
    render(<Harness />);
    openDialog();
    await vi.waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("Amount")));
  });

  it("focuses the dialog itself when it has no focusable children", async () => {
    render(<Harness withFields={false} />);
    openDialog();
    // Only the close button is focusable in that case.
    await vi.waitFor(() =>
      expect(document.activeElement).toBe(screen.getByText("Close")));
  });
});

describe("Tab stays inside the dialog", () => {
  it("wraps forward from the last control to the first", async () => {
    render(<Harness />);
    openDialog();
    const dialog = screen.getByRole("dialog");
    const [first] = focusableWithin(dialog);
    const last = focusableWithin(dialog).slice(-1)[0];

    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
  });

  it("wraps backward from the first control to the last", async () => {
    render(<Harness />);
    openDialog();
    const dialog = screen.getByRole("dialog");
    const items = focusableWithin(dialog);

    items[0].focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(items[items.length - 1]);
  });

  it("pulls focus back when it has escaped to the background", () => {
    render(<Harness />);
    openDialog();
    screen.getByText("Background control").focus();

    fireEvent.keyDown(document, { key: "Tab" });

    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });
});

describe("Escape", () => {
  it("closes the dialog", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    openDialog();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("can be opted out of for dialogs that must not close on Escape", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} closeOnEscape={false} />);
    openDialog();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("focus returns to the trigger", () => {
  it("restores focus to the exact element that opened the dialog", async () => {
    render(<Harness />);
    const trigger = screen.getByText("Open payout");
    trigger.focus();
    fireEvent.click(trigger);
    await vi.waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    fireEvent.keyDown(document, { key: "Escape" });

    expect(document.activeElement).toBe(trigger);
  });

  it("does not throw when the trigger has been removed from the document", async () => {
    function Vanishing() {
      const [open, setOpen] = useState(false);
      const [showTrigger, setShowTrigger] = useState(true);
      const ref = useDialogA11y(open, () => setOpen(false));
      return (
        <div>
          {showTrigger && (
            <button type="button" onClick={() => { setOpen(true); setShowTrigger(false); }}>
              Open
            </button>
          )}
          {open && (
            <div ref={ref} role="dialog" aria-modal="true" aria-label="X">
              <button type="button" onClick={() => setOpen(false)}>Close</button>
            </div>
          )}
        </div>
      );
    }
    render(<Vanishing />);
    fireEvent.click(screen.getByText("Open"));
    await vi.waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    expect(() => fireEvent.keyDown(document, { key: "Escape" })).not.toThrow();
  });
});

describe("listeners do not outlive the dialog", () => {
  it("stops handling Escape once closed", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    openDialog();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
