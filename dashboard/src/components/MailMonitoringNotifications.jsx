import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API } from "../config.js";
import { subscribeMailEvents, subscribeMailStatus } from "../notifications/mailEventStream.js";
import { useDialogA11y } from "../hooks/useDialogA11y.js";
import { formatIstDateTime, formatScheduleDateTime, formatScheduleIstDateTime } from "../utils/istTime.js";
import { useConfirm } from "../context/ConfirmContext.jsx";
import { InlineLoader, OverlayLoader } from "../Loader.jsx";

// Important candidate employment outcomes and actionable interview activity.
const TRACKED_CLASSIFICATIONS = [
  "job_selection_confirmed", "offer_received", "offer_accepted",
  "offer_declined", "offer_revoked", "joining_confirmed",
  "joining_date_updated", "onboarding_started", "background_verification",
  "document_verification", "compensation_confirmation",
  "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
  "interview_cancelled", "candidate_rejected",
];
// Tracked categories for the compact notification-type filter.
const JOB_CONFIRMED_CLASSIFICATIONS = [
  "job_selection_confirmed", "offer_received", "offer_accepted",
  "offer_declined", "offer_revoked", "joining_confirmed",
  "joining_date_updated", "onboarding_started", "background_verification",
  "document_verification", "compensation_confirmation", "candidate_rejected",
];
const AUTO_BOOKING_CLASSIFICATIONS = [
  "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
  "interview_cancelled",
];
const human = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const when = (value) => formatIstDateTime(value, {
  day: "numeric",
  hour: "numeric",
  second: undefined,
});
const confidence = (value) => `${Math.round(Number(value || 0) * 100)}%`;
const plainEmailBody = (value) => {
  const source = String(value || "");
  if (!source || !/<[a-z][\s\S]*>/i.test(source) || typeof DOMParser === "undefined") return source;
  return new DOMParser().parseFromString(source, "text/html").body.textContent || "";
};

export function mailStatusTone(item = {}) {
  const status = [item.candidate_status, item.booking_status, item.classification]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (/automatically booked|auto booked|already booked|approved.*booked|joining confirmed|selection confirmed|offer accepted/.test(status)) return "success";
  if (/processing failed|cancelled|rejected|failed/.test(status)) return "danger";
  if (/booking blocked|blocked/.test(status)) return "warning";
  if (/needs review|review required|pending review|review only/.test(status)) return "review";
  if (/rescheduled|interview confirmed|offer received/.test(status)) return "info";
  return "neutral";
}

