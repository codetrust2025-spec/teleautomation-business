import React, { useCallback, useEffect, useMemo, useState } from "react";
import { API } from "../config.js";
import { useConfirm } from "../context/ConfirmContext.jsx";
export { default as RecruitmentMailPanel } from "./RecruitmentMailPanelRedesign.jsx";

const api = async (path, options = {}) => {
  const isGet = !options.method || options.method === "GET";
  const separator = path.includes("?") ? "&" : "?";
  const requestPath = isGet
    ? `${path}${separator}_offerReview=offer_review_cleanup_v1`
    : path;
  const response = await fetch(`${API}${requestPath}`, {
    credentials: "include",
    cache: isGet ? "no-store" : undefined,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(body.detail || body.message || "Request failed");
  return body;
};
const human = (value) => String(value || "").replaceAll("_", " ");
const when = (value) => (value ? new Date(value).toLocaleString() : "Never");
const emailDate = (value) => {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Never";
  return `${String(date.getDate()).padStart(2, "0")}-${String(date.getMonth() + 1).padStart(2, "0")}-${date.getFullYear()}`;
};
const IMPORTANT_STATUSES = [
  "SELECTED",
  "FINAL_SELECTION_CONFIRMED",
  "OFFER_INDICATION",
  "OFFER_IN_PROGRESS",
  "OFFER_APPROVED",
  "OFFER_LETTER_RECEIVED",
  "APPOINTMENT_LETTER_RECEIVED",
  "OFFER_ACCEPTED",
  "JOINING_CONFIRMED",
  "JOINED",
  "POST_SELECTION_ONBOARDING",
  "MANUAL_REVIEW_REQUIRED",
];
const HIDDEN_STATUSES = new Set([
  "IGNORED_NOT_OFFER_RELATED",
  "IGNORED_LOW_CONFIDENCE",
  "NO_RELEVANT_STATUS",
]);
const HIDDEN_REVIEWS = new Set(["IGNORED", "FALSE_POSITIVE", "DUPLICATE"]);
const IMPORTANT_EVIDENCE_MEANINGS = new Set(
  IMPORTANT_STATUSES.filter((status) => status !== "MANUAL_REVIEW_REQUIRED"),
);
export function shouldShowInSelectionOfferReview(event) {
  const status = String(event?.primary_status || "").toUpperCase();
  const review = String(event?.review_status || "").toUpperCase();
  const evidence = event?.structured_result?.evidence || [];
  if (!IMPORTANT_STATUSES.includes(status) || HIDDEN_STATUSES.has(status))
    return false;
  if (HIDDEN_REVIEWS.has(review) || event?.visible_in_offer_review === false)
    return false;
  if (Number(event?.confidence || 0) < 0.8 || evidence.length === 0)
    return false;
  if (status === "MANUAL_REVIEW_REQUIRED") {
    if (event?.structured_result?.is_selection_or_offer_related !== true)
      return false;
    if (
      !evidence.some((item) =>
        IMPORTANT_EVIDENCE_MEANINGS.has(
          String(item?.meaning || "").toUpperCase(),
        ),
      )
    )
      return false;
  }
  return true;
}
const STATUS_FILTERS = {
  selected: ["SELECTED", "FINAL_SELECTION_CONFIRMED"],
  offer_indication: ["OFFER_INDICATION"],
  offer_in_progress: ["OFFER_IN_PROGRESS", "OFFER_APPROVED"],
  offer_letter: ["OFFER_LETTER_RECEIVED"],
  appointment_letter: ["APPOINTMENT_LETTER_RECEIVED"],
  offer_accepted: ["OFFER_ACCEPTED"],
  joining_confirmed: ["JOINING_CONFIRMED"],
  joined: ["JOINED"],
};

function EvidencePanel({ eventId, onClose, onChanged }) {
  const [event, setEvent] = useState(null),
    [notes, setNotes] = useState(""),
    [status, setStatus] = useState(""),
    [company, setCompany] = useState(""),
    [role, setRole] = useState("");
  useEffect(() => {
    api(`/api/ai-recruitment/events/${eventId}`).then((v) => {
      setEvent(v.event);
      setNotes(v.event.review_notes || "");
      setStatus(v.event.primary_status);
      setCompany(v.event.company_name || "");
      setRole(v.event.job_title || "");
    });
  }, [eventId]);
  const save = async () => {
    const result = await api(`/api/ai-recruitment/events/${eventId}`, {
      method: "PATCH",
      body: JSON.stringify({
        notes,
        changes: {
          primary_status: status,
          company_name: company || null,
          job_title: role || null,
        },
      }),
    });
    setEvent({ ...event, ...result.event });
    onChanged?.();
  };
  return (
    <aside className="recruitment-mail-evidence">
      <header>
        <h3>Detection evidence</h3>
        <button onClick={onClose}>Close</button>
      </header>
      {!event ? (
        <p>Loading…</p>
      ) : (
        <>
          <dl>
            <div>
              <dt>Source email</dt>
              <dd>{event.subject || "—"}</dd>
            </div>
            <div>
              <dt>Sender</dt>
              <dd>{event.sender_name || event.sender_email || "—"}</dd>
            </div>
            <div>
              <dt>AI model</dt>
              <dd>{event.ai_model}</dd>
            </div>
            <div>
              <dt>Prompt</dt>
              <dd>
                {event.prompt_name} {event.prompt_version}
              </dd>
            </div>
          </dl>
          <h4>Edit extracted data</h4>
          <label>
            Status
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {IMPORTANT_STATUSES.map((value) => (
                <option value={value} key={value}>
                  {human(value)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Company
            <input
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            />
          </label>
          <label>
            Job role
            <input value={role} onChange={(e) => setRole(e.target.value)} />
          </label>
          <label>
            Review notes
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
            />
          </label>
          <button onClick={save}>Save extracted data</button>
          <h4>Evidence</h4>
          <ul>
            {(event.structured_result?.evidence || []).map((item, i) => (
              <li key={i}>
                <strong>{human(item.source)}</strong> {item.text}
                {item.meaning && <small>{human(item.meaning)}</small>}
              </li>
            ))}
          </ul>
          <h4>Attachments</h4>
          {event.attachments?.length ? (
            <ul>
              {event.attachments.map((a) => (
                <li key={a.id}>
                  <strong>{a.filename}</strong> · {human(a.attachment_type)} ·{" "}
                  {human(a.extraction_status)}
                </li>
              ))}
            </ul>
          ) : (
            <p>No supported attachments.</p>
          )}
        </>
      )}
    </aside>
  );
}

function ReviewTable({ rows, names, onReview, onEvidence }) {
  return (
    <div className="recruitment-mail-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Detection Date</th>
            <th>Candidate</th>
            <th>Important Status</th>
            <th>Company</th>
            <th>Job Role</th>
            <th>Confidence</th>
            <th>Evidence</th>
            <th>Offer Details</th>
            <th>Review</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={9}>No important detections match the filters.</td>
            </tr>
          ) : (
            rows.map((event) => (
              <tr key={event.id}>
                <td title="Email received time">
                  {emailDate(event.email_sent_at || event.created_at)}
                </td>
                <td>{names[event.candidate_id] || event.candidate_id}</td>
                <td>
                  <span className="recruitment-mail-badge">
                    {human(event.primary_status)}
                  </span>
                </td>
                <td>{event.company_name || "—"}</td>
                <td>{event.job_title || "—"}</td>
                <td>{Math.round(Number(event.confidence) * 100)}%</td>
                <td>
                  <span>
                    {event.structured_result?.evidence?.length || 0} source(s)
                  </span>
                  <button
                    className="recruitment-mail-link"
                    onClick={() => onEvidence(event.id)}
                  >
                    View evidence
                  </button>
                </td>
                <td>
                  {event.offered_ctc
                    ? `${event.currency || ""} ${Number(event.offered_ctc).toLocaleString()}`
                    : event.joining_date
                      ? `Joining ${event.joining_date}`
                      : event.summary || "—"}
                </td>
                <td>
                  {event.review_status === "PENDING" ? (
                    <div className="recruitment-mail-actions">
                      <button onClick={() => onReview(event.id, "approve")}>
                        Approve
                      </button>
                      <button onClick={() => onReview(event.id, "reject")}>
                        Reject
                      </button>
                      <button
                        onClick={() => onReview(event.id, "false-positive")}
                      >
                        False positive
                      </button>
                      <button onClick={() => onReview(event.id, "duplicate")}>
                        Duplicate
                      </button>
                    </div>
                  ) : (
                    human(event.review_status)
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export function RecruitmentMailPanelLegacy() {
  const today = new Date().toISOString().slice(0, 10);
  const thirtyDaysAgo = new Date(Date.now() - 29 * 86400000)
    .toISOString()
    .slice(0, 10);
  const { confirm } = useConfirm();
  const [metrics, setMetrics] = useState(
      /** @type {Record<string, any>} */ ({}),
    ),
    [charts, setCharts] = useState(/** @type {Record<string, any>} */ ({})),
    [flags, setFlags] = useState([]);
  const [events, setEvents] = useState([]),
    [offers, setOffers] = useState([]),
    [candidates, setCandidates] = useState([]);
  const [candidateId, setCandidateId] = useState(""),
    [mailbox, setMailbox] = useState(null),
    [mailboxStats, setMailboxStats] = useState({}),
    [timeline, setTimeline] = useState([]);
  const [email, setEmail] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [tab, setTab] = useState("reviews"),
    [evidenceId, setEvidenceId] = useState(null);
  const [filters, setFilters] = useState({
      candidate: "",
      company: "",
      status: "",
      review: "PENDING",
      confidence: "",
      from: "",
      to: "",
      sort: "newest",
    }),
    [page, setPage] = useState(0),
    [bulkText, setBulkText] = useState("");
  const [rescanRange, setRescanRange] = useState({
    range_start: thirtyDaysAgo,
    range_end: today,
  });
  useEffect(() => {
    const selected = sessionStorage.getItem("ai-mail-candidate-id");
    if (selected) {
      setCandidateId(selected);
      setTab("mailboxes");
      sessionStorage.removeItem("ai-mail-candidate-id");
    }
  }, []);
  const load = useCallback(async () => {
    try {
      const [dash, review, cases, people] = await Promise.all([
        api("/api/ai-recruitment/dashboard"),
        api("/api/ai-recruitment/review?limit=100"),
        api("/api/offer-verification?limit=100"),
        api("/candidates?limit=500"),
      ]);
      setMetrics(dash.metrics || {});
      setCharts(dash.charts || {});
      setFlags(dash.flags || []);
      setEvents((review.events || []).filter(shouldShowInSelectionOfferReview));
      setOffers(cases.cases || []);
      setCandidates(people.candidates || []);
    } catch (e) {
      setMessage(e.message);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!candidateId) {
      setMailbox(null);
      setTimeline([]);
      return;
    }
    Promise.all([
      api(`/api/candidates/${candidateId}/mailbox`),
      api(`/api/candidates/${candidateId}/recruitment-timeline`),
    ])
      .then(([m, t]) => {
        setMailbox(m.mailbox);
        setMailboxStats(m.stats || {});
        setEmail(m.mailbox?.email_address || "");
        setTimeline(t.events || []);
      })
      .catch((e) => setMessage(e.message));
  }, [candidateId]);
  const run = async (action) => {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await load();
      if (candidateId) {
        const m = await api(`/api/candidates/${candidateId}/mailbox`);
        setMailbox(m.mailbox);
        setMailboxStats(m.stats || {});
      }
    } catch (e) {
      setMessage(e.message);
    } finally {
      setBusy(false);
    }
  };
  const connect = () =>
    run(async () => {
      const redirect_uri = `${window.location.origin}/api/candidate-mailboxes/oauth/google/callback`;
      const result = await api(
        `/api/candidates/${candidateId}/mailbox/connect`,
        {
          method: "POST",
          body: JSON.stringify({ email_address: email, redirect_uri }),
        },
      );
      window.location.assign(result.authorization_url);
    });
  const review = async (id, action) => {
    const ok = await confirm({
      title: `${human(action)} detection?`,
      message: "This decision is recorded in the audit log.",
      confirmLabel: human(action),
      variant: action === "approve" ? "success" : "danger",
    });
    if (ok)
      run(() =>
        api(`/api/ai-recruitment/events/${id}/${action}`, {
          method: "POST",
          body: "{}",
        }),
      );
  };
  const offerReview = async (id, action) => {
    const ok = await confirm({
      title: `${human(action)} offer case?`,
      message: "This does not create a payment obligation.",
      confirmLabel: human(action),
      variant: action === "verify" ? "success" : "warn",
    });
    if (ok)
      run(() =>
        api(`/api/offer-verification/${id}/${action}`, {
          method: "POST",
          body: "{}",
        }),
      );
  };
  const disconnect = async () => {
    const ok = await confirm({
      title: "Disconnect Gmail?",
      message:
        "Monitoring will stop and the stored OAuth credential will be removed.",
      confirmLabel: "Disconnect",
      variant: "danger",
    });
    if (ok)
      run(() =>
        api(`/api/candidates/${candidateId}/mailbox`, { method: "DELETE" }),
      );
  };
  const bulkImport = () =>
    run(async () => {
      const mailboxes = bulkText
        .split(/\r?\n/)
        .map((line) => {
          const [candidate_id, email_address] = line
            .split(",")
            .map((v) => v.trim());
          return { candidate_id, email_address };
        })
        .filter((v) => v.candidate_id && v.email_address);
      await api("/api/candidate-mailboxes/bulk-import", {
        method: "POST",
        body: JSON.stringify({ mailboxes }),
      });
      setBulkText("");
    });
  const names = Object.fromEntries(candidates.map((c) => [c.id, c.name]));
  const filtered = useMemo(
    () =>
      events
        .filter(shouldShowInSelectionOfferReview)
        .filter(
          (e) =>
            (!filters.candidate || e.candidate_id === filters.candidate) &&
            (!filters.company ||
              String(e.company_name || "")
                .toLowerCase()
                .includes(filters.company.toLowerCase())) &&
            (!filters.status ||
              (STATUS_FILTERS[filters.status] || [filters.status]).includes(
                e.primary_status,
              )) &&
            (!filters.review || e.review_status === filters.review) &&
            (!filters.confidence ||
              Number(e.confidence) >= Number(filters.confidence)) &&
            (!filters.from || String(e.created_at) >= filters.from) &&
            (!filters.to || String(e.created_at) <= `${filters.to}T23:59:59`),
        )
        .sort((a, b) =>
          filters.sort === "oldest"
            ? String(a.created_at).localeCompare(String(b.created_at))
            : filters.sort === "high"
              ? Number(b.confidence) - Number(a.confidence)
              : filters.sort === "low"
                ? Number(a.confidence) - Number(b.confidence)
                : filters.sort === "candidate"
                  ? String(names[a.candidate_id]).localeCompare(
                      String(names[b.candidate_id]),
                    )
                  : String(b.created_at).localeCompare(String(a.created_at)),
        ),
    [events, filters, names],
  );
  const pageRows = filtered.slice(page * 20, page * 20 + 20);
  const cards = [
    ["Candidates selected", metrics.selections_detected],
    ["Offer indications", metrics.offer_indications],
    ["Offers in progress", metrics.offers_in_progress],
    ["Offer letters", metrics.offer_letters_detected],
    ["Appointment letters", metrics.appointment_letters_detected],
    ["Offers accepted", metrics.offers_accepted],
    ["Joining confirmations", metrics.joining_confirmations],
    ["Candidates joined", metrics.candidates_joined],
    ["Pending important reviews", metrics.pending_reviews],
    ["Verified offers", metrics.verified_offers],
  ];
  return (
    <main className="recruitment-mail-page">
      <header className="recruitment-mail-header">
        <div>
          <h2>Selection and Offer Review</h2>
          <p>
            Only job selections, offers, joining confirmations, and related
            verification mail.
          </p>
        </div>
        <button onClick={load}>Refresh</button>
      </header>
      {message && <div className="recruitment-mail-alert">{message}</div>}
      <section className="recruitment-mail-metrics">
        {cards.map(([name, value]) => (
          <article key={name}>
            <span>{name}</span>
            <strong>{value ?? 0}</strong>
          </article>
        ))}
      </section>
      <nav className="recruitment-mail-tabs">
        {[
          ["reviews", "Review queue"],
          ["mailboxes", "Mailboxes"],
          ["offers", "Offer cases"],
          ["timeline", "Candidate timeline"],
          ["analytics", "Analytics"],
        ].map(([value, name]) => (
          <button
            className={tab === value ? "active" : ""}
            onClick={() => setTab(value)}
            key={value}
          >
            {name}
          </button>
        ))}
      </nav>
      {tab === "reviews" && (
        <section className="recruitment-mail-card">
          <div className="recruitment-mail-section-title">
            <h3>Important job outcome review</h3>
            <span>{filtered.length} records</span>
          </div>
          <div className="recruitment-mail-filters">
            <select
              value={filters.candidate}
              onChange={(e) => {
                setFilters({ ...filters, candidate: e.target.value });
                setPage(0);
              }}
            >
              <option value="">All candidates</option>
              {candidates.map((c) => (
                <option value={c.id} key={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <input
              placeholder="Company"
              value={filters.company}
              onChange={(e) =>
                setFilters({ ...filters, company: e.target.value })
              }
            />
            <select
              value={filters.status}
              onChange={(e) =>
                setFilters({ ...filters, status: e.target.value })
              }
            >
              <option value="">All important detections</option>
              <option value="selected">Selected</option>
              <option value="offer_indication">Offer indication</option>
              <option value="offer_in_progress">Offer in progress</option>
              <option value="offer_letter">Offer letter</option>
              <option value="appointment_letter">Appointment letter</option>
              <option value="offer_accepted">Offer accepted</option>
              <option value="joining_confirmed">Joining confirmed</option>
              <option value="joined">Joined</option>
              <option value="MANUAL_REVIEW_REQUIRED">
                Manual review required
              </option>
            </select>
            <select
              value={filters.review}
              onChange={(e) =>
                setFilters({ ...filters, review: e.target.value })
              }
            >
              <option value="">All review decisions</option>
              {["PENDING", "APPROVED", "REJECTED"].map((v) => (
                <option key={v}>{human(v)}</option>
              ))}
            </select>
            <select
              value={filters.confidence}
              onChange={(e) =>
                setFilters({ ...filters, confidence: e.target.value })
              }
            >
              <option value="">Any confidence</option>
              <option value="0.9">90%+</option>
              <option value="0.8">80%+</option>
            </select>
            <input
              type="date"
              value={filters.from}
              onChange={(e) => setFilters({ ...filters, from: e.target.value })}
            />
            <input
              type="date"
              value={filters.to}
              onChange={(e) => setFilters({ ...filters, to: e.target.value })}
            />
            <select
              value={filters.sort}
              onChange={(e) => setFilters({ ...filters, sort: e.target.value })}
            >
              {[
                ["newest", "Newest"],
                ["oldest", "Oldest"],
                ["high", "Highest confidence"],
                ["low", "Lowest confidence"],
                ["candidate", "Candidate"],
              ].map(([v, n]) => (
                <option value={v} key={v}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <ReviewTable
            rows={pageRows}
            names={names}
            onReview={review}
            onEvidence={setEvidenceId}
          />
          <div className="recruitment-mail-pagination">
            <button disabled={!page} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span>Page {page + 1}</span>
            <button
              disabled={(page + 1) * 20 >= filtered.length}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </div>
        </section>
      )}
      {tab === "mailboxes" && (
        <>
          <section className="recruitment-mail-card">
            <h3>Candidate mailbox</h3>
            <div className="recruitment-mail-form">
              <label>
                Candidate
                <select
                  value={candidateId}
                  onChange={(e) => setCandidateId(e.target.value)}
                >
                  <option value="">Select candidate</option>
                  {candidates.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} · {c.phone || "no phone"}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Authorized Gmail
                <input
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Authorized Gmail address"
                />
              </label>
              {!mailbox?.connection_status ||
              ["PENDING", "DISCONNECTED", "ERROR"].includes(
                mailbox.connection_status,
              ) ? (
                <button
                  disabled={!candidateId || !email || busy}
                  onClick={connect}
                >
                  Connect Gmail
                </button>
              ) : (
                <>
                  <span
                    className={`recruitment-mail-status is-${String(mailbox.connection_status).toLowerCase()}`}
                  >
                    {mailbox.connection_status}
                  </span>
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(() =>
                        api(`/api/candidates/${candidateId}/mailbox/verify`, {
                          method: "POST",
                        }),
                      )
                    }
                  >
                    Verify
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(() =>
                        api(`/api/candidates/${candidateId}/mailbox/sync`, {
                          method: "POST",
                        }),
                      )
                    }
                  >
                    Sync now
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(() =>
                        api(`/api/candidates/${candidateId}/mailbox/settings`, {
                          method: "PATCH",
                          body: JSON.stringify({
                            monitoring_enabled: !mailbox.monitoring_enabled,
                          }),
                        }),
                      )
                    }
                  >
                    {mailbox.monitoring_enabled
                      ? "Disable monitoring"
                      : "Enable monitoring"}
                  </button>
                  <button disabled={busy} onClick={disconnect}>
                    Disconnect
                  </button>
                </>
              )}
            </div>
            {mailbox && (
              <>
                <dl className="recruitment-mail-details">
                  <div>
                    <dt>Last attempt</dt>
                    <dd>{when(mailbox.last_sync_attempt_at)}</dd>
                  </div>
                  <div>
                    <dt>Last successful sync</dt>
                    <dd>{when(mailbox.last_successful_sync_at)}</dd>
                  </div>
                  <div>
                    <dt>Next sync</dt>
                    <dd>{when(mailbox.next_sync_at)}</dd>
                  </div>
                  <div>
                    <dt>Latest error</dt>
                    <dd>{mailbox.last_error_message || "None"}</dd>
                  </div>
                </dl>
                <div className="recruitment-mail-mini-metrics">
                  {Object.entries(mailboxStats).map(([name, value]) => (
                    <span key={name}>
                      {human(name)} <strong>{value}</strong>
                    </span>
                  ))}
                </div>
                {mailbox.connection_status === "CONNECTED" && (
                  <div className="recruitment-mail-rescan">
                    <div>
                      <strong>Historical detection repair</strong>
                      <p>
                        Reprocess stored mail first, then fetch only missing
                        Gmail content. Existing events and offer cases are
                        updated without duplicates.
                      </p>
                    </div>
                    <label>
                      From
                      <input
                        type="date"
                        value={rescanRange.range_start}
                        max={rescanRange.range_end}
                        onChange={(e) =>
                          setRescanRange((value) => ({
                            ...value,
                            range_start: e.target.value,
                          }))
                        }
                      />
                    </label>
                    <label>
                      To
                      <input
                        type="date"
                        value={rescanRange.range_end}
                        min={rescanRange.range_start}
                        onChange={(e) =>
                          setRescanRange((value) => ({
                            ...value,
                            range_end: e.target.value,
                          }))
                        }
                      />
                    </label>
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          await api(
                            `/api/candidates/${candidateId}/mailbox/rescan`,
                            {
                              method: "POST",
                              body: JSON.stringify(rescanRange),
                            },
                          );
                          setMessage(
                            "Historical rescan queued. Important joining and offer emails will appear in Review queue automatically.",
                          );
                        })
                      }
                    >
                      Rescan Important Historical Emails
                    </button>
                  </div>
                )}
              </>
            )}
          </section>
          <section className="recruitment-mail-card">
            <h3>Bulk onboarding</h3>
            <p>
              One line per mailbox: <code>candidate-id,authorized-address</code>
              . OAuth authorization is still required.
            </p>
            <textarea
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              rows={5}
            />
            <button disabled={!bulkText.trim() || busy} onClick={bulkImport}>
              Import pending mailboxes
            </button>
          </section>
        </>
      )}
      {tab === "offers" && (
        <section className="recruitment-mail-card">
          <h3>Offer verification cases</h3>
          <div className="recruitment-mail-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Company / role</th>
                  <th>CTC</th>
                  <th>Joining</th>
                  <th>Confidence</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {offers.length ? (
                  offers.map((row) => (
                    <tr key={row.id}>
                      <td>{names[row.candidate_id] || row.candidate_id}</td>
                      <td>
                        {row.company_name || "—"}
                        <small>{row.job_title}</small>
                      </td>
                      <td>
                        {row.offered_ctc
                          ? `${row.currency || "INR"} ${Number(row.offered_ctc).toLocaleString()}`
                          : "—"}
                      </td>
                      <td>{row.joining_date || "—"}</td>
                      <td>{Math.round(Number(row.confidence) * 100)}%</td>
                      <td>{human(row.verification_status)}</td>
                      <td>
                        <div className="recruitment-mail-actions">
                          {row.verification_status === "PENDING_REVIEW" &&
                            ["verify", "reject", "duplicate", "dispute"].map(
                              (action) => (
                                <button
                                  key={action}
                                  onClick={() => offerReview(row.id, action)}
                                >
                                  {human(action)}
                                </button>
                              ),
                            )}
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7}>No offer cases.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {tab === "timeline" && (
        <section className="recruitment-mail-card">
          <h3>Candidate selection and offer timeline</h3>
          <select
            value={candidateId}
            onChange={(e) => setCandidateId(e.target.value)}
          >
            <option value="">Select candidate</option>
            {candidates.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <ol className="recruitment-timeline">
            {timeline.map((item) => (
              <li key={item.id}>
                <time>{when(item.created_at)}</time>
                <strong>{human(item.primary_status)}</strong>
                <span>
                  {item.company_name || "Unknown company"} ·{" "}
                  {item.job_title || "Unknown role"} ·{" "}
                  {Math.round(Number(item.confidence) * 100)}%
                </span>
                <p>{item.summary}</p>
                <em>{human(item.review_status)}</em>
                <button onClick={() => setEvidenceId(item.id)}>
                  View evidence
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
      {tab === "analytics" && (
        <section className="recruitment-mail-analytics">
          <article className="recruitment-mail-card">
            <h3>Events by day</h3>
            {(charts.events_by_day || []).map((row) => (
              <div className="recruitment-mail-bar" key={row.day}>
                <span>{row.day}</span>
                <i style={{ width: `${Math.min(100, row.count * 8)}%` }} />
                <strong>{row.count}</strong>
              </div>
            ))}
          </article>
          <article className="recruitment-mail-card">
            <h3>Status distribution</h3>
            {(charts.status_distribution || []).map((row) => (
              <div className="recruitment-mail-bar" key={row.status}>
                <span>{human(row.status)}</span>
                <i style={{ width: `${Math.min(100, row.count * 8)}%` }} />
                <strong>{row.count}</strong>
              </div>
            ))}
          </article>
          <article className="recruitment-mail-card">
            <h3>Conflicts and duplicates</h3>
            {flags.length ? (
              flags.map((flag) => (
                <p key={flag.id}>
                  <strong>{human(flag.flag_type)}</strong> ·{" "}
                  {names[flag.candidate_id] || flag.candidate_id}
                </p>
              ))
            ) : (
              <p>No pending risk flags.</p>
            )}
          </article>
        </section>
      )}
      {evidenceId && (
        <EvidencePanel
          eventId={evidenceId}
          onClose={() => setEvidenceId(null)}
          onChanged={load}
        />
      )}
    </main>
  );
}
