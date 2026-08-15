import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API } from "../config.js";
import { useConfirm } from "../context/ConfirmContext.jsx";
import { useDialogA11y } from "../hooks/useDialogA11y.js";
import { ButtonContent, InlineLoader } from "../Loader.jsx";

const request = async (path, options = {}) => {
  const response = await fetch(`${API}${path}`, {
    credentials: "include",
    cache: !options.method || options.method === "GET" ? "no-store" : undefined,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(body.detail || body.message || "Request failed");
  return body;
};

export const SELECTION = "SELECTION";
export const INTERVIEW = "INTERVIEW";

/**
 * Selection and interview-slot results answer different questions and share no
 * category, which is what stops a mailbox of interview invitations reading as
 * hiring progress. The lists below are display labels only; the partition
 * itself lives in the audit engine.
 */
const SELECTION_OUTCOMES = [
  ["VERIFIED_OFFER_LETTER", "Verified offer letter"],
  ["FINAL_SELECTION", "Final selection"],
  ["OFFER_INDICATION", "Offer indication"],
  ["JOINING_CONFIRMED", "Joining confirmation"],
  ["BACKGROUND_VERIFICATION", "Background verification"],
  ["SHORTLISTED", "Shortlisted"],
  ["NEXT_ROUND", "Next round"],
  ["REJECTED", "Rejected"],
  ["MANUAL_REVIEW_REQUIRED", "Manual review required"],
  ["NOT_RELEVANT", "No selection evidence"],
];

const INTERVIEW_OUTCOMES = [
  ["INTERVIEW_INVITE", "Interview invitation"],
  ["INTERVIEW_AUTO_BOOKED", "Interview automatically booked"],
  ["INTERVIEW_RESCHEDULED", "Interview rescheduled"],
  ["INTERVIEW_CANCELLED", "Interview cancelled"],
  ["BOOKING_BLOCKED", "Booking blocked"],
  ["DUPLICATE_BOOKING_IGNORED", "Duplicate booking ignored"],
  ["SLOT_CONFLICT", "Slot conflict"],
  ["MISSING_DATE_OR_TIME", "Missing date or time"],
  ["MISSED_OR_UNPROCESSED_INVITE", "Missed or unprocessed invite"],
  ["HISTORICAL_NOT_BOOKED", "Historical, not booked"],
  ["NOT_RELEVANT", "No interview activity"],
];

// The decision first. Everything else is one click away, never removed.
const SELECTION_HEADLINE = [
  ["candidates_verified_offer_letters", "Verified offers"],
  ["candidates_offer_indication", "Offer indications"],
  ["candidates_rejected", "Rejected"],
  ["candidates_manual_review", "Needs review"],
  ["pipeline_gaps_total", "Pipeline issues"],
];
const SELECTION_MORE = [
  ["candidates_final_selection", "Final selections"],
  ["candidates_joining_confirmed", "Joining confirmed"],
  ["candidates_background_verification", "Background verification"],
  ["candidates_shortlisted", "Shortlisted"],
  ["candidates_next_round", "Next round"],
  ["candidates_no_outcome", "No selection evidence"],
  ["total_connected_mailboxes", "Connected mailboxes"],
  ["mailboxes_scanned", "Scanned"],
  ["mailboxes_failed", "Failed to scan"],
];

const INTERVIEW_HEADLINE = [
  ["candidates_with_interview_invites", "Invitations"],
  ["candidates_auto_booked", "Automatically booked"],
  ["candidates_booking_blocked", "Booking blocked"],
  ["candidates_slot_conflict", "Slot conflicts"],
  ["pipeline_gaps_total", "Pipeline issues"],
];
const INTERVIEW_MORE = [
  ["candidates_interview_rescheduled", "Rescheduled"],
  ["candidates_interview_cancelled", "Cancelled"],
  ["candidates_duplicate_booking_ignored", "Duplicate ignored"],
  ["candidates_missing_date_or_time", "Missing date or time"],
  ["candidates_missed_invites", "Missed or unprocessed"],
  ["candidates_historical_not_booked", "Historical, not booked"],
  ["total_connected_mailboxes", "Connected mailboxes"],
  ["mailboxes_scanned", "Scanned"],
  ["mailboxes_failed", "Failed to scan"],
];

const CLEANUP_REASONS = {
  IRRELEVANT: "Irrelevant",
  DUPLICATE: "Duplicate",
  SUPERSEDED: "Superseded",
  WRONG_AUDIT_MODE: "Moved to Interview Slots",
};

const AUTHENTICITY = ["PASS", "PARTIAL", "UNVERIFIED", "SUSPICIOUS"];
const LABELS = new Map([...SELECTION_OUTCOMES, ...INTERVIEW_OUTCOMES]);

const human = (value) =>
  LABELS.get(value) ||
  String(value || "")
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase());

const when = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
};