// Why a booking was blocked, as the backend decided it. Never reconstructed
// here from the status text: the row must show the actual decision, and only
// the validator knows which of several blocks applied.
export function blockingReason(item = {}) {
  if (!item.booking_block_reason && !item.booking_block_reason_code) return null;
  return {
    text: item.booking_block_reason || "Booking requires manual review",
    code: item.booking_block_reason_code || "",
    internal: item.booking_failure_code || "",
  };
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

function navigate(view, detail = {}) {
  window.dispatchEvent(new CustomEvent("teleautomation:navigate", { detail: { view, ...detail } }));
}

/**
 * Live mail events for UI.
 *
 * The socket itself now lives in notifications/mailEventStream.js and is shared
 * by every subscriber, so this page and the header bell no longer open one
 * each. Sound is not triggered here: GlobalNotificationSounds subscribes to the
 * same stream and is the only place that makes a noise, which is what stops one
 * event from being heard twice.
 */
function useMailLive(onUpdate) {
  const [status, setStatus] = useState("Offline");
  const callback = useRef(onUpdate);
  callback.current = onUpdate;

  useEffect(() => subscribeMailEvents((payload) => callback.current?.(payload)), []);
  useEffect(() => subscribeMailStatus(setStatus), []);

  return status;
}

export function MailNotificationBell({ compact = false }) {
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState({ unread: 0 });
  const [items, setItems] = useState([]);
  const [toast, setToast] = useState(null);
  const wrap = useRef(null);
  const load = useCallback(async () => {
    try {
      const [summaryBody, listBody] = await Promise.all([
        request("/api/mail-monitoring/summary"),
        request("/api/mail-monitoring/notifications?limit=6&offset=0"),
      ]);
      setSummary(summaryBody.summary || {}); setItems(listBody.notifications || []);
    } catch { /* API fallback will retry */ }
  }, []);
  const live = useMailLive((event) => {
    if (["notification_created", "important_mail_detected", "mail_needs_review", "connected"].includes(event?.event)) load();
    if (event?.event === "notification_created") {
      setToast(event); window.setTimeout(() => setToast(null), 6000);
    }
  });
  useEffect(() => {
    load(); const id = window.setInterval(load, 30000); return () => window.clearInterval(id);
  }, [load]);
  useEffect(() => {
    // Ask up front so the first tracked mail doesn't spend its alert on a prompt.
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }, []);
  useEffect(() => {
    const close = (event) => wrap.current && !wrap.current.contains(event.target) && setOpen(false);
    document.addEventListener("mousedown", close); return () => document.removeEventListener("mousedown", close);
  }, []);
  const action = async (id, name) => {
    await request(`/api/mail-monitoring/notifications/${id}/${name}`, { method: "POST", body: "{}" });
    await load();
  };
  const unread = Number(summary.unread || 0);
  return <div className={`mail-bell${compact ? " mail-bell--compact" : ""}`} ref={wrap}>
    <button type="button" className="mail-bell__button" aria-label={`${unread} unread mail monitoring notifications`} title="Mail monitoring alerts" onClick={() => setOpen((value) => !value)}>
      <span aria-hidden>📧</span>{unread > 0 && <span className="mail-bell__count">{unread > 99 ? "99+" : unread}</span>}
    </button>
    {open && <div className="mail-bell__popover">
      <header><div><strong>Mail monitoring</strong><span className={`mail-live mail-live--${live.toLowerCase()}`}>{live}</span></div><button type="button" onClick={() => { setOpen(false); navigate("mail-notifications"); }}>View all</button></header>
      <div className="mail-bell__list">{items.length ? items.map((item) => <article className={item.is_read ? "" : "is-unread"} key={item.id}>
        <button type="button" className="mail-bell__main" onClick={() => { if (!item.is_read) action(item.id, "read"); setOpen(false); navigate("mail-notifications", { notificationId: item.id }); }}>
          <strong>{item.candidate_name || "Candidate"} · {item.company_name || "Company pending"}</strong>
          <span>{item.candidate_status || human(item.classification)}</span><small>{when(item.email_received_at || item.created_at)}</small>
        </button>
        <button type="button" className="mail-bell__toggle" onClick={() => action(item.id, item.is_read ? "unread" : "read")}>{item.is_read ? "Unread" : "Read"}</button>
      </article>) : <p className="mail-empty">No mail alerts yet.</p>}</div>
    </div>}
    {toast && <button type="button" className="mail-alert-toast" onClick={() => { setToast(null); navigate("mail-notifications", { notificationId: toast.notification_id }); }}><strong>{toast.status || human(toast.classification)}</strong><span>{toast.candidate_name || "Candidate"}{toast.company_name ? ` · ${toast.company_name}` : ""}</span></button>}
  </div>;
}

function NotificationDetail({ item, onClose, onChanged }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose);
  const [note, setNote] = useState(item.review_notes || "");
  const [classification, setClassification] = useState(item.classification);
  const [candidateStatus, setCandidateStatus] = useState(item.candidate_status || "Needs Review");
  const act = async (action, changes) => {
    await request(`/api/mail-monitoring/notifications/${item.id}/${action}`, { method: "POST", body: JSON.stringify({ notes: note, changes }) });
    onChanged(); if (action !== "read" && action !== "unread") onClose();
  };
  const viewAudit = async () => {
    const params = new URLSearchParams(item.booking_id ? { booking_id: item.booking_id } : { candidate_id: item.candidate_id });
    const body = await request(`/api/mail-monitoring/booking-audit?${params}`);
    const rows = body.audit || [];
    window.alert(rows.length ? rows.map((row) => `${when(row.created_at)} — ${row.booking_status}${row.failure_message ? ` — ${row.failure_message}` : ""}`).join("\n") : "No booking audit history found.");
  };
  const originalEmail = item.event_detail?.received_email;
  // Empty when the invite is already IST, so the extra line appears only when
  // the reader actually has to convert something.
  const istInterviewTime = formatScheduleIstDateTime(
    item.interview_date,
    item.interview_time,
    item.interview_timezone,
  );
  return <div className="mail-detail-backdrop" role="presentation" onClick={(event) => event.target === event.currentTarget && onClose()}>
    <section ref={dialogRef} className="mail-detail" role="dialog" aria-modal="true" aria-label="Mail monitoring notification">
      <header><div><h3>{item.candidate_status || human(item.classification)}</h3><p>{item.candidate_name || "Candidate"} · {item.company_name || "Company unavailable"}</p></div><button type="button" onClick={onClose} aria-label="Close">×</button></header>
      <dl><div><dt>Email</dt><dd>{item.email_subject || "No subject"}</dd></div><div><dt>From</dt><dd>{item.sender_name || item.sender_email || "Unknown"}</dd></div><div><dt>Mail received</dt><dd>{when(item.email_received_at)}</dd></div><div><dt>Tool detected</dt><dd>{when(item.created_at)}</dd></div><div><dt>AI confidence</dt><dd>{confidence(item.ai_confidence)}</dd></div>{item.booking_status && <div><dt>Booking</dt><dd>{item.booking_status}</dd></div>}{item.interview_date && <div><dt>Interview</dt><dd>{formatScheduleDateTime(item.interview_date, item.interview_time, item.interview_timezone)}</dd></div>}{istInterviewTime && <div><dt>IST Time</dt><dd>{istInterviewTime}</dd></div>}{item.interview_round && <div><dt>Round</dt><dd>{item.interview_round}</dd></div>}{(() => {
        const reason = blockingReason(item);
        if (!reason) return null;
        return <>
          <div><dt>Blocking reason</dt><dd>{reason.text}</dd></div>
          <div><dt>Reason code</dt><dd><code>{reason.code}</code>{reason.internal && reason.internal !== reason.code ? <> · <code>{reason.internal}</code></> : null}</dd></div>
          <div><dt>Attempted booking</dt><dd>{item.booking_status || "Not attempted"} — no slot was created</dd></div>
        </>;
      })()}</dl>
      <section className="mail-detail__original" aria-label="Original email">
        <strong>Original email</strong>
        {item.detail_loading
          ? <InlineLoader label="Loading original email…" />
          : originalEmail
            ? <>
                <div className="mail-detail__email-meta">
                  <span><b>From:</b> {originalEmail.sender_name || originalEmail.sender_email || "Unknown"}</span>
                  <span><b>To:</b> {originalEmail.recipient_email || item.candidate_email || "Unknown"}</span>
                  <span><b>Received:</b> {when(originalEmail.sent_at || item.email_received_at)}</span>
                  <span><b>Subject:</b> {originalEmail.subject || item.email_subject || "(no subject)"}</span>
                </div>
                <pre>{plainEmailBody(originalEmail.body) || "This email has no text body."}</pre>
              </>
            : <p>{item.detail_error || "The original email body is unavailable."}</p>}
      </section>
      <div className="mail-detail__copy"><strong>Summary</strong><p>{item.ai_summary || "No summary available."}</p><strong>Detection reason</strong><p>{item.ai_reason || "Contextual classification"}</p><strong>Recommended action</strong><p>{item.recommended_action || "Review the candidate and email before taking action."}</p></div>
      <label>Review note<textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} /></label>
      <div className="mail-detail__correction"><select value={classification} onChange={(event) => setClassification(event.target.value)}>{TRACKED_CLASSIFICATIONS.map((value) => <option value={value} key={value}>{human(value)}</option>)}</select><input value={candidateStatus} onChange={(event) => setCandidateStatus(event.target.value)} maxLength={80} /></div>
      <footer>
        {item.booking_id && <button type="button" onClick={() => { onClose(); navigate("daily-ops", { bookingId: item.booking_id, candidateId: item.candidate_id }); }}>View booking</button>}
        <button type="button" onClick={() => { sessionStorage.setItem("cand-open-pending", JSON.stringify({ candidate_id:item.candidate_id, candidate_name:item.candidate_name, action:"contact" })); navigate("candidates", { candidateId: item.candidate_id }); }}>View / contact candidate</button>
        <button type="button" onClick={() => { sessionStorage.setItem("cand-open-pending", JSON.stringify({ candidate_id:item.candidate_id, candidate_name:item.candidate_name, action:"payment-follow-up" })); navigate("candidates", { candidateId: item.candidate_id, action: "payment-follow-up" }); }}>Start payment follow-up</button>
        {item.booking_status && <button type="button" onClick={() => { sessionStorage.setItem("cand-open-pending", JSON.stringify({ candidate_id:item.candidate_id, candidate_name:item.candidate_name, action:"payment-follow-up" })); navigate("candidates", { candidateId: item.candidate_id, action: "payment-follow-up" }); }}>View payment</button>}
        {item.gmail_message_id && <button type="button" onClick={() => window.open(`https://mail.google.com/mail/u/?authuser=${encodeURIComponent(item.candidate_email || "")}#all/${encodeURIComponent(item.gmail_message_id)}`, "_blank", "noopener,noreferrer")}>View email</button>}
        {/^https?:\/\//i.test(item.meeting_link || "") && <button type="button" onClick={() => window.open(item.meeting_link, "_blank", "noopener,noreferrer")}>Open meeting link</button>}
        {item.booking_audit_id && <button type="button" onClick={viewAudit}>View audit history</button>}
        <button type="button" onClick={() => act("false-detection")}>False detection</button>
        <button type="button" onClick={() => act("rerun")}>Re-run AI</button>
        <button type="button" onClick={() => act("correct", { classification, candidate_status: candidateStatus })}>Save correction</button>
        <button type="button" className="mail-primary" onClick={() => act("reviewed")}>Confirm & reviewed</button>
      </footer>
    </section>
  </div>;
}

