/**
 * The unread mail count, shared between the Mail Alerts page and the sidebar.
 *
 * The sidebar cannot poll for this on its own without lagging behind the page:
 * marking a mail read is a local action whose result the server only reports
 * on the next fetch. So the page publishes the count it already knows, and the
 * sidebar listens. A fetch on mount and on each mail socket event covers the
 * cases the page is not open for — a mail arriving, or another tab.
 */
import { useEffect, useState } from 'react'
import { subscribeMailEvents } from './mailEventStream.js'

const EVENT = 'teleautomation:mail-unread'
const API = import.meta.env?.VITE_API_BASE || ''

/** Announce a freshly known unread count to anything showing it. */
export function publishMailUnread(count) {
  const value = Math.max(0, Number(count) || 0)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { unread: value } }))
}

export async function fetchMailUnread() {
  const response = await fetch(`${API}/api/mail-monitoring/summary`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!response.ok) throw new Error('summary unavailable')
  const body = await response.json().catch(() => ({}))
  return Math.max(0, Number(body?.summary?.unread) || 0)
}

/**
 * Current unread count, 0 until something says otherwise.
 *
 * Never throws and never renders an error: a sidebar badge is not worth
 * breaking the shell over, so a failed read simply leaves the last known
 * count in place.
 */
export function useMailUnreadCount() {
  const [unread, setUnread] = useState(0)

  useEffect(() => {
    let live = true
    const refresh = () => {
      fetchMailUnread()
        .then(count => { if (live) setUnread(count) })
        .catch(() => { /* keep the last known count */ })
    }
    refresh()

    // The page publishes after every read/unread toggle, so the badge moves
    // with the click rather than on the next poll.
    const onPublished = event => {
      if (!live) return
      const value = Number(event?.detail?.unread)
      if (Number.isFinite(value)) setUnread(Math.max(0, value))
    }
    window.addEventListener(EVENT, onPublished)
    const unsubscribe = subscribeMailEvents(refresh)

    return () => {
      live = false
      window.removeEventListener(EVENT, onPublished)
      unsubscribe?.()
    }
  }, [])

  return unread
}