const day = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleDateString("en-IN", { dateStyle: "medium" });
};

// Only three states get colour. Everything else is neutral, so the eye lands
// on the outcome rather than on a wall of badges.
const TONE = {
  VERIFIED_OFFER_LETTER: "good",
  JOINING_CONFIRMED: "good",
  INTERVIEW_AUTO_BOOKED: "good",
  FINAL_SELECTION: "good",
  REJECTED: "bad",
  INTERVIEW_CANCELLED: "bad",
  SLOT_CONFLICT: "bad",
  BOOKING_BLOCKED: "warn",
  MANUAL_REVIEW_REQUIRED: "warn",
};

function Collapsible({ label, count, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="audit-collapse">
      <button
        type="button"
        className="audit-collapse__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden>{open ? "▾" : "▸"}</span> {label}
        {count != null && <span className="audit-collapse__count">{count}</span>}
      </button>
      {open && <div className="audit-collapse__body">{children}</div>}
    </div>
  );
}

export function OutcomeAuditPanel() {
  const { confirm } = useConfirm();
  const [mode, setMode] = useState(SELECTION);
  const [view, setView] = useState("report");
  const [summary, setSummary] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [gaps, setGaps] = useState([]);
  const [excluded, setExcluded] = useState([]);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [menuOpen, setMenuOpen] = useState(false);
  const [showMetrics, setShowMetrics] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [showTechnical, setShowTechnical] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);
  const menuRef = useRef(null);

  const [filters, setFilters] = useState({
    candidate: "",
    company: "",
    outcome: "ALL",
    authenticity: "ALL",
    sync_status: "ALL",
    min_confidence: "",
    manual_review: false,
    mismatch: false,
  });
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const isSelection = mode === SELECTION;
  const outcomeOptions = isSelection ? SELECTION_OUTCOMES : INTERVIEW_OUTCOMES;
  const headline = isSelection ? SELECTION_HEADLINE : INTERVIEW_HEADLINE;
  const moreMetrics = isSelection ? SELECTION_MORE : INTERVIEW_MORE;

  const activeFilters = useMemo(() => {
    const active = [];
    if (filters.candidate.trim()) active.push("search");
    if (filters.company.trim()) active.push("company");
    if (filters.outcome !== "ALL") active.push("outcome");
    if (filters.authenticity !== "ALL") active.push("authenticity");
    if (filters.sync_status !== "ALL") active.push("mailbox");
    if (filters.min_confidence) active.push("confidence");
    if (filters.manual_review) active.push("needs review");
    if (filters.mismatch) active.push("mismatch");
    if (dateFrom || dateTo) active.push("dates");
    return active;
  }, [filters, dateFrom, dateTo]);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    params.set("mode", mode);
    if (filters.candidate.trim()) params.set("candidate", filters.candidate.trim());
    if (filters.company.trim()) params.set("company", filters.company.trim());
    if (filters.outcome !== "ALL") params.set("outcome", filters.outcome);
    if (filters.authenticity !== "ALL") params.set("authenticity", filters.authenticity);
    if (filters.sync_status !== "ALL") params.set("sync_status", filters.sync_status);
    if (filters.min_confidence) params.set("min_confidence", filters.min_confidence);
    if (filters.manual_review) params.set("manual_review", "1");
    if (isSelection && filters.mismatch) params.set("mismatch", "1");
    return params.toString();
  }, [filters, mode, isSelection]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [summaryBody, candidateBody, gapBody, excludedBody] = await Promise.all([
        request(`/api/mail-outcome-audit/summary?${query}`),
        request(`/api/mail-outcome-audit/candidates?${query}`),
        request(`/api/mail-outcome-audit/gaps?mode=${mode}&limit=300`),
        request(`/api/mail-outcome-audit/excluded?limit=500`),
      ]);
      setSummary(summaryBody.summary || null);
      setCandidates(candidateBody.candidates || []);
      setGaps(gapBody.gaps || []);
      setExcluded(excludedBody.excluded || []);
    } catch (exc) {
      setError(exc.message || "Could not load the audit report");
    } finally {
      setLoading(false);
    }
  }, [query, mode]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDoc = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

  // An outcome filter from one audit means nothing in the other.
  const switchMode = useCallback((next) => {
    setMode(next);
    setView("report");
    setDetail(null);
    setExpandedRow(null);
    setFilters((prev) => ({ ...prev, outcome: "ALL", mismatch: false }));
  }, []);

  const clearFilters = useCallback(() => {
    setFilters({
      candidate: "", company: "", outcome: "ALL", authenticity: "ALL",
      sync_status: "ALL", min_confidence: "", manual_review: false, mismatch: false,
    });
    setDateFrom("");
    setDateTo("");
  }, []);

  const runAudit = useCallback(async () => {
    const ok = await confirm({
      title: "Run the mail audit",
      message:
        "This reads every authorized candidate mailbox and rebuilds the report. " +
        "It is report-only: no email is modified and no candidate status changes.",
      confirmLabel: "Run audit",
    });
    if (!ok) return;
    setRunning(true);
    setError("");
    setNotice("");
    try {
      const body = await request("/api/mail-outcome-audit/run", {
        method: "POST",
        body: JSON.stringify({ incremental: false }),
      });
      const run = body.run || {};
      setNotice(
        `Audit complete — ${run.mailboxes_scanned}/${run.mailboxes_total} mailboxes scanned, ` +
          `${run.messages_examined} messages examined.`,
      );
      await load();
    } catch (exc) {
      setError(exc.message || "The audit could not be started");
    } finally {
      setRunning(false);
    }
  }, [confirm, load]);

  const exportReport = useCallback(() => {
    window.open(`${API}/api/mail-outcome-audit/export?${query}`, "_blank", "noopener");
    setMenuOpen(false);
  }, [query]);

  const openCandidate = useCallback(
    async (row) => {
      setDetail({ loading: true, candidate: row });
      try {
        const params = new URLSearchParams();
        params.set("mode", mode);
        if (dateFrom) params.set("date_from", dateFrom);
        if (dateTo) params.set("date_to", dateTo);
        const body = await request(
          `/api/mail-outcome-audit/candidates/${encodeURIComponent(row.canonical_candidate_id)}?${params}`,
        );
        setDetail({ loading: false, ...body });
      } catch (exc) {
        setDetail({ loading: false, candidate: row, error: exc.message });
      }
    },
    [dateFrom, dateTo, mode],
  );

  const approve = useCallback(
    async (finding, decision) => {
      const ok = await confirm({
        title: decision === "APPROVED" ? "Apply this outcome" : "Mark as reviewed",
        message:
          decision === "APPROVED"
            ? `Set this candidate's status from the audited outcome "${human(finding.outcome)}"? ` +
              "This is the only action that changes a candidate record."
            : "Record that this audited outcome was reviewed and not applied?",
        confirmLabel: decision === "APPROVED" ? "Apply status" : "Mark reviewed",
      });
      if (!ok) return;
      try {
        const body = await request(
          `/api/mail-outcome-audit/findings/${encodeURIComponent(finding.id)}/approve`,
          { method: "POST", body: JSON.stringify({ decision }) },
        );
        const approval = body.approval || {};
        setNotice(
          decision === "APPROVED"
            ? `Status set to "${approval.status}" for candidate ${approval.candidate_id}.`
            : "Recorded as reviewed; nothing was changed.",
        );
        await load();
        if (detail?.candidate) await openCandidate(detail.candidate);
      } catch (exc) {
        setError(exc.message || "The approval could not be applied");
      }
    },
    [confirm, load, detail, openCandidate],
  );

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  const closeDetail = useCallback(() => setDetail(null), []);
  const dialogRef = useDialogA11y(Boolean(detail), closeDetail);

  // The one application an administrator could act on, if any. Eligibility is
  // decided by the server; this only picks which one to surface first.
  const primaryApplication = useMemo(
    () => (detail?.applications || []).find((app) => app.approval?.eligible) || null,
    [detail],
  );

  const modeLabel = isSelection ? "Selection" : "Interview slots";

  return (
    <div className="audit">
      <header className="audit__header">
        <div className="audit__title">
          <p className="audit__eyebrow">AI mail monitoring</p>
          <h1>Candidate Mail Audit</h1>
          <p className="audit__lastrun">
            {summary?.latest_run
              ? `Last audit ${when(summary.latest_run.started_at)} · report only`
              : "No audit has been run yet"}
          </p>
        </div>
        <div className="audit__actions" ref={menuRef}>
          <button
            type="button"
            className="audit-btn audit-btn--primary"
            onClick={runAudit}
            disabled={running}
          >
            <ButtonContent loading={running} loadingLabel="Auditing…">Run audit</ButtonContent>
          </button>
          <button
            type="button"
            className="audit-btn audit-btn--icon"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="More actions"
            onClick={() => setMenuOpen((value) => !value)}
          >
            ⋯
          </button>
          {menuOpen && (
            <div className="audit-menu" role="menu">
              <button type="button" role="menuitem" onClick={exportReport}>
                Export CSV
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  load();
                }}
              >
                Refresh
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setShowTechnical((value) => !value);
                  setMenuOpen(false);
                }}
              >
                Audit technical details
              </button>
            </div>
          )}
        </div>
      </header>

      {showTechnical && summary?.latest_run && (
        <section className="audit-technical" aria-label="Audit technical details">
          <dl>
            <div><dt>Run</dt><dd>{summary.latest_run.id || "—"}</dd></div>
            <div><dt>Status</dt><dd>{summary.latest_run.status}</dd></div>
            <div><dt>Mode</dt><dd>{summary.latest_run.mode}</dd></div>
            <div><dt>Messages examined</dt><dd>{summary.latest_run.messages_examined}</dd></div>
            <div><dt>Mailboxes</dt><dd>{summary.mailboxes_scanned}/{summary.total_connected_mailboxes}</dd></div>
            <div><dt>Excluded findings</dt><dd>{summary.excluded_findings ?? excluded.length}</dd></div>
          </dl>
        </section>
      )}

      <nav className="audit-nav" aria-label="Audit sections">
        <button
          type="button"
          className={mode === SELECTION && view === "report" ? "is-active" : ""}
          aria-current={mode === SELECTION && view === "report" ? "page" : undefined}
          onClick={() => switchMode(SELECTION)}
        >
          Selection
        </button>
        <button
          type="button"
          className={mode === INTERVIEW && view === "report" ? "is-active" : ""}
          aria-current={mode === INTERVIEW && view === "report" ? "page" : undefined}
          onClick={() => switchMode(INTERVIEW)}
        >
          Interviews
        </button>
        <button
          type="button"
          className={view === "gaps" ? "is-active" : ""}
          aria-current={view === "gaps" ? "page" : undefined}
          onClick={() => {
            setView("gaps");
            setDetail(null);
          }}
        >
          Pipeline
        </button>
      </nav>

      {error && <div className="audit-alert audit-alert--error">{error}</div>}
      {notice && <div className="audit-alert audit-alert--ok">{notice}</div>}

      {loading ? (
        <InlineLoader label="Loading audit…" />
      ) : view === "report" ? (
        <>
          <section className="audit-metrics" aria-label={`${modeLabel} summary`}>
            <div className="audit-metrics__row">
              {headline.map(([key, label]) => (
                <div className="audit-metric" key={key}>
                  <span className="audit-metric__value">{summary?.[key] ?? 0}</span>
                  <span className="audit-metric__label">{label}</span>
                </div>
              ))}
            </div>
            <Collapsible label="View all metrics">
              <div className="audit-metrics__row audit-metrics__row--secondary">
                {moreMetrics.map(([key, label]) => (
                  <div className="audit-metric audit-metric--small" key={key}>
                    <span className="audit-metric__value">{summary?.[key] ?? 0}</span>
                    <span className="audit-metric__label">{label}</span>
                  </div>
                ))}
              </div>
            </Collapsible>
          </section>

          <section className="audit-filters" aria-label="Filters">
            <div className="audit-filters__row">
              <input
                className="audit-input audit-input--search"
                placeholder="Search candidate"
                value={filters.candidate}
                onChange={(e) => setFilter("candidate", e.target.value)}
                aria-label="Search candidate"
              />
              <select
                className="audit-input"
                value={filters.outcome}
                onChange={(e) => setFilter("outcome", e.target.value)}
                aria-label="Filter by outcome"
              >
                <option value="ALL">All outcomes</option>
                {outcomeOptions.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
              <label className="audit-toggle">
                <input
                  type="checkbox"
                  checked={filters.manual_review}
                  onChange={(e) => setFilter("manual_review", e.target.checked)}
                />
                <span>Needs review</span>
              </label>
              <button
                type="button"
                className="audit-btn audit-btn--ghost"
                aria-expanded={showFilters}
                onClick={() => setShowFilters((value) => !value)}
              >
                More filters
              </button>
              {activeFilters.length > 0 && (
                <button type="button" className="audit-link" onClick={clearFilters}>
                  Clear filters ({activeFilters.length})
                </button>
              )}
            </div>
            {showFilters && (
              <div className="audit-filters__panel">
                <input
                  className="audit-input"
                  placeholder="Company"
                  value={filters.company}
                  onChange={(e) => setFilter("company", e.target.value)}
                  aria-label="Filter by company"
                />
                <select
                  className="audit-input"
                  value={filters.authenticity}
                  onChange={(e) => setFilter("authenticity", e.target.value)}
                  aria-label="Filter by authenticity"
                >
                  <option value="ALL">All authenticity</option>
                  {AUTHENTICITY.map((value) => (
                    <option key={value} value={value}>{human(value)}</option>
                  ))}
                </select>
                <select
                  className="audit-input"
                  value={filters.sync_status}
                  onChange={(e) => setFilter("sync_status", e.target.value)}
                  aria-label="Filter by mailbox sync status"
                >
                  <option value="ALL">All mailboxes</option>
                  <option value="MONITORING_ACTIVE">Monitoring active</option>
                  <option value="CONNECTED">Connected</option>
                  <option value="FAILED">Sync failed</option>
                </select>
                <select
                  className="audit-input"
                  value={filters.min_confidence}
                  onChange={(e) => setFilter("min_confidence", e.target.value)}
                  aria-label="Filter by minimum confidence"
                >
                  <option value="">Any confidence</option>
                  <option value="60">60% and above</option>
                  <option value="75">75% and above</option>
                  <option value="85">85% and above</option>
                </select>
                <input
                  type="date"
                  className="audit-input"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  aria-label="Evidence from date"
                />
                <input
                  type="date"
                  className="audit-input"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  aria-label="Evidence to date"
                />
                {isSelection && (
                  <label className="audit-toggle">
                    <input
                      type="checkbox"
                      checked={filters.mismatch}
                      onChange={(e) => setFilter("mismatch", e.target.checked)}
                    />
                    <span>Status mismatch</span>
                  </label>
                )}
              </div>
            )}
          </section>

          <section className="audit-table-wrap" aria-label={`${modeLabel} results`}>
            <table className="audit-table">
              <thead>
                <tr>
                  <th scope="col">Candidate</th>
                  <th scope="col">Strongest outcome</th>
                  <th scope="col">Company</th>
                  <th scope="col">System status</th>
                  <th scope="col">Last updated</th>
                  <th scope="col"><span className="audit-sr">Evidence</span></th>
                </tr>
              </thead>
              <tbody>
                {candidates.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="audit-empty">
                      No audited mailboxes match these filters.
                    </td>
                  </tr>
                ) : (
                  candidates.map((row) => {
                    const id = row.canonical_candidate_id;
                    const open = expandedRow === id;
                    const needsAttention =
                      row.manual_review_required ||
                      row.conflicting_evidence ||
                      row.suspicious_evidence;
                    const companies = row.companies || [];
                    return (
                      <React.Fragment key={id}>
                        <tr className={open ? "is-open" : ""}>
                          <td>
                            <button
                              type="button"
                              className="audit-rowtoggle"
                              aria-expanded={open}
                              onClick={() => setExpandedRow(open ? null : id)}
                            >
                              <span aria-hidden>{open ? "▾" : "▸"}</span>
                              <span className="audit-rowtoggle__name">
                                {row.candidate_name || "Unnamed"}
                              </span>
                            </button>
                            <span className="audit-sub">{row.email_address}</span>
                          </td>
                          <td>
                            <span
                              className={`audit-badge audit-badge--${TONE[row.strongest_outcome] || "muted"}`}
                            >
                              {human(row.strongest_outcome)}
                            </span>
                            {needsAttention && (
                              <span
                                className="audit-warn"
                                title="Needs review — open Evidence for detail"
                                aria-label="Needs review"
                              >
                                !
                              </span>
                            )}
                            <span className="audit-sub">
                              {row.strongest_confidence
                                ? `${Math.round(row.strongest_confidence)}% confidence`
                                : "confidence not set"}
                              {row.strongest_authenticity
                                ? ` · ${human(row.strongest_authenticity)}`
                                : ""}
                            </span>
                          </td>
                          <td>
                            {companies.length === 0 ? "—" : companies[0]}
                            {companies.length > 1 && (
                              <span className="audit-sub">+{companies.length - 1} more</span>
                            )}
                          </td>
                          <td>
                            {row.system_status || "—"}
                            {row.status_mismatch && (
                              <span className="audit-mismatch" title={row.mismatch_detail}>
                                Mismatch
                              </span>
                            )}
                          </td>
                          <td>{when(row.last_successful_sync_at)}</td>
                          <td className="audit-table__action">
                            <button
                              type="button"
                              className="audit-btn audit-btn--ghost audit-btn--sm"
                              onClick={() => openCandidate(row)}
                            >
                              Evidence
                            </button>
                          </td>
                        </tr>
                        {open && (
                          <tr className="audit-rowdetail">
                            <td colSpan={6}>
                              <dl>
                                <div><dt>Gmail</dt><dd>{row.email_address}</dd></div>
                                <div><dt>Candidate ID</dt><dd>{id}</dd></div>
                                <div><dt>Mailbox</dt><dd>{human(row.monitoring_status)}</dd></div>
                                <div><dt>Authenticity</dt><dd>{human(row.strongest_authenticity) || "—"}</dd></div>
                                <div><dt>Messages examined</dt><dd>{row.messages_examined ?? "—"}</dd></div>
                                <div><dt>Relevant</dt><dd>{row.relevant_messages ?? "—"}</dd></div>
                                <div className="audit-rowdetail__wide">
                                  <dt>Companies</dt>
                                  <dd>{companies.join(", ") || "—"}</dd>
                                </div>
                                <div className="audit-rowdetail__wide">
                                  <dt>Recommended</dt>
                                  <dd>{row.recommended_action}</dd>
                                </div>
                              </dl>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </section>

          {isSelection && excluded.length > 0 && (
            <p className="audit-footnote">
              <button
                type="button"
                className="audit-link"
                onClick={() => setView("excluded")}
              >
                {excluded.length} findings excluded from this audit
              </button>{" "}
              — irrelevant, duplicate or superseded mail. Nothing was deleted.
            </p>
          )}
        </>
      ) : view === "excluded" ? (
        <>
          <p className="audit-footnote">
            <button type="button" className="audit-link" onClick={() => setView("report")}>
              ← Back to {modeLabel}
            </button>
          </p>
          <section className="audit-table-wrap" aria-label="Excluded findings">
            <table className="audit-table">
              <thead>
                <tr>
                  <th scope="col">Reason</th>
                  <th scope="col">Candidate</th>
                  <th scope="col">Subject</th>
                  <th scope="col">Why</th>
                  <th scope="col">Excluded</th>
                </tr>
              </thead>
              <tbody>
                {excluded.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <span className="audit-badge audit-badge--muted">
                        {CLEANUP_REASONS[row.suppression_reason] || row.suppression_reason}
                      </span>
                    </td>
                    <td>
                      {row.candidate_name || row.canonical_candidate_id}
                      <span className="audit-sub">{row.email_address}</span>
                    </td>
                    <td className="audit-cell--wide">{row.subject || "(no subject)"}</td>
                    <td className="audit-cell--wide">{row.suppression_detail}</td>
                    <td>{day(row.suppressed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : (
        <section className="audit-table-wrap" aria-label="Pipeline issues">
          <table className="audit-table">
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Issue</th>
                <th scope="col">Candidate</th>
                <th scope="col">Detail</th>
              </tr>
            </thead>
            <tbody>
              {gaps.length === 0 ? (
                <tr>
                  <td colSpan={4} className="audit-empty">No pipeline issues recorded.</td>
                </tr>
              ) : (
                gaps.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <span
                        className={`audit-badge audit-badge--${
                          row.severity === "HIGH" ? "bad" : row.severity === "MEDIUM" ? "warn" : "muted"
                        }`}
                      >
                        {row.severity}
                      </span>
                    </td>
                    <td>{human(row.gap_type)}</td>
                    <td>
                      {row.candidate_name || row.canonical_candidate_id}
                      <span className="audit-sub">{row.email_address}</span>
                    </td>
                    <td className="audit-cell--wide">{row.detail}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>
      )}

      {detail && (
        <div className="audit-drawer-backdrop" role="presentation" onClick={closeDetail}>
          <aside
            ref={dialogRef}
            className="audit-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Candidate mail evidence"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="audit-drawer__head">
              <div>
                <h2>{detail.candidate?.candidate_name || "Candidate"}</h2>
                <p className="audit-sub">
                  {detail.candidate?.email_address} · {modeLabel} audit
                </p>
              </div>
              <button
                type="button"
                className="audit-btn audit-btn--ghost audit-btn--sm"
                onClick={closeDetail}
              >
                Close
              </button>
            </header>

            {detail.loading ? (
              <InlineLoader label="Loading evidence…" />
            ) : detail.error ? (
              <div className="audit-alert audit-alert--error">{detail.error}</div>
            ) : (
              <>
                <section className="audit-drawer__action">
                  <h3>Recommended action</h3>
                  <p>{detail.candidate?.recommended_action}</p>
                  {detail.candidate?.mismatch_detail && (
                    <p className="audit-alert audit-alert--warn">
                      {detail.candidate.mismatch_detail}
                    </p>
                  )}
                  <div className="audit-drawer__buttons">
                    {primaryApplication ? (
                      <button
                        type="button"
                        className="audit-btn audit-btn--primary"
                        onClick={() =>
                          approve(
                            {
                              id: primaryApplication.strongest_finding_id,
                              outcome: primaryApplication.latest_verified_state,
                            },
                            "APPROVED",
                          )
                        }
                      >
                        Review status update
                      </button>
                    ) : (
                      <p className="audit-blocked">
                        No application meets the bar for a status change.
                      </p>
                    )}
                    {(detail.applications || []).length > 0 && (
                      <button
                        type="button"
                        className="audit-btn audit-btn--ghost"
                        onClick={() =>
                          approve(
                            {
                              id:
                                (primaryApplication || detail.applications[0])
                                  .strongest_finding_id,
                              outcome:
                                (primaryApplication || detail.applications[0])
                                  .latest_verified_state,
                            },
                            "REJECTED",
                          )
                        }
                      >
                        Mark reviewed
                      </button>
                    )}
                  </div>
                </section>

                {isSelection && (detail.applications || []).length > 0 && (
                  <section className="audit-drawer__section">
                    <h3>Companies and applications</h3>
                    <p className="audit-sub">
                      Each application is its own lifecycle. A result from one company never
                      affects another.
                    </p>
                    <ul className="audit-apps">
                      {detail.applications.map((app) => (
                        <li key={app.application_key}>
                          <div className="audit-apps__head">
                            <strong>{app.company}</strong>
                            <span
                              className={`audit-badge audit-badge--${TONE[app.latest_verified_state] || "muted"}`}
                            >
                              {human(app.latest_verified_state)}
                            </span>
                          </div>
                          <p className="audit-sub">
                            {app.role} · {day(app.latest_message_at)} ·{" "}
                            {human(app.evidence_strength)} evidence
                          </p>
                          {!app.approval?.eligible && (
                            <p className="audit-blocked">{app.approval?.message}</p>
                          )}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {!isSelection && (detail.bookings || []).length > 0 && (
                  <section className="audit-drawer__section">
                    <h3>Booking outcomes</h3>
                    <ul className="audit-list">
                      {detail.bookings.map((booking) => (
                        <li key={booking.id}>
                          <strong>{human(booking.booking_outcome)}</strong> —{" "}
                          {booking.booking_status}
                          {booking.failure_message ? ` · ${booking.failure_message}` : ""}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                <section className="audit-drawer__section">
                  <h3>Strongest evidence</h3>
                  {(detail.findings || []).filter((f) => f.outcome !== "NOT_RELEVANT").length === 0 ? (
                    <p className="audit-empty">No mail here carries an outcome.</p>
                  ) : (
                    <ol className="audit-evidence">
                      {(detail.findings || [])
                        .filter((f) => f.outcome !== "NOT_RELEVANT")
                        .map((finding) => {
                          const review = (detail.ollama_reviews || {})[finding.id];
                          return (
                            <li key={finding.id}>
                              <div className="audit-evidence__head">
                                <span
                                  className={`audit-badge audit-badge--${TONE[finding.outcome] || "muted"}`}
                                >
                                  {human(finding.outcome)}
                                </span>
                                <span className="audit-sub">{day(finding.received_at)}</span>
                              </div>
                              <p className="audit-evidence__subject">
                                {finding.subject || "(no subject)"}
                              </p>
                              <p className="audit-sub">{finding.sender_email}</p>
                              <p className="audit-evidence__why">{finding.rationale}</p>
                              {(finding.evidence || []).slice(0, 1).map((item, index) => (
                                <blockquote key={index}>“{item.text}”</blockquote>
                              ))}

                              {review && (
                                <Collapsible label="AI audit comparison">
                                  <p className="audit-sub">
                                    Deterministic <strong>{human(finding.outcome)}</strong> ·
                                    Pipeline{" "}
                                    <strong>
                                      {finding.pipeline_outcome
                                        ? human(finding.pipeline_outcome)
                                        : "no event"}
                                    </strong>{" "}
                                    · Ollama{" "}
                                    <strong>
                                      {human(review.restricted_outcome || review.suggested_outcome)}
                                    </strong>
                                  </p>
                                  <p className="audit-sub">
                                    {human(review.derived_agreement)} ·{" "}
                                    {review.normalized_confidence == null
                                      ? "confidence withheld"
                                      : `${Math.round(review.normalized_confidence)}%`}
                                  </p>
                                  {review.quoted_evidence && (
                                    <blockquote>“{review.quoted_evidence}”</blockquote>
                                  )}
                                  <p className="audit-evidence__why">{review.reasoning}</p>
                                  <p className="audit-blocked">{review.approval_state}</p>
                                </Collapsible>
                              )}

                              <Collapsible label="Technical details">
                                <dl className="audit-tech">
                                  <div><dt>Source</dt><dd>{human(finding.source_type)}</dd></div>
                                  <div><dt>Evidence strength</dt><dd>{human(finding.evidence_strength)}</dd></div>
                                  <div><dt>Authenticity</dt><dd>{human(finding.authenticity)}</dd></div>
                                  <div><dt>Confidence</dt><dd>{Math.round(finding.confidence || 0)}%</dd></div>
                                  <div><dt>Pipeline</dt><dd>{human(finding.pipeline_agreement)}</dd></div>
                                  <div><dt>Message</dt><dd>{finding.provider_message_id || "—"}</dd></div>
                                  {review && (
                                    <>
                                      <div><dt>Model</dt><dd>{review.model}</dd></div>
                                      <div><dt>Cited message</dt><dd>{review.cited_message_id || "—"}</dd></div>
                                      <div><dt>Citations</dt><dd>{review.verified ? "verified" : "unverified"}</dd></div>
                                    </>
                                  )}
                                </dl>
                                {(finding.attachment_evidence || []).length > 0 && (
                                  <p className="audit-sub">
                                    Attachments:{" "}
                                    {(finding.attachment_evidence || [])
                                      .map((a) => `${a.filename} (${a.extraction_status})`)
                                      .join("; ")}
                                  </p>
                                )}
                              </Collapsible>
                            </li>
                          );
                        })}
                    </ol>
                  )}
                </section>

                {(detail.gaps || []).length > 0 && (
                  <section className="audit-drawer__section">
                    <Collapsible label="Conflicting evidence and pipeline issues" count={detail.gaps.length}>
                      <ul className="audit-list">
                        {detail.gaps.map((gap) => (
                          <li key={gap.id}>
                            <strong>{human(gap.gap_type)}</strong> — {gap.detail}
                          </li>
                        ))}
                      </ul>
                    </Collapsible>
                  </section>
                )}

                {(detail.approvals || []).length > 0 && (
                  <section className="audit-drawer__section">
                    <Collapsible label="Approval history" count={detail.approvals.length}>
                      <ul className="audit-list">
                        {detail.approvals.map((item) => (
                          <li key={item.id}>
                            {when(item.created_at)} — {item.decision}{" "}
                            {human(item.requested_outcome)} by {item.approved_by}
                            {item.applied ? ` → "${item.applied_system_status}"` : " (not applied)"}
                          </li>
                        ))}
                      </ul>
                    </Collapsible>
                  </section>
                )}
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

export default OutcomeAuditPanel;
