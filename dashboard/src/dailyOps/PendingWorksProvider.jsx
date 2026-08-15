import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'

const PendingWorksContext = createContext(null)

function currentMonthKey() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function usePendingWorksQuery({ month = 'all', enabled = true } = {}) {
  const [works, setWorks] = useState([])
  const [count, setCount] = useState(0)
  const [candidateCount, setCandidateCount] = useState(0)
  const [byKind, setByKind] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const reload = useCallback(async ({ silent = false } = {}) => {
    if (!enabled) {
      setWorks([])
      setCount(0)
      setCandidateCount(0)
      setByKind({})
      setLoading(false)
      return
    }
    if (!silent) setLoading(true)
    try {
      const params = new URLSearchParams()
      if (month && month !== 'all') params.set('month', month)
      params.set('_ts', String(Date.now()))
      const res = await fetch(`${API}/candidates/pending-works?${params}`, {
        credentials: 'include',
        cache: 'no-store',
      })
      if (!(res.headers.get('content-type') || '').includes('application/json')) {
        throw new Error(`Server returned ${res.status}`)
      }
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Failed to load pending works')
      setWorks(data.works || [])
      setCount(data.count || 0)
      setCandidateCount(data.candidate_count || 0)
      setByKind(data.by_kind || {})
      setError('')
    } catch (err) {
      if (!silent) setError(err.message || 'Failed to load')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [enabled, month])

  useEffect(() => {
    // Clear stale chips when disabled and refresh immediately when returning
    // from candidate editing to the dashboard.
    reload()
  }, [reload])

  useEffect(() => {
    if (!enabled) return undefined
    const t = setInterval(() => reload({ silent: true }), 120000)
    return () => clearInterval(t)
  }, [enabled, reload])

  return { works, count, candidateCount, byKind, loading, error, reload }
}

function usePendingInterviewsQuery({ enabled = true, deferMs = 5000, days = 7 } = {}) {
  const [pendingCount, setPendingCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const reload = useCallback(async ({ silent = false } = {}) => {
    if (!enabled) {
      setPendingCount(0)
      setLoading(false)
      return
    }
    if (!silent) setLoading(true)
    try {
      const params = new URLSearchParams({ days: String(days), include_today: 'true' })
      const res = await fetch(`${API}/candidates/interviews/upcoming?${params}`, { credentials: 'include' })
      if (!(res.headers.get('content-type') || '').includes('application/json')) {
        throw new Error(`Server returned ${res.status}`)
      }
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Failed to load pending interviews')
      setPendingCount(data.scheduled_count || data.pending_count || 0)
      setError('')
    } catch (err) {
      if (!silent) setError(err.message || 'Failed to load')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [enabled, days])

  useEffect(() => {
    if (!enabled) return undefined
    const t = setTimeout(() => reload(), deferMs)
    return () => clearTimeout(t)
  }, [enabled, reload, deferMs])

  useEffect(() => {
    if (!enabled) return undefined
    const t = setInterval(() => reload({ silent: true }), 120000)
    return () => clearInterval(t)
  }, [enabled, reload])

  return { pendingCount, loading, error, reload }
}

export function PendingWorksProvider({ children, mainView = 'dashboard' }) {
  const { enabled: authEnabled, authenticated, loading: authLoading } = useAuth()
  const deferCandidates = mainView === 'candidates'
  const authReady = !authEnabled || (authenticated && !authLoading)

  // Interview reminders moved to GlobalNotificationSounds, alongside every
  // other notification sound, so this provider is only about pending work.

  const pendingWorks = usePendingWorksQuery({
    enabled: authReady && !deferCandidates,
    month: 'all',
  })
  const pendingInterviews = usePendingInterviewsQuery({
    enabled: authReady,
    deferMs: 8000,
    days: 1,
  })

  const value = {
    ...pendingWorks,
    pendingInterviewCount: pendingInterviews.pendingCount,
    reloadPendingInterviews: pendingInterviews.reload,
  }

  return (
    <PendingWorksContext.Provider value={value}>
      {children}
    </PendingWorksContext.Provider>
  )
}

export function usePendingWorksContext() {
  const ctx = useContext(PendingWorksContext)
  if (!ctx) throw new Error('usePendingWorksContext must be used within PendingWorksProvider')
  return ctx
}

export function usePendingWorksContextOptional() {
  return useContext(PendingWorksContext)
}

const OPEN_INTENT_KEY = 'cand-open-pending'
const FILTER_KEY = 'cand-works-pending'

export function stashPendingWorkOpenIntent(work) {
  if (!work?.candidate_id && !work?.candidate_name) return
  try {
    sessionStorage.setItem(OPEN_INTENT_KEY, JSON.stringify({
      candidate_id: work.candidate_id || '',
      candidate_name: work.candidate_name || '',
      kind: work.kind || work.label || '',
    }))
  } catch {}
}

export function markCandidatesPendingWorksFilter() {
  try {
    sessionStorage.setItem(FILTER_KEY, '1')
  } catch {}
}

export function consumePendingWorkOpenIntent() {
  try {
    const raw = sessionStorage.getItem(OPEN_INTENT_KEY)
    if (!raw) return null
    sessionStorage.removeItem(OPEN_INTENT_KEY)
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export function navigatePendingWorkToCandidates(work, { onNavCandidates } = {}) {
  stashPendingWorkOpenIntent(work)
  markCandidatesPendingWorksFilter()
  onNavCandidates?.()
}

export { currentMonthKey }
