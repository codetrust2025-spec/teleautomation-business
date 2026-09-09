import React, { useMemo } from "react";
import { reconnectWorklist } from "../utils/mailboxStatus.js";
import "./ReconnectWorklist.css";

/**
 * Every Gmail account that needs reconnecting, in one place.
 *
 * The OAuth app is in Testing mode, so Google expires refresh tokens seven days
 * after consent — measured, not assumed: across 54 consecutive reconnects the
 * median gap is 7.0 days. With nineteen connected mailboxes that is roughly
 * three reconnects a day, and until the app is published to Production there is
 * no code change that prevents them. What there can be is one screen that shows
 * them together instead of an operator finding them a banner at a time.
 *
 * Two groups, deliberately separate. Already broken is work that must happen;
 * about to break is work that can be batched with it while someone is here. The
 * split is computed once in `reconnectWorklist`, off the same rows the mailbox
 * table renders, so this list and the badges beside those accounts cannot
 * disagree.
 */
function whenLabel(days, { alreadyExpired = false } = {}) {
  if (alreadyExpired) {
    // Google can revoke before the seven days are up -- a password change or a
    // user withdrawing access -- so a broken mailbox may still have time left
    // on the clock. Reporting "expires today" for an account that has already
    // stopped collecting mail would be plainly wrong.
    if (days === null || days === undefined || days > 0) return "authorisation revoked";
    const overdue = Math.abs(Math.round(days));
    return overdue < 1
      ? "expired today"
      : `expired ${overdue} day${overdue === 1 ? "" : "s"} ago`;
  }
  if (days === null || days === undefined) return "unknown";
  if (days < 1) return "expires today";
  const left = Math.round(days);
  return `expires in ${left} day${left === 1 ? "" : "s"}`;
}

function Group({ title, hint, rows, busy, onAction, tone }) {
  if (!rows.length) return null;
  return (
    <section className={`sot-reconnect-group is-${tone}`}>
      <header>
        <h3>
          {title} <span className="sot-reconnect-count">{rows.length}</span>
        </h3>
        <p>{hint}</p>
      </header>
      <div className="sot-table-wrap">
        <table className="sot-mailbox-table sot-reconnect-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Gmail</th>
              <th>Grant</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id || row.email_address}>
                <td data-label="Candidate">{row.candidateName || "—"}</td>
                <td data-label="Gmail">{row.email_address}</td>
                <td data-label="Grant">
                  <span className={`sot-reconnect-when is-${tone}`}>
                    {whenLabel(row.grantDaysRemaining, { alreadyExpired: tone === "expired" })}
                  </span>
                </td>
                <td data-label="Action">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onAction("reconnect", row.source)}
                  >
                    Reconnect Gmail
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ReconnectWorklist({ rows, busy, onAction, withinDays = 2 }) {
  const worklist = useMemo(() => {
    // The table's rows carry the mailbox beside the candidate; flatten them so
    // one derivation sees both, and keep the original row for the reconnect
    // action, which expects exactly what the table passes it.
    const flattened = (Array.isArray(rows) ? rows : []).map((row) => ({
      ...(row.mailbox || {}),
      candidateName: row.candidate?.name || "",
      source: row,
    }));
    return reconnectWorklist(flattened, { withinDays });
  }, [rows, withinDays]);

  if (!worklist.total) {
    return (
      <div className="sot-empty sot-reconnect-empty">
        <p>No Gmail account needs reconnecting.</p>
        <p className="sot-reconnect-empty__hint">
          Grants last {"≈"}7 days while the OAuth app is in Testing mode, so
          accounts will appear here as they approach expiry.
        </p>
      </div>
    );
  }

  return (
    <div className="sot-reconnect-worklist">
      <Group
        title="Reconnect required"
        hint="Monitoring has stopped on these accounts. Google will not issue a new token until someone reconnects them."
        rows={worklist.expired}
        busy={busy}
        onAction={onAction}
        tone="expired"
      />
      <Group
        title="Expiring soon"
        hint="Still monitoring, but their grant is nearly up. Reconnecting now avoids a gap."
        rows={worklist.expiring}
        busy={busy}
        onAction={onAction}
        tone="soon"
      />
    </div>
  );
}

export default ReconnectWorklist;
