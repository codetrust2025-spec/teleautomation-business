import { useCallback, useEffect, useRef } from "react";

/**
 * Focus management for dialogs that do not use CommonModal.
 *
 * An audit of every `role="dialog"` in the app found focus restoration and a
 * Tab trap in exactly one place — CommonModal. The other twenty-eight dialogs
 * had neither: opening one left focus on the page behind it, Tab walked out of
 * the dialog into the background, and closing it dropped focus to <body> so
 * keyboard users restarted from the top of the document.
 *
 * Migrating all of them to CommonModal would mean rewriting their markup and
 * behaviour, so the behaviour is extracted here instead and applied in place.
 *
 *   const dialogRef = useDialogA11y(open, onClose)
 *   ...
 *   <div ref={dialogRef} role="dialog" aria-modal="true" aria-label="Payout">
 *
 * The hook deliberately does not render anything or alter layout: it only
 * moves focus, traps Tab, and closes on Escape.
 */
const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function focusableWithin(root) {
  if (!root) return [];
  return Array.from(root.querySelectorAll(FOCUSABLE)).filter((el) => {
    if (el.hasAttribute("disabled") || el.getAttribute("aria-hidden") === "true") return false;
    // Computed style only. Geometry is the wrong signal here: a fixed-position
    // dialog reports no offsetParent, and jsdom reports a zero-sized box for
    // everything, so a size check would exclude every control in both.
    const styled = typeof getComputedStyle === "function" ? getComputedStyle(el) : null;
    if (styled && (styled.visibility === "hidden" || styled.display === "none")) return false;
    return true;
  });
}

/**
 * @param {boolean} open      whether the dialog is currently rendered
 * @param {() => void} onClose called on Escape
 * @param {{ closeOnEscape?: boolean, autoFocus?: boolean }} [options]
 * @returns {import('react').RefObject<HTMLElement>} ref for the dialog element
 */
export function useDialogA11y(open, onClose, options = {}) {
  const { closeOnEscape = true, autoFocus = true } = options;
  const dialogRef = useRef(null);
  const previouslyFocused = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Remember the trigger before focus moves, and put it back on close.
  useEffect(() => {
    if (!open) return undefined;
    previouslyFocused.current =
      typeof document !== "undefined" ? document.activeElement : null;

    let raf = null;
    if (autoFocus) {
      // Wait a frame: the dialog's children may not be mounted on first paint.
      raf = setTimeout(() => {
        const node = dialogRef.current;
        if (!node) return;
        const [first] = focusableWithin(node);
        if (first) {
          first.focus();
        } else {
          if (!node.hasAttribute("tabindex")) node.setAttribute("tabindex", "-1");
          node.focus();
        }
      }, 0);
    }

    return () => {
      if (raf) clearTimeout(raf);
      const trigger = previouslyFocused.current;
      previouslyFocused.current = null;
      // Only restore if the trigger is still in the document; a dialog opened
      // from a row that has since been removed must not throw.
      if (trigger && typeof trigger.focus === "function" && trigger.isConnected !== false) {
        trigger.focus();
      }
    };
  }, [open, autoFocus]);

  const handleKeyDown = useCallback(
    (event) => {
      if (!open) return;
      if (closeOnEscape && event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current?.();
        return;
      }
      if (event.key !== "Tab") return;
      const node = dialogRef.current;
      if (!node) return;
      const items = focusableWithin(node);
      if (items.length === 0) {
        // Nothing to move to — keep focus on the dialog rather than the page.
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!node.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [open, closeOnEscape],
  );

  // Capture phase on the document so the trap works even when focus has
  // already escaped into the background.
  useEffect(() => {
    if (!open || typeof document === "undefined") return undefined;
    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, [open, handleKeyDown]);

  return dialogRef;
}

export default useDialogA11y;
