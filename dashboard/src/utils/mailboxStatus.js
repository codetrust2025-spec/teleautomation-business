/**
 * When a candidate mailbox needs the operator to reconnect Gmail.
 *
 * Extracted verbatim from RecruitmentMailPanelRedesign so the page badge and
 * the global fault alert cannot drift apart: one predicate, two callers. The
 * condition itself is unchanged.
 */
export function needsReconnect(mailbox) {
  const error = String(mailbox?.last_error_message || '').toLowerCase()
  return (
    mailbox?.connection_status === 'ERROR'
    || error.includes('expired')
    || error.includes('revoked')
  )
}

/**
 * The reconnect-required mailboxes, in the one shape both callers use.
 *
 * `/api/candidate-mailboxes/health` and `/api/candidate-mailboxes/overview`
 * return identical mailbox rows — overview is built from the health rows — so
 * selecting here keeps the page badge and the fault alert on one source of
 * truth rather than two hand-rolled projections.
 *
 * Neither payload carries a candidate name (see `mailbox_health_rows`, which
 * selects no name column), so `email` is the label that is always populated.
 * A name is still read when present so a richer payload needs no change here.
 */
export function reconnectRequiredMailboxes(mailboxes) {
  return (Array.isArray(mailboxes) ? mailboxes : [])
    .filter(needsReconnect)
    .map((mailbox) => ({
      id: mailbox?.id,
      candidateId: String(
        mailbox?.canonical_candidate_id || mailbox?.candidate_id || '',
      ),
      email: String(mailbox?.email_address || ''),
      name: String(mailbox?.candidate_name || mailbox?.name || ''),
    }))
}

/**
 * Describe reconnect-required mailboxes so the wording matches what is counted.
 *
 * The unit is the *mailbox*, because each expired mailbox needs its own
 * reconnect. One candidate holding several expired mailboxes is reported as
 * several accounts against one candidate, not as several candidates.
 *
 * This is never called with an empty list — the caller only alerts when at
 * least one mailbox is newly broken — so it can never render a zero count.
 */
export function describeReconnectTargets(rows) {
  const list = Array.isArray(rows) ? rows : []
  if (!list.length) return ''

  const label = (row) => String(row?.name || row?.email || '').trim()
  if (list.length === 1) return label(list[0]) || '1 Gmail account'

  const people = new Set(
    list
      .map((row) => String(row?.candidateId || '').trim() || label(row))
      .filter(Boolean),
  )
  if (people.size > 1) {
    return `${list.length} Gmail accounts across ${people.size} candidates`
  }
  const who = label(list[0])
  return who
    ? `${list.length} Gmail accounts for ${who}`
    : `${list.length} Gmail accounts`
}
