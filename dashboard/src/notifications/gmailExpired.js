/**
 * How many Gmail accounts need reconnecting, shared by the page and the sidebar.
 *
 * Deliberately built the same way as the unread-mail badge next to it: the page
 * publishes the count it already has, and anything showing the number listens.
 * A reconnect or a disconnect is a local action whose result the server only
 * reports on the next poll, so without the publish the badge would lag behind
 * the row the operator just fixed.
 *
 * The count is never computed here. `reconnectRequiredMailboxes` is the same
 * selector the mailbox rows and the desktop fault alert already use, so the
 * badge cannot drift away from the rows it is counting -- which is the whole
 * reason that selector was extracted in the first place.
 */
import { useEffect, useState } from 'react'
import { reconnectRequiredMailboxes } from '../utils/mailboxStatus.js'

const EVENT = 'teleautomation:gmail-expired'
const API = import.meta.env?.VITE_API_BASE || ''

/** How often to re-read when nothing has published a count. */
export const GMAIL_EXPIRED_POLL_MS = 60000

/** Announce a freshly known reconnect count to anything showing it. */
export function publishGmailExpired(count) {
  const value = Math.max(0, Number(count) || 0)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { expired: value } }))
}

export async function fetchGmailExpiredCount() {
  // The cache-buster matches the existing health poll: this endpoint is read
  // straight after an operator reconnects, and a cached body would show the
  // account still broken.
  const response = await fetch(
    `${API}/api/candidate-mailboxes/health?_=${Date.now()}`,
    { credentials: 'include', headers: { 'Content-Type': 'application/json' } },
  )
  if (!response.ok) throw new Error('mailbox health unavailable')
  const body = await response.json().catch(() => ({}))
  return reconnectRequiredMailboxes(body?.mailboxes).length
}

/**
 * Current reconnect-required count, 0 until something says otherwise.
 *
 * Never throws and never renders an error: a sidebar badge is not worth
 * breaking the shell over, so a failed read leaves the last known count.
 */
export function useGmailExpiredCount() {
  const [expired, setExpired] = useState(0)

  useEffect(() => {
    let live = true
    const refresh = () => {
      fetchGmailExpiredCount()
        .then(count => { if (live) setExpired(count) })
        .catch(() => { /* keep the last known count */ })
    }
    refresh()

    // The mailbox page publishes after every load, so reconnecting an account
    // moves the badge with the row rather than on the next poll.
    const onPublished = event => {
      if (!live) return
      const value = Number(event?.detail?.expired)
      if (Number.isFinite(value)) setExpired(Math.max(0, value))
    }
    window.addEventListener(EVENT, onPublished)
    const timer = window.setInterval(refresh, GMAIL_EXPIRED_POLL_MS)

    return () => {
      live = false
      window.removeEventListener(EVENT, onPublished)
      window.clearInterval(timer)
    }
  }, [])

  return expired
}
