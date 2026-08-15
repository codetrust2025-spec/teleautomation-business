import React, { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env?.VITE_API_BASE ?? "";

const STATUS_LABELS = {
  AWAITING_COLLECTION: "Awaiting collection",
  PARTIALLY_COLLECTED: "Partially collected",
  COLLECTED: "Collected",
  PAYMENT_PENDING: "Payment pending",
  PARTIALLY_SETTLED: "Partially settled",
  SETTLED: "Settled",
  SENT_TO_CONSULTANCY: "Sent to consultancy",
  IN_PROGRESS: "In progress",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
  MANUAL_REVIEW: "Manual review",
};

const TONE = {
  SETTLED: "ok",
  COMPLETED: "ok",
  CANCELLED: "muted",
  MANUAL_REVIEW: "bad",
};

function rupees(value) {
  return `₹${(Number(value) || 0).toLocaleString("en-IN")}`;
}

function CaseDetail({ caseId, onBack }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const res = await fetch(`${API}/bgv/cases/${caseId}`, {
          credentials: "include",
        });
        const payload = await res.json();
        if (!live) return;
        if (payload.status === "ok") setDetail(payload.case);
        else setError(payload.message || "Could not load case");
      } catch (err) {
        if (live) setError(err.message || "Network error");
      }
    })();
    return () => {
      live = false;
    };
  }, [caseId]);

  if (error) return <p className="bgv-error">{error}</p>;
  if (!detail) return <p className="bgv-muted">Loading case…</p>;

  return (
    <div className="bgv-detail">
      <button type="button" className="bgv-btn bgv-btn--ghost" onClick={onBack}>
        ← All cases
      </button>
      <header className="bgv-detail-head">
        <div>
          <h3 className="bgv-detail-name">{detail.candidate_name}</h3>
          <p className="bgv-muted">
            {detail.consultancy || "Consultancy not set"} ·{" "}
            {detail.service_description || "Background verification"}
          </p>
        </div>
        <span className={`bgv-tag bgv-tag--${TONE[detail.status] || "warn"}`}>
          {STATUS_LABELS[detail.status] || detail.status}
        </span>
      </header>

      <dl className="bgv-balances">
        <div><dt>Expected</dt><dd>{rupees(detail.bgv_expected)}</dd></div>
        <div><dt>Collected</dt><dd>{rupees(detail.bgv_collected)}</dd></div>
        <div><dt>Outstanding</dt><dd>{rupees(detail.bgv_outstanding)}</dd></div>
        <div><dt>Paid to consultancy</dt><dd>{rupees(detail.paid_to_consultancy)}</dd></div>
        <div><dt>Consultancy payable</dt><dd>{rupees(detail.consultancy_payable)}</dd></div>
        <div><dt>Company earning</dt><dd>{rupees(detail.company_earning)}</dd></div>
        <div><dt>Referral earning</dt><dd>{rupees(detail.referral_earning)}</dd></div>
      </dl>

      {detail.needs_adjustment && (
        <p className="bgv-warn">
          {rupees(detail.over_settled)} more has been paid out than collected.
          That needs a refund or adjustment record.
        </p>
      )}

      <section className="bgv-section">
        <h4>Collections</h4>
        {(detail.collections || []).length === 0 ? (
          <p className="bgv-muted">Nothing collected yet.</p>
        ) : (
          <ul className="bgv-rows">
            {detail.collections.map((row) => (
              <li key={row.collection_id}>
                <span className="bgv-row-amount">{rupees(row.amount)}</span>
                <span className="bgv-row-ref">
                  {row.transaction_reference || row.transaction_id || "—"}
                </span>
                <span className="bgv-row-when">{row.occurred_on || ""}</span>
                <span className={row.verified ? "bgv-ok" : "bgv-pending"}>
                  {row.verified ? "verified" : "unverified"}
                </span>
                {row.note && <span className="bgv-row-note">{row.note}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="bgv-section">
        <h4>Settlements to consultancy</h4>
        {(detail.settlements || []).length === 0 ? (
          <p className="bgv-muted">Nothing settled yet.</p>
        ) : (
          <ul className="bgv-rows">
            {detail.settlements.map((row) => (
              <li key={row.settlement_id}>
                <span className="bgv-row-amount">{rupees(row.amount)}</span>
                <span className="bgv-row-ref">{row.transaction_reference || "—"}</span>
                <span className="bgv-row-when">{row.occurred_on || ""}</span>
                <span className={row.verified ? "bgv-ok" : "bgv-pending"}>
                  {row.verified ? "verified" : "unverified"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="bgv-section">
        <h4>Audit history</h4>
        <ol className="bgv-audit">
          {(detail.audit || []).map((event, index) => (
            <li key={`${event.action}-${index}`}>
              <span className="bgv-audit-action">{event.action}</span>
              <span className="bgv-audit-when">
                {event.at} · {event.actor}
              </span>
              {event.reason && <span className="bgv-row-note">{event.reason}</span>}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

export default function BgvRegisterPanel() {
  const [board, setBoard] = useState(null);
  const [error, setError] = useState("");
  const [openCase, setOpenCase] = useState(null);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const res = await fetch(`${API}/bgv/dashboard`, { credentials: "include" });
      const payload = await res.json();
      if (payload.status === "ok") setBoard(payload);
      else setError(payload.message || "Could not load the BGV register");
    } catch (err) {
      setError(err.message || "Network error");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const cases = useMemo(() => {
    const rows = board?.cases || [];
    const needle = search.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter(
      (row) =>
        String(row.candidate_name || "").toLowerCase().includes(needle) ||
        String(row.consultancy || "").toLowerCase().includes(needle),
    );
  }, [board, search]);

  if (error) {
    return (
      <div className="bgv-page bgv-page--empty">
        <p className="bgv-error">{error}</p>
        <button type="button" className="bgv-btn" onClick={load}>Try again</button>
      </div>
    );
  }
  if (!board) return <div className="bgv-page bgv-page--empty">Loading…</div>;

  if (openCase) {
    return (
      <div className="bgv-page">
        <CaseDetail caseId={openCase} onBack={() => setOpenCase(null)} />
      </div>
    );
  }

  return (
    <div className="bgv-page">
      <header className="bgv-head">
        <div>
          <h2 className="bgv-title">BGV Consultancy</h2>
          <p className="bgv-sub">
            Money collected for a third party and passed on. It earns the company,
            the referrer and the handler nothing.
          </p>
        </div>
        <div className="bgv-head-actions">
          <a className="bgv-btn" href={`${API}/bgv/cases.csv`}>Export CSV</a>
          <button type="button" className="bgv-btn" onClick={load}>Refresh</button>
        </div>
      </header>

      <section className="bgv-cards" aria-label="BGV summary">
        <div className="bgv-card">
          <span className="bgv-card-value">{board.total_cases}</span>
          <span className="bgv-card-label">Total cases</span>
        </div>
        <div className="bgv-card">
          <span className="bgv-card-value">{board.active_cases}</span>
          <span className="bgv-card-label">Active</span>
        </div>
        <div className="bgv-card">
          <span className="bgv-card-value">{board.completed_cases}</span>
          <span className="bgv-card-label">Completed</span>
        </div>
        <div className="bgv-card">
          <span className="bgv-card-value">{rupees(board.collected_total)}</span>
          <span className="bgv-card-label">Collected</span>
        </div>
        <div className="bgv-card">
          <span className="bgv-card-value">{rupees(board.outstanding_total)}</span>
          <span className="bgv-card-label">Still to collect</span>
        </div>
        <div className="bgv-card">
          <span className="bgv-card-value">{rupees(board.paid_to_consultancy_total)}</span>
          <span className="bgv-card-label">Paid to consultancies</span>
        </div>
        <div className="bgv-card">
          <span className="bgv-card-value">{rupees(board.consultancy_payable_total)}</span>
          <span className="bgv-card-label">Remaining payable</span>
        </div>
        <div className="bgv-card">
          <span className="bgv-card-value">{board.needs_review}</span>
          <span className="bgv-card-label">Needs review</span>
        </div>
      </section>

      <div className="bgv-toolbar">
        <input
          className="bgv-search"
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search candidate or consultancy"
          aria-label="Search BGV cases"
        />
        <span className="bgv-count">{cases.length} case{cases.length === 1 ? "" : "s"}</span>
      </div>

      <div className="bgv-table-wrap">
        <table className="bgv-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Consultancy</th>
              <th>Expected</th>
              <th>Collected</th>
              <th>Outstanding</th>
              <th>Paid out</th>
              <th>Payable</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {cases.map((row) => (
              <tr key={row.case_id}>
                <td>{row.candidate_name}</td>
                <td>{row.consultancy || "—"}</td>
                <td>{rupees(row.bgv_expected)}</td>
                <td>{rupees(row.bgv_collected)}</td>
                <td>{rupees(row.bgv_outstanding)}</td>
                <td>{rupees(row.paid_to_consultancy)}</td>
                <td>{rupees(row.consultancy_payable)}</td>
                <td>
                  <span className={`bgv-tag bgv-tag--${TONE[row.status] || "warn"}`}>
                    {STATUS_LABELS[row.status] || row.status}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    className="bgv-btn bgv-btn--ghost bgv-btn--xs"
                    onClick={() => setOpenCase(row.case_id)}
                  >
                    Open
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {cases.length === 0 && <p className="bgv-muted">No BGV cases yet.</p>}
      </div>
    </div>
  );
}
