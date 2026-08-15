import React, { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env?.VITE_API_BASE ?? "";

const CLASSIFICATION_LABELS = {
  EXACT_MATCH: "Exact match",
  SAFE_AUTOMATIC_CORRECTION: "Safe to correct",
  GENUINE_MISMATCH: "Genuine mismatch",
  DUPLICATE_TRANSACTION: "Duplicate transaction",
  MISSING_EVIDENCE: "Missing evidence",
  LEGACY_INCOMPLETE_COVERAGE: "Legacy incomplete",
  BGV_ALLOCATION_ISSUE: "BGV allocation issue",
  UNALLOCATED_EXCESS: "Unallocated excess",
  EXTRACTOR_DEFECT_CORRECTED: "Extractor defect corrected",
  ADMIN_CONFIRMED_NOT_PAID: "Confirmed not paid",
  MANUAL_REVIEW_REQUIRED: "Manual review",
};

// Three tones only: settled, needs a decision, actively wrong.
const TONE = {
  EXACT_MATCH: "ok",
  SAFE_AUTOMATIC_CORRECTION: "warn",
  DUPLICATE_TRANSACTION: "warn",
  LEGACY_INCOMPLETE_COVERAGE: "warn",
  MISSING_EVIDENCE: "warn",
  UNALLOCATED_EXCESS: "warn",
  MANUAL_REVIEW_REQUIRED: "warn",
  GENUINE_MISMATCH: "bad",
  BGV_ALLOCATION_ISSUE: "bad",
};

function rupees(value) {
  const amount = Number(value) || 0;
  return `₹${amount.toLocaleString("en-IN")}`;
}

export default function PaymentReconciliationPanel() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API}/payments/reconciliation`, {
        credentials: "include",
      });
      const payload = await res.json();
      if (payload.status !== "ok") {
        setError(payload.message || "Could not load reconciliation");
        return;
      }
      setData(payload);
    } catch (err) {
      setError(err.message || "Network error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const records = data?.records || [];

  const cards = useMemo(() => {
    const counts = data?.counts || {};
    return [
      { key: "", label: "Profiles checked", value: data?.profiles_checked || 0 },
      { key: "EXACT_MATCH", label: "Exact matches", value: counts.EXACT_MATCH || 0 },
      { key: "GENUINE_MISMATCH", label: "Genuine mismatches",
        value: counts.GENUINE_MISMATCH || 0 },
      { key: "DUPLICATE_TRANSACTION", label: "Duplicate transactions",
        value: counts.DUPLICATE_TRANSACTION || 0 },
      { key: "MISSING_EVIDENCE", label: "Missing evidence",
        value: counts.MISSING_EVIDENCE || 0 },
      { key: "BGV_ALLOCATION_ISSUE", label: "BGV allocation issues",
        value: counts.BGV_ALLOCATION_ISSUE || 0 },
      { key: "EXTRACTOR_DEFECT_CORRECTED", label: "Extractor defects",
        value: counts.EXTRACTOR_DEFECT_CORRECTED || 0 },
      { key: "LEGACY_INCOMPLETE_COVERAGE", label: "Legacy incomplete",
        value: counts.LEGACY_INCOMPLETE_COVERAGE || 0 },
      { key: "UNALLOCATED_EXCESS", label: "Unallocated excess",
        value: counts.UNALLOCATED_EXCESS || 0 },
      { key: "MANUAL_REVIEW_REQUIRED", label: "Manual review",
        value: counts.MANUAL_REVIEW_REQUIRED || 0 },
    ];
  }, [data]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return records.filter((row) => {
      if (filter && row.classification !== filter) return false;
      if (!needle) return true;
      return (
        String(row.candidate_name || "").toLowerCase().includes(needle) ||
        String(row.candidate_id || "").toLowerCase().includes(needle) ||
        (row.utrs || []).some((u) => String(u).toLowerCase().includes(needle))
      );
    });
  }, [records, filter, search]);

  if (loading) {
    return <div className="recon-page recon-page--empty">Loading reconciliation…</div>;
  }
  if (error) {
    return (
      <div className="recon-page recon-page--empty">
        <p className="recon-error">{error}</p>
        <button type="button" className="recon-btn" onClick={load}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="recon-page">
      <header className="recon-head">
        <div>
          <h2 className="recon-title">Payment Reconciliation</h2>
          <p className="recon-sub">
            Recorded money against verified evidence. Nothing here changes a
            figure on its own.
          </p>
        </div>
        <div className="recon-head-actions">
          <a className="recon-btn" href={`${API}/payments/reconciliation.csv`}>
            Export CSV
          </a>
          <button type="button" className="recon-btn" onClick={load}>
            Refresh
          </button>
        </div>
      </header>

      <section className="recon-cards" aria-label="Reconciliation summary">
        {cards.map((card) => (
          <button
            key={card.label}
            type="button"
            className={`recon-card${filter === card.key && card.key ? " recon-card--on" : ""}`}
            onClick={() => setFilter(card.key === filter ? "" : card.key)}
            disabled={!card.key}
          >
            <span className="recon-card-value">{card.value}</span>
            <span className="recon-card-label">{card.label}</span>
          </button>
        ))}
      </section>

      <div className="recon-toolbar">
        <input
          className="recon-search"
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search candidate, id or UTR"
          aria-label="Search reconciliation"
        />
        {filter && (
          <button type="button" className="recon-btn recon-btn--ghost"
                  onClick={() => setFilter("")}>
            Clear filter: {CLASSIFICATION_LABELS[filter] || filter}
          </button>
        )}
        <span className="recon-count">
          {visible.length} of {records.length}
        </span>
      </div>

      <div className="recon-table-wrap">
        <table className="recon-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Expected service</th>
              <th>Expected BGV</th>
              <th>Received</th>
              <th>Verified total</th>
              <th>Service</th>
              <th>BGV</th>
              <th>Outstanding</th>
              <th>Evidence</th>
              <th>Classification</th>
              <th>Recommended action</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.candidate_id}>
                <td>
                  <span className="recon-name">{row.candidate_name}</span>
                  {row.notes?.length > 0 && (
                    <span className="recon-note" title={row.notes.join(" ")}>
                      {row.notes.length} note{row.notes.length > 1 ? "s" : ""}
                    </span>
                  )}
                </td>
                <td>{rupees(row.service_expected)}</td>
                <td>{rupees(row.bgv_expected)}</td>
                <td>{rupees(row.recorded_received)}</td>
                <td>{rupees(row.verified_transaction_total)}</td>
                <td>{rupees(row.service_allocation)}</td>
                <td>{rupees(row.bgv_allocation)}</td>
                <td>{rupees(row.outstanding)}</td>
                <td className="recon-evidence">
                  {(row.file_states || []).join(", ") || "—"}
                </td>
                <td>
                  <span
                    className={`recon-tag recon-tag--${TONE[row.classification] || "warn"}`}
                  >
                    {CLASSIFICATION_LABELS[row.classification] || row.classification}
                  </span>
                </td>
                <td className="recon-action">{row.recommended_action}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {visible.length === 0 && (
          <p className="recon-empty">Nothing matches that filter.</p>
        )}
      </div>
    </div>
  );
}
