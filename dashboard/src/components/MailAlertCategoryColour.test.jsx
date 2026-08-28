/**
 * Interview alerts must be visually distinguishable from selection/offer ones.
 *
 * Two cues carry different information: the category (what kind of alert) and
 * the outcome (whether it went wrong). Category sets the hue because that is
 * what an operator scans for; outcome overrides it for anything negative, so
 * "Interview Cancelled" cannot read as a routine interview merely because it
 * belongs to the interview group.
 *
 * The category is derived from CLASSIFICATION_GROUPS, not a second mapping, so
 * the colour on a row always agrees with the filter that would select it. That
 * is asserted directly: a private list would drift the first time a
 * classification moved between groups, and nothing else would fail.
 *
 * `.mail-status` had no rule at all before this — the detected-status column
 * rendered as bare text — so these also check the badge exists as a badge.
 */

import React from "react";
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  mailAlertCategory,
  mailStatusTone,
  CLASSIFICATION_GROUPS,
  TRACKED_CLASSIFICATIONS,
} from "./MailMonitoringNotifications.jsx";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = ["index.css", "recruitmentMail.css"]
  .map((f) => readFileSync(join(SRC, f), "utf8"))
  .join("\n");

describe("category derivation", () => {
  it("puts every tracked classification in exactly one colour group", () => {
    for (const classification of TRACKED_CLASSIFICATIONS) {
      const category = mailAlertCategory({ classification });
      expect(["interview", "selection"]).toContain(category);
    }
  });

  it("agrees with the filter that would select the row", () => {
    // The colour and the filter must not disagree about what an alert is.
    for (const group of CLASSIFICATION_GROUPS) {
      for (const classification of group.classifications) {
        expect(mailAlertCategory({ classification })).toBe(group.value);
      }
    }
  });

  it("colours interview alerts as interview", () => {
    expect(mailAlertCategory({ classification: "interview_confirmed" })).toBe("interview");
    expect(mailAlertCategory({ classification: "interview_cancelled" })).toBe("interview");
  });

  it("colours offer and joining alerts as selection", () => {
    expect(mailAlertCategory({ classification: "offer_received" })).toBe("selection");
    expect(mailAlertCategory({ classification: "joining_confirmed" })).toBe("selection");
    expect(mailAlertCategory({ classification: "job_selection_confirmed" })).toBe("selection");
  });

  it("returns no category rather than guessing for an unknown classification", () => {
    // needs_review is not in either group; a wrong colour is worse than none.
    expect(mailAlertCategory({ classification: "needs_review" })).toBe("");
    expect(mailAlertCategory({})).toBe("");
  });
});

describe("the two categories are actually different colours", () => {
  const block = (selector) => {
    const at = css.indexOf(selector + " {");
    expect(at, `${selector} has no rule`).toBeGreaterThan(-1);
    return css.slice(at, css.indexOf("}", at));
  };

  it("styles the status badge at all", () => {
    const base = block(".mail-status");
    expect(base).toContain("border-radius");
    expect(base).toContain("padding");
  });

  it("gives each category its own badge colour", () => {
    const interview = block(".mail-status--interview");
    const selection = block(".mail-status--selection");
    expect(interview).toContain("color:");
    expect(selection).toContain("color:");
    expect(interview).not.toBe(selection);
  });

  it("gives each category its own row accent", () => {
    const interview = block(".mail-notification-row--interview td:first-child");
    const selection = block(".mail-notification-row--selection td:first-child");
    expect(interview).toContain("inset 3px 0 0");
    expect(selection).toContain("inset 3px 0 0");
    expect(interview).not.toBe(selection);
  });

  it("keeps the row accent free of outcome colour", () => {
    // The accent is the scanning cue for category. Mixing outcome into it
    // would make two rows of the same kind look like different kinds.
    for (const tone of ["danger", "warning", "success", "review"]) {
      expect(css).not.toContain(`.mail-notification-row--${tone}`);
    }
  });
});

describe("a failed alert still reads as failed", () => {
  it("orders outcome overrides after the category rules", () => {
    // Same specificity, so source order decides. If the category rules came
    // last, a cancelled interview would look like a routine one.
    const category = css.indexOf(".mail-status--interview {");
    const danger = css.indexOf(".mail-status--danger {");
    const warning = css.indexOf(".mail-status--warning {");
    expect(category).toBeGreaterThan(-1);
    expect(danger).toBeGreaterThan(category);
    expect(warning).toBeGreaterThan(category);
  });

  it("still classifies cancellations and failures as negative outcomes", () => {
    // mailStatusTone is unchanged; the category is additive.
    expect(mailStatusTone({ candidate_status: "Interview Cancelled" })).toBe("danger");
    expect(mailStatusTone({ booking_status: "Processing Failed" })).toBe("danger");
    expect(mailStatusTone({ candidate_status: "Automatic Booking Blocked" })).toBe("warning");
  });

  it("a cancelled interview carries both classes so the override can apply", () => {
    const item = { classification: "interview_cancelled", candidate_status: "Interview Cancelled" };
    expect(mailAlertCategory(item)).toBe("interview");
    expect(mailStatusTone(item)).toBe("danger");
  });
});