export function MailMonitoringNotifications() {
  const { confirm } = useConfirm();
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState({ new_offers: 0, selections: 0, joining_confirmations: 0, auto_booked_interviews: 0, needs_review: 0, unread: 0 });
  const [selected, setSelected] = useState(null);
  const [clearing, setClearing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState({ search: "", classification: "", priority: "", read: "" });
  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "20", offset: String(page * 20), sort: "newest" });
    for (const [key, value] of Object.entries({ search:filters.search,classification:filters.classification,priority:filters.priority,is_read:filters.read })) if (value !== "") params.set(key, String(value));
    return params.toString();
  }, [filters, page]);
  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try { const [list, counts] = await Promise.all([request(`/api/mail-monitoring/notifications?${query}`),request("/api/mail-monitoring/summary")]); setItems(list.notifications || []);setTotal(list.total || 0);setSummary(counts.summary || {}); } catch { /* retain last good state */ }
    finally { if (!silent) setLoading(false); }
  }, [query]);
  useMailLive((event) => ["notification_created","important_mail_detected","mail_needs_review","connected"].includes(event?.event) && load({ silent:true }));
  useEffect(() => { load(); }, [load]);
  const set = (key) => (event) => { setPage(0); setFilters((value) => ({ ...value, [key]: event.target.value })); };
  const act = async (item, action) => { await request(`/api/mail-monitoring/notifications/${item.id}/${action}`, { method:"POST", body:"{}" }); load({ silent:true }); };
  const openNotification = useCallback(async (item) => {
    setSelected({ ...item, is_read: true, detail_loading: true, detail_error: "" });
    if (!item.is_read) {
      setItems((rows) => rows.map((row) => row.id === item.id ? { ...row, is_read: true } : row));
      setSummary((value) => ({ ...value, unread: Math.max(0, Number(value.unread || 0) - 1) }));
    }
    const detailRequest = item.ai_recruitment_event_id
      ? request(`/api/ai-recruitment/events/${item.ai_recruitment_event_id}`)
      : Promise.resolve({ event: null });
    try {
      const [detail] = await Promise.all([
        detailRequest,
        item.is_read
          ? Promise.resolve()
          : request(`/api/mail-monitoring/notifications/${item.id}/read`, { method:"POST", body:"{}" }),
      ]);
      setSelected((current) => current?.id === item.id
        ? { ...current, detail_loading: false, event_detail: detail.event || null }
        : current);
    } catch (error) {
      setSelected((current) => current?.id === item.id
        ? { ...current, detail_loading: false, detail_error: error.message || "Unable to load the original email." }
        : current);
    } finally {
      load({ silent:true });
    }
  }, [load]);
  const clearAll = async () => {
    const count=summary.visible_total ?? total;
    const confirmed = await confirm({
      title: "Clear all mail notifications?",
      message: `Remove all ${count} notifications from this list across every filter?`,
      confirmLabel: "Clear notifications",
      cancelLabel: "Keep notifications",
      variant: "danger",
      kept: ["Email evidence", "Booking audits", "Candidate history"],
    });
    if (!confirmed) return;
    setClearing(true);
    try {
      await request("/api/mail-monitoring/notifications/clear-all", { method:"POST", body:"{}" });
      setSelected(null);setPage(0);await load();
    } finally { setClearing(false); }
  };
  return <section className="mail-monitoring-page">
    <header className="mail-monitoring-page__head"><div><p className="mail-eyebrow">AI MAIL MONITORING</p><h1>Mail Monitoring Notifications</h1><p>Persistent candidate job-status alerts with live delivery and administrator review.</p></div><div className="mail-monitoring-page__actions"><button type="button" className="mail-clear-all" disabled={!(summary.visible_total ?? total) || clearing} onClick={clearAll}>{clearing ? "Clearing…" : "Clear all notifications"}</button><span className="mail-live mail-live--live">Live</span></div></header>
    <div className="mail-summary mail-summary--compact">
      <button onClick={() => { setPage(0);setFilters({ search:"", classification:"", priority:"", read:"" }); }}><strong>{summary.visible_total || 0}</strong><span>All</span></button>
      <button onClick={() => { setPage(0);setFilters((value) => ({ ...value, priority:"review_required", read:"" })); }}><strong>{summary.needs_review || 0}</strong><span>Needs review</span></button>
      <button onClick={() => { setPage(0);setFilters((value) => ({ ...value, read:"false", priority:"" })); }}><strong>{summary.unread || 0}</strong><span>Unread</span></button>
    </div>
    <div className="mail-filters mail-filters--compact">
      <input aria-label="Search notifications" placeholder="Search candidate, email, company or subject" value={filters.search} onChange={set("search")} />
      <select aria-label="Classification filter" value={filters.classification} onChange={set("classification")}><option value="">All notification types</option>{TRACKED_CLASSIFICATIONS.map((value) => <option value={value} key={value}>{human(value)}</option>)}</select>
    </div>
    <div className={`mail-table-wrap${loading ? " is-loading" : ""}`}>{loading && <OverlayLoader label="Loading notifications…" />}<table className="mail-table"><thead><tr><th>Candidate</th><th>Company</th><th>Detected status</th><th>Email subject</th><th>Confidence</th><th>Mail received</th><th>Tool detected</th><th>Review</th><th>Action</th></tr></thead><tbody>
      {items.map((item) => <tr
        key={item.id}
        className={`${item.is_read ? "" : "is-unread"} mail-notification-row`}
        tabIndex={0}
        aria-label={`Open email notification: ${item.email_subject || "no subject"}`}
        onClick={() => openNotification(item)}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget || !["Enter", " "].includes(event.key)) return;
          event.preventDefault();
          openNotification(item);
        }}
      ><td><strong>{item.candidate_name || "Candidate"}</strong><small>{item.candidate_email || ""}</small></td><td>{item.company_name || "—"}<small>{item.job_role || ""}</small></td><td><span className={`mail-status mail-status--${mailStatusTone(item)}`}>{item.candidate_status || human(item.classification)}</span>{(() => {
        const reason = blockingReason(item);
        if (!reason) return null;
        // Shown in the row itself: a blocked booking is unusable information
        // until you know why, and making that a click away hides it.
        return <span
          className="mail-status__reason"
          title={reason.internal ? `${reason.text} (${reason.code} / ${reason.internal})` : `${reason.text} (${reason.code})`}
        >Reason: {reason.text}</span>;
      })()}</td><td>{item.email_subject || "(no subject)"}</td><td>{confidence(item.ai_confidence)}</td><td>{when(item.email_received_at)}</td><td>{when(item.created_at)}</td><td>{item.is_reviewed ? "Reviewed" : "Pending"}</td><td onClick={(event) => event.stopPropagation()}><button onClick={() => openNotification(item)}>Open</button><button onClick={() => act(item,item.is_read ? "unread" : "read")}>{item.is_read ? "Unread" : "Read"}</button><button onClick={() => act(item,"dismiss")}>Dismiss</button></td></tr>)}
      {!loading && !items.length && <tr><td colSpan={9} className="mail-empty">No notifications match these filters.</td></tr>}
    </tbody></table></div>
    {total > 20 && <footer className="mail-pagination"><span>{total} notifications</span><button disabled={page===0} onClick={() => setPage((value) => value-1)}>Previous</button><span>Page {page+1}</span><button disabled={(page+1)*20>=total} onClick={() => setPage((value) => value+1)}>Next</button></footer>}
    {selected && <NotificationDetail item={selected} onClose={() => setSelected(null)} onChanged={load} />}
  </section>;
}
