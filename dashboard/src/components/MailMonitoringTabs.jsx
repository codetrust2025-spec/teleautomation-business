import React from "react";

/**
 * Sub-navigation for the AI Mail Monitoring area.
 *
 * The audit report is also a top-level view, but that nav is a horizontal
 * scroll container with a hidden scrollbar: once it holds nine sections the
 * later ones clip off the edge with nothing on screen to say they exist. An
 * administrator standing on the Notifications page had no way to discover the
 * audit at all, so the two related pages link to each other directly.
 */
const TABS = [
  {
    view: "mail-notifications",
    label: "Notifications",
    title: "Live candidate job-status alerts",
  },
  {
    view: "outcome-audit",
    label: "Mail Audit",
    title: "Evidence-based outcome audit across every connected mailbox",
  },
];

export function MailMonitoringTabs({ active }) {
  const go = (view) => {
    if (view === active) return;
    window.dispatchEvent(
      new CustomEvent("teleautomation:navigate", { detail: { view } }),
    );
  };

  return (
    <nav className="mail-area-tabs" aria-label="AI mail monitoring sections">
      {TABS.map((tab) => (
        <button
          key={tab.view}
          type="button"
          className={`mail-area-tabs__btn${
            tab.view === active ? " mail-area-tabs__btn--active" : ""
          }`}
          aria-current={tab.view === active ? "page" : undefined}
          title={tab.title}
          onClick={() => go(tab.view)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}

export default MailMonitoringTabs;
