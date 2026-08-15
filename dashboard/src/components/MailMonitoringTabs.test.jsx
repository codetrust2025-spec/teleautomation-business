import React from "react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MailMonitoringTabs } from "./MailMonitoringTabs.jsx";

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => fs.readFileSync(path.join(SRC, rel), "utf8");

afterEach(cleanup);

describe("AI mail monitoring sub-navigation", () => {
  it("offers both sections", () => {
    render(<MailMonitoringTabs active="mail-notifications" />);
    expect(screen.getByText("Notifications")).toBeTruthy();
    expect(screen.getByText("Mail Audit")).toBeTruthy();
  });

  it("marks the current section for assistive technology", () => {
    render(<MailMonitoringTabs active="outcome-audit" />);
    expect(screen.getByText("Mail Audit").getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("Notifications").getAttribute("aria-current")).toBeNull();
  });

  it("navigates to the audit view on the app's own event bus", () => {
    const seen = [];
    const listener = (event) => seen.push(event.detail?.view);
    window.addEventListener("teleautomation:navigate", listener);
    render(<MailMonitoringTabs active="mail-notifications" />);
    fireEvent.click(screen.getByText("Mail Audit"));
    window.removeEventListener("teleautomation:navigate", listener);
    expect(seen).toEqual(["outcome-audit"]);
  });

  it("does not re-navigate to the section already open", () => {
    const seen = [];
    const listener = (event) => seen.push(event.detail?.view);
    window.addEventListener("teleautomation:navigate", listener);
    render(<MailMonitoringTabs active="outcome-audit" />);
    fireEvent.click(screen.getByText("Mail Audit"));
    window.removeEventListener("teleautomation:navigate", listener);
    expect(seen).toEqual([]);
  });
});

describe("Mail Audit is reachable from the independent Operations shell", () => {
  it("is listed directly below AI Mail Review and routed", () => {
    const app = read("App.jsx");
    expect(app).toContain("OutcomeAuditPanel");
    expect(app).toContain("view === 'outcome-audit'");
    expect(app.indexOf("id: 'ai-recruitment'")).toBeLessThan(
      app.indexOf("id: 'outcome-audit'"),
    );
  });

  it("is linked from the notifications page", () => {
    expect(read("components/MailMonitoringNotifications.jsx")).toContain(
      'MailMonitoringTabs active="mail-notifications"',
    );
  });

  it("does not repeat that link on the audit page itself", () => {
    // The audit page reached the point of carrying two navigation bars. The
    // sidebar lists both sections, and Notifications still links across, so
    // the duplicate inside the audit page is redundant chrome.
    expect(read("components/OutcomeAuditPanel.jsx")).not.toContain("MailMonitoringTabs");
  });

});
