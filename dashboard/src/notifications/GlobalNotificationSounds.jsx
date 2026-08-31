import { useEffect, useRef } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { reconnectRequiredMailboxes } from '../utils/mailboxStatus.js'
import { subscribeMailEvents, traceMailAlert } from './mailEventStream.js'
import { notifyGmailReconnect, notifyTrackedMail } from './notificationEvents.js'
import { useInterviewReminders } from './useInterviewReminders.js'
import { installSoundPreview } from './soundPreview.js'

const GMAIL_HEALTH_POLL_MS = 120000

export function GlobalNotificationSounds() {
  const { authenticated } = useAuth()
  const gmailEnabledRef = useRef(null)

  useEffect(() => { if (import.meta.env?.DEV) installSoundPreview() }, [])
  useInterviewReminders(authenticated)

  useEffect(() => {
    if (!authenticated) return undefined
    return subscribeMailEvents((payload, meta) => {
      if (!meta?.fromSocket) return
      if (payload?.event === 'notification_created' && payload?.delivery_source === 'replay') {
        traceMailAlert('sound_suppressed_replay', payload)
        return
      }
      if (notifyTrackedMail(payload)) traceMailAlert('sound', payload)
    })
  }, [authenticated])

  useEffect(() => {
    if (!authenticated) return undefined
    let cancelled = false
    const readMailboxes = async () => {
      if (gmailEnabledRef.current == null) {
        const config = await fetch(`${API}/api/ai-recruitment/config`, { credentials: 'include' }).then(r => r.ok ? r.json() : null).catch(() => null)
        gmailEnabledRef.current = Boolean(config?.enabled)
      }
      if (!gmailEnabledRef.current || cancelled) return
      const body = await fetch(`${API}/api/candidate-mailboxes/health?_=${Date.now()}`, { credentials: 'include' }).then(r => r.ok ? r.json() : null).catch(() => null)
      if (body && !cancelled) notifyGmailReconnect(reconnectRequiredMailboxes(body.mailboxes))
    }
    readMailboxes()
    const timer = window.setInterval(readMailboxes, GMAIL_HEALTH_POLL_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [authenticated])

  return null
}

export default GlobalNotificationSounds
