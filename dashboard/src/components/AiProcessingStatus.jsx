import React, { useEffect, useMemo, useRef, useState } from "react";

import "./AiProcessingStatus.css";

/**
 * Shared status surface for every Ollama-backed action.
 *
 * The project had three unrelated ways of showing AI work in progress — a bare
 * spinner, a text-only "AI reading completed" strip, and a generic overlay —
 * none of which distinguished queued from retrying, or offered a way out of a
 * failure. This component is the single answer for all of them.
 *
 * Two deliberate restraints:
 *
 * - No invented progress. A percentage is shown only when the caller passes a
 *   real one. AI work has no honest ETA, so a fake bar would be a lie the user
 *   makes decisions on.
 * - Motion is decorative only. Under `prefers-reduced-motion` every animation
 *   stops and the same information is conveyed by text and colour alone.
 */

export const AI_STAGE_MESSAGES = [
  "Connecting to AI…",
  "Understanding your data…",
  "Verifying details…",
  "Preparing the result…",
];

const MESSAGE_INTERVAL_MS = 2600;

const STATE_COPY = {
  queued: { label: "Queued", detail: "Waiting for an AI slot…" },
  processing: { label: "Processing", detail: null },
  retrying: { label: "Retrying", detail: "That attempt failed. Trying again…" },
  success: { label: "Done", detail: "AI finished successfully." },
  timeout: {
    label: "Took too long",
    detail: "The AI did not answer in time. Retry, or continue manually.",
  },
  error: { label: "Failed", detail: "The AI could not complete this step." },
};

const ACTIVE_STATES = new Set(["queued", "processing", "retrying"]);
const FAILED_STATES = new Set(["timeout", "error"]);

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = (event) => setReduced(event.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

/**
 * Cycle the human-friendly copy while work is genuinely in flight.
 * Holds on the first message when motion is reduced so the text does not
 * change under someone who asked for stillness.
 */
function useRotatingMessage(active, messages, reducedMotion) {
  const [index, setIndex] = useState(0);
  const timer = useRef(null);
  useEffect(() => {
    if (!active || reducedMotion || messages.length <= 1) {
      setIndex(0);
      return undefined;
    }
    timer.current = window.setInterval(() => {
      setIndex((current) => (current + 1) % messages.length);
    }, MESSAGE_INTERVAL_MS);
    return () => window.clearInterval(timer.current);
  }, [active, reducedMotion, messages]);
  return messages[Math.min(index, messages.length - 1)] || "";
}

/**
 * Time the work actually took, from entering an active state to leaving it.
 *
 * The rotating copy and the trail both say "something is happening" without
 * saying how long for, so a 4-second read and a 40-second one look identical
 * while you wait. The elapsed count is a real measurement rather than invented
 * progress, and it keeps its final value once the work stops so the number can
 * still be read afterwards — which is the point when comparing inference nodes.
 *
 * A retry keeps counting rather than restarting: the honest answer to "how long
 * has this taken" is the whole wait, not the latest attempt.
 */
function useElapsed(active, enabled, reducedMotion) {
  const [ms, setMs] = useState(null);
  const startedAt = useRef(null);

  useEffect(() => {
    if (!enabled) return undefined;
    if (!active) {
      startedAt.current = null;
      return undefined;
    }
    if (startedAt.current === null) {
      startedAt.current = Date.now();
      setMs(0);
    }
    // A number changing ten times a second is motion; someone who asked for
    // stillness gets whole seconds instead of tenths.
    const period = reducedMotion ? 1000 : 100;
    const timer = window.setInterval(() => {
      if (startedAt.current !== null) setMs(Date.now() - startedAt.current);
    }, period);
    return () => window.clearInterval(timer);
  }, [active, enabled, reducedMotion]);

  return ms;
}

export function formatElapsed(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return null;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

export const MODE_LABELS = { "ocr+ai": "OCR + AI", ai: "AI only" };

export default function AiProcessingStatus({
  state = "processing",
  variant = "card",
  /** "ocr+ai" | "ai" — which engine is reading the file. Omit to hide. */
  mode = null,
  title = "AI processing",
  message,
  messages = AI_STAGE_MESSAGES,
  /** Real 0–100 progress only. Omit entirely when none exists. */
  progress = null,
  /** Show how long the work has been running, and how long it took. */
  showElapsed = true,
  /**
   * Supply the measurement instead of timing internally.
   *
   * Callers that swap this component out between states — a processing card
   * replaced by a success strip, say — unmount it, and internal timing dies
   * with the instance. Those callers own the request, so they should own the
   * clock and pass the result here.
   */
  elapsedMs = null,
  onRetry,
  onCancel,
  retryLabel = "Retry",
  cancelLabel = "Cancel",
  className = "",
}) {
  const reducedMotion = usePrefersReducedMotion();
  const active = ACTIVE_STATES.has(state);
  const failed = FAILED_STATES.has(state);
  const rotating = useRotatingMessage(state === "processing", messages, reducedMotion);

  const copy = STATE_COPY[state] || STATE_COPY.processing;
  const detail = message || (state === "processing" ? rotating : copy.detail);

  const controlled = typeof elapsedMs === "number" && Number.isFinite(elapsedMs);
  const internalMs = useElapsed(active, showElapsed && !controlled, reducedMotion);
  const elapsedText = showElapsed
    ? formatElapsed(controlled ? elapsedMs : internalMs)
    : null;
  const elapsedTitle = active
    ? "Time so far"
    : state === "success"
      ? "Time taken"
      : "Time before it stopped";

  const hasRealProgress =
    typeof progress === "number" && Number.isFinite(progress) && progress >= 0;
  const clamped = hasRealProgress ? Math.min(100, Math.max(0, progress)) : null;

  const modeLabel = mode ? MODE_LABELS[mode] || null : null;

  // Announce politely: this is status, never an interruption.
  const ariaLabel = useMemo(
    () =>
      [title, modeLabel, copy.label, detail, elapsedText && `${elapsedTitle} ${elapsedText}`]
        .filter(Boolean)
        .join(" — "),
    [title, modeLabel, copy.label, detail, elapsedText, elapsedTitle],
  );

  const classes = [
    "aips",
    `aips--${variant}`,
    `aips--${state}`,
    reducedMotion ? "aips--still" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      role="status"
      aria-live="polite"
      aria-busy={active || undefined}
      aria-label={ariaLabel}
    >
      <span className="aips__orb" aria-hidden="true">
        {state === "success" ? (
          <svg className="aips__check" viewBox="0 0 24 24" focusable="false">
            <path d="M5 12.5l4.5 4.5L19 7.5" />
          </svg>
        ) : failed ? (
          <span className="aips__glyph">!</span>
        ) : (
          <>
            <span className="aips__core" />
            <span className="aips__ring" />
          </>
        )}
      </span>

      <span className="aips__body">
        {variant === "card" && (
          <span className="aips__title">
            {title}
            {modeLabel && <span className="aips__mode">{modeLabel}</span>}
            {/* Anchored to the title rather than the message, which rotates and
                would make a trailing counter jump about. */}
            {elapsedText && (
              <span className="aips__elapsed" title={elapsedTitle}>
                {elapsedText}
              </span>
            )}
          </span>
        )}
        {variant !== "card" && modeLabel && (
          <span className="aips__mode aips__mode--inline">{modeLabel}</span>
        )}
        <span className="aips__message">
          {detail || copy.label}
          {variant !== "card" && elapsedText && (
            <span className="aips__elapsed aips__elapsed--inline" title={elapsedTitle}>
              {elapsedText}
            </span>
          )}
        </span>
        {hasRealProgress && (
          <span
            className="aips__meter"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={clamped}
          >
            <span className="aips__meter-fill" style={{ width: `${clamped}%` }} />
          </span>
        )}
        {/* No meter without real progress — a moving trail conveys liveness
            without implying a measurable ETA. */}
        {!hasRealProgress && active && <span className="aips__trail" aria-hidden="true" />}
      </span>

      {failed && (onRetry || onCancel) && (
        <span className="aips__actions">
          {onRetry && (
            <button type="button" className="aips__btn aips__btn--retry" onClick={onRetry}>
              {retryLabel}
            </button>
          )}
          {onCancel && (
            <button type="button" className="aips__btn" onClick={onCancel}>
              {cancelLabel}
            </button>
          )}
        </span>
      )}
    </div>
  );
}
