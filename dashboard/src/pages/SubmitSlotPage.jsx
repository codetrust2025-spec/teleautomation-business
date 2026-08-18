import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Spinner } from '../Loader.jsx'
import { SubmitSlotFileDrop } from './SubmitSlotFileDrop.jsx'
import { bookingSourceMeta } from '../utils/bookingSource.js'

const API_BASE = typeof window !== 'undefined' && window.location.port === '3000'
  ? ''
  : (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.host}` : '')

function formatFriendlyDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(`${iso}T12:00:00`)
    return d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  } catch { return iso }
}

function formatFriendlyTime(hhmm) {
  if (!hhmm) return ''
  // Already in 12h format? (e.g., "02:00 PM")
  if (/\d{1,2}:\d{2}\s*(AM|PM|am|pm)/i.test(hhmm)) return hhmm
  const [h, m] = hhmm.split(':').map(Number)
  if (Number.isNaN(h)) return hhmm
  const d = new Date()
  d.setHours(h, m || 0, 0, 0)
  return d.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true })
}

/** Convert any time to 12-hour "hh:mm AM/PM" format */
function normalizeTo12h(val) {
  if (!val) return ''
  val = val.trim()
  // Already 12h? e.g., "02:00 PM", "2:30 pm"
  const m12 = val.match(/^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$/i)
  if (m12) { return `${m12[1].padStart(2,'0')}:${m12[2]} ${m12[3].toUpperCase()}` }
  // Short 12h: "2 PM"
  const ms = val.match(/^(\d{1,2})\s*(AM|PM|am|pm)$/i)
  if (ms) { return `${ms[1].padStart(2,'0')}:00 ${ms[2].toUpperCase()}` }
  // 24h: "14:00"
  const m24 = val.match(/^(\d{1,2}):(\d{2})$/)
  if (m24) {
    let h = parseInt(m24[1]), min = m24[2]
    if (h === 0) return `12:${min} AM`
    if (h < 12) return `${String(h).padStart(2,'0')}:${min} AM`
    if (h === 12) return `12:${min} PM`
    return `${String(h-12).padStart(2,'0')}:${min} PM`
  }
  return val
}

/** Convert 12h "02:00 PM" to 24h "14:00" for native inputs or submission */
function to24h(val) {
  if (!val) return ''
  val = val.trim()
  // Already 24h?
  if (/^\d{1,2}:\d{2}$/.test(val)) return val
  const m = val.match(/^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$/i)
  if (!m) return val
  let h = parseInt(m[1]), min = m[2], ap = m[3].toUpperCase()
  if (ap === 'AM' && h === 12) h = 0
  else if (ap === 'PM' && h !== 12) h += 12
  return `${String(h).padStart(2,'0')}:${min}`
}

function platformLabel(platform) {
  const map = { teams: 'Microsoft Teams', zoom: 'Zoom', gmail: 'Gmail', google_calendar: 'Google Calendar', barraiser: 'BarRaiser' }
  return map[platform] || platform || ''
}

/** Fix dates where AI/OCR returned wrong year (e.g. 2023 instead of 2026) */
function fixPastYear(dateStr) {
  if (!dateStr) return dateStr
  try {
    const d = new Date(dateStr + 'T00:00:00')
    const today = new Date(); today.setHours(0,0,0,0)
    const diffDays = (today - d) / (1000*60*60*24)
    if (diffDays > 7) {
      // Date is more than 7 days in the past — likely wrong year
      const corrected = new Date(today.getFullYear(), d.getMonth(), d.getDate())
      if ((today - corrected) / (1000*60*60*24) <= 7) return corrected.toISOString().slice(0,10)
      if (corrected > today) return corrected.toISOString().slice(0,10)
      // Still in past with current year, try next year
      const next = new Date(today.getFullYear() + 1, d.getMonth(), d.getDate())
      return next.toISOString().slice(0,10)
    }
    return dateStr
  } catch { return dateStr }
}

/** De-duplicate chip values case-insensitively, removing empties */
function uniqueNonEmptyTags(values) {
  const seen = new Set()
  return values.filter(Boolean).map(v => String(v).trim()).filter(v => {
    const key = v.toLowerCase()
    if (!v || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const ROUND_OPTIONS = ['Screening', 'L1', 'L2', 'Final', 'HR']

function candidateNameKey(value) {
  return String(value || '').trim().toLocaleLowerCase().replace(/[^a-z0-9]/g, '')
}

function formatDayHeader(iso) {
  if (!iso) return ''
  try {
    const d = new Date(`${iso}T12:00:00`)
    const today = new Date()
    const tomorrow = new Date(); tomorrow.setDate(today.getDate() + 1)
    const dateStr = d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })
    if (d.toDateString() === today.toDateString()) return `Today · ${dateStr}`
    if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow · ${dateStr}`
    return d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric' })
  } catch { return iso }
}

function groupSlotsByDate(slots) {
  const groups = new Map()
  for (const slot of slots) {
    const key = (slot.date || '').slice(0, 10)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(slot)
  }
  return [...groups.entries()].map(([date, items]) => ({ date, items }))
}

function dedupeCandidates(rows) {
  const byName = new Map()
  for (const row of rows || []) {
    const name = String(row?.name || '').trim()
    const key = candidateNameKey(name)
    if (!name || !key) continue
    const current = byName.get(key)
    if (!current || (current.name === current.name.toUpperCase() && name !== name.toUpperCase())) {
      byName.set(key, { ...row, name })
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }))
}

function SlotCandidatePicker({ candidates, value, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const options = useMemo(() => dedupeCandidates(candidates), [candidates])
  const query = value.trim().toLocaleLowerCase()
  const matches = useMemo(() => options.filter(c => c.name.toLocaleLowerCase().includes(query)), [options, query])
  useEffect(() => {
    function close(e) { if (!rootRef.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [])
  return (
    <div ref={rootRef} className="sbs-picker">
      <div className="sbs-picker__input-wrap">
        <svg className="sbs-picker__icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
        </svg>
        <input className="sbs-input sbs-name-input" value={value}
          onChange={e => { onChange(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          placeholder="Choose or type your name" disabled={disabled}
          autoComplete="name" aria-autocomplete="list" aria-expanded={open} aria-controls="sbs-candidate-options" />
        <button type="button" className="sbs-picker__toggle" onClick={() => setOpen(v => !v)} disabled={disabled} aria-label="Show names" aria-expanded={open}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M6 9l6 6 6-6"/></svg>
        </button>
      </div>
      {open && (
        <div id="sbs-candidate-options" className="sbs-picker__menu" role="listbox">
          {matches.length ? matches.map(c => (
            <button key={candidateNameKey(c.name)} type="button" role="option"
              aria-selected={c.name.toLocaleLowerCase() === query}
              className="sbs-picker__option"
              onClick={() => { onChange(c.name); setOpen(false) }}>{c.name}</button>
          )) : <p className="sbs-picker__empty">Type a new name to continue.</p>}
        </div>
      )}
    </div>
  )
}

/** Payment AI extraction result card — shown in the public slot booking form after proof upload */
function PaymentAiResultCard({ ai }) {
  if (!ai) return null
  const verified = ai.verified
  const amount = ai.amount ? `₹${Number(ai.amount).toLocaleString('en-IN')}` : null
  const utr = ai.utr_number || ai.reference_number || null
  const app = ai.payment_app || null
  const status = ai.status || 'unknown'
  const narrative = ai.narrative || ai.verification_result || null
  const confidence = ai.confidence_score || 0

  const borderColor = verified ? 'rgba(34,197,94,0.35)' : status === 'failed' ? 'rgba(239,68,68,0.35)' : 'rgba(251,191,36,0.35)'
  const bgColor = verified ? 'rgba(34,197,94,0.07)' : status === 'failed' ? 'rgba(239,68,68,0.06)' : 'rgba(251,191,36,0.06)'
  const icon = verified ? '✓' : status === 'failed' ? '✗' : '⚠'
  const iconColor = verified ? '#22c55e' : status === 'failed' ? '#ef4444' : '#fbbf24'

  return (
    <div style={{ marginTop: '10px', padding: '10px 12px', borderRadius: '8px', background: bgColor, border: `1px solid ${borderColor}`, fontSize: '12px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: narrative ? '6px' : 0 }}>
        <span style={{ fontWeight: 700, color: iconColor, fontSize: '14px' }}>{icon}</span>
        <span style={{ fontWeight: 600, color: 'rgba(226,232,240,0.9)' }}>
          {verified ? 'Payment verified' : status === 'failed' ? 'Payment failed' : 'Payment needs review'}
        </span>
        {confidence > 0 && (
          <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'rgba(148,163,184,0.7)' }}>
            {ai.detected_by || ai.primary_model || 'AI'} · {confidence}%
          </span>
        )}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', color: 'rgba(226,232,240,0.8)', marginBottom: narrative ? '6px' : 0 }}>
        {amount && <span>💰 {amount}</span>}
        {utr && <span>🔖 UTR {utr}</span>}
        {app && <span>📱 {app}</span>}
        {ai.payment_date && <span>📅 {ai.payment_date}</span>}
        {ai.sender_name && <span>👤 {ai.sender_name}</span>}
      </div>
      {narrative && (
        <p style={{ margin: 0, color: 'rgba(203,213,225,0.85)', fontStyle: 'italic', lineHeight: 1.45 }}>{narrative}</p>
      )}
    </div>
  )
}

export function SubmitSlotPage() {
  const [tab, setTab] = useState('book')
  const [candidates, setCandidates] = useState([])
  const [booked, setBooked] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [name, setName] = useState('')
  const [roundWisePhone, setRoundWisePhone] = useState('')
  const [parsedSlot, setParsedSlot] = useState(null)
  const [slotFile, setSlotFile] = useState(null)
  const [slotPreview, setSlotPreview] = useState('')
  // A fee paid in instalments produces one proof per transfer, so the booking
  // carries a list of proof ids rather than a single one.
  const [paymentProofIds, setPaymentProofIds] = useState([])
  const [paymentFiles, setPaymentFiles] = useState([])
  const [paymentTotals, setPaymentTotals] = useState(null)
  const [sessionFile, setSessionFile] = useState(null)
  const [sessionPreview, setSessionPreview] = useState('')
  const [manualDate, setManualDate] = useState('')
  const [manualTime, setManualTime] = useState('')
  const [interviewRound, setInterviewRound] = useState('')
  const [serviceType, setServiceType] = useState('profile_service')
  const [showServiceDrop, setShowServiceDrop] = useState(false)
  const [triedSubmit, setTriedSubmit] = useState(false)
  const [aiExtraction, setAiExtraction] = useState(null)
  const [aiBlocked, setAiBlocked] = useState('')
  const [userEditedFields, setUserEditedFields] = useState({})
  const [paymentAiResults, setPaymentAiResults] = useState([])
  const [paymentRejected, setPaymentRejected] = useState([])
  const [paymentAnalysing, setPaymentAnalysing] = useState(false)

  const effectiveName = name.trim()
  const selected = useMemo(() => {
    if (!effectiveName) return null
    const key = effectiveName.toLowerCase()
    return dedupeCandidates(candidates).find(c => c.name.toLowerCase() === key) || null
  }, [effectiveName, candidates])

  const bookingSlot = useMemo(() => {
    const effectiveDate = manualDate || parsedSlot?.date || ''
    const effectiveTime = manualTime || parsedSlot?.time || ''
    const effectiveEnd = parsedSlot?.time_end || ''
    if (effectiveDate && effectiveTime) return { ...parsedSlot, date: effectiveDate, time: to24h(effectiveTime), time_end: to24h(effectiveEnd), interview_round: interviewRound }
    return null
  }, [parsedSlot, manualDate, manualTime, interviewRound])

  const showManualSlotFields = Boolean(slotFile && !parsing && (!aiExtraction || aiExtraction.manual_fields_required || aiExtraction.confidence_score < 70))
  // Uploading a proof is no longer the same as having paid: instalments only
  // clear the fee once they add up, and the server decides when they do.
  const paymentComplete = Boolean(paymentProofIds.length && paymentTotals?.payment_complete)
  const needsPaymentProof = Boolean(selected?.needs_payment_proof && !paymentComplete)

  const resetPaymentProofs = useCallback(() => {
    setPaymentProofIds([])
    setPaymentFiles([])
    setPaymentTotals(null)
    setPaymentAiResults([])
    setPaymentRejected([])
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [cRes, bRes] = await Promise.all([
        fetch(`${API_BASE}/public/slots/candidates`, { cache: 'no-store' }),
        fetch(`${API_BASE}/public/slots/booked`, { cache: 'no-store' }),
      ])
      const cData = await cRes.json()
      const bData = await bRes.json()
      if (cData.status === 'ok') setCandidates(dedupeCandidates(cData.candidates || []))
      if (bData.status === 'ok') setBooked(bData.slots || [])
    } catch { setError('Could not load — check your connection.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => () => {
    if (slotPreview) URL.revokeObjectURL(slotPreview)
    if (sessionPreview) URL.revokeObjectURL(sessionPreview)
  }, [slotPreview, sessionPreview])

  async function parseScreenshot(file) {
    if (!file) { setParsedSlot(null); setAiExtraction(null); setAiBlocked(''); return }
    setParsing(true); setError(''); setSuccess(''); setAiExtraction(null); setAiBlocked('')
    try {
      // Try AI extraction first
      const fd = new FormData(); fd.append('file', file)
      const res = await fetch(`${API_BASE}/public/slots/extract-invite-ai`, { method: 'POST', body: fd })
      const data = await res.json()

      if (res.ok && data.status === 'ok' && data.data) {
        const ext = data.data
        setAiExtraction(ext)

        // Check if it's a payment screenshot
        if (ext.is_payment_screenshot) {
          setAiBlocked('This looks like a payment screenshot. Please upload the interview invite screenshot here.')
          setParsedSlot(null)
          setParsing(false)
          return
        }
        // Check if it doesn't look like an invite
        if (ext.looks_like_interview_invite === false) {
          setAiBlocked('This image does not look like an interview invite.')
          setParsedSlot(null)
          setParsing(false)
          return
        }

        // Auto-fill fields (only if user hasn't manually edited them)
        const slot = {}
        if (ext.interview_date && !userEditedFields.date) slot.date = ext.interview_date
        if ((ext.start_time || ext.time) && !userEditedFields.time) slot.time = normalizeTo12h(ext.start_time || ext.time)
        if ((ext.end_time || ext.time_end) && !userEditedFields.time_end) slot.time_end = normalizeTo12h(ext.end_time || ext.time_end)
        if (ext.meeting_platform) slot.platform = ext.meeting_platform
        if (ext.technology) slot.technology = ext.technology
        if (ext.interview_round && !interviewRound && !userEditedFields.round) {
          setInterviewRound(ext.interview_round)
          slot.interview_round = ext.interview_round
        }

        console.log('[Invite extraction]', { raw: ext, mapped: slot })
        setParsedSlot(slot)
        if (!userEditedFields.date) setManualDate(ext.interview_date || '')
        if (!userEditedFields.time) setManualTime(normalizeTo12h(ext.start_time || ext.time || ''))
        setParsing(false)
        return
      }
    } catch (e) {
      console.warn('AI extraction failed, falling back to OCR:', e)
    }

    // Fallback to existing OCR endpoint
    try {
      const fd2 = new FormData(); fd2.append('file', file)
      const res2 = await fetch(`${API_BASE}/public/slots/parse-screenshot`, { method: 'POST', body: fd2 })
      const data2 = await res2.json()
      if (!res2.ok) { setParsedSlot(null); setError('Auto-read failed — enter date & time manually.'); return }
      const slot = data2.slot || null
      // Normalize all times to 12h format and fix wrong year
      if (slot) {
        if (slot.date) slot.date = fixPastYear(slot.date)
        if (slot.time) slot.time = normalizeTo12h(slot.time)
        if (slot.time_end) slot.time_end = normalizeTo12h(slot.time_end)
      }
      setParsedSlot(slot)
      if (!interviewRound) setInterviewRound(slot?.interview_round || '')
      setManualDate(''); setManualTime('')
    } catch { setParsedSlot(null); setError('Network error while reading screenshot') }
    finally { setParsing(false) }
  }

  async function onSlotFileChange(file) {
    if (slotPreview) URL.revokeObjectURL(slotPreview)
    setSlotFile(file || null); setParsedSlot(null); setManualDate(''); setManualTime(''); setSuccess(''); setAiExtraction(null); setAiBlocked(''); setUserEditedFields({})
    if (file) { setSlotPreview(URL.createObjectURL(file)); await parseScreenshot(file) }
    else setSlotPreview('')
  }

  async function onSessionFileChange(file) {
    if (sessionPreview) URL.revokeObjectURL(sessionPreview)
    setSessionFile(file || null)
    if (file) setSessionPreview(URL.createObjectURL(file)); else setSessionPreview('')
  }

  async function uploadPaymentProof() {
    if (!effectiveName || !paymentFiles.length) { setError('Enter your name and attach at least one payment screenshot first.'); return }
    setBusy(true); setError(''); setSuccess(''); setPaymentRejected([]); setPaymentAnalysing(true)
    try {
      const fd = new FormData()
      fd.append('name', effectiveName)
      // Every screenshot goes in one request; ids already saved are sent back
      // so instalments uploaded across several attempts still add together.
      paymentFiles.forEach(f => fd.append('files', f))
      fd.append('existing_proof_ids', paymentProofIds.join(','))
      const res = await fetch(`${API_BASE}/public/slots/payment-proof`, { method: 'POST', body: fd })
      const data = await res.json()
      setPaymentRejected(data.rejected || [])
      if (!res.ok) { setError(data.message || 'Payment upload failed'); return }
      setPaymentProofIds(data.proof_ids || [])
      setPaymentTotals(data)
      setPaymentFiles([])
      // Every accepted screenshot keeps the AI reading the backend already ran.
      setPaymentAiResults([
        ...paymentAiResults,
        ...(data.ai_extractions || []).filter(ai => ai && ai.is_payment_screenshot),
      ])
      if (data.payment_complete) setSuccess('Payment proof saved — you can confirm your slot.')
    } catch { setError('Network error — try again') }
    finally { setBusy(false); setPaymentAnalysing(false) }
  }

  const effectiveBookingDate = manualDate || parsedSlot?.date || ''
  const isPastDate = (() => {
    if (!effectiveBookingDate) return false
    const today = new Date(); today.setHours(0,0,0,0)
    const d = new Date(effectiveBookingDate + 'T00:00:00'); d.setHours(0,0,0,0)
    return d < today
  })()

  async function submitBook(ev) {
    ev.preventDefault()
    if (parsing) {
      setError('Please wait until invite reading is complete.')
      return
    }
    if (!effectiveName || !slotFile || !interviewRound || needsPaymentProof
        || (serviceType === 'round_wise' && !roundWisePhone.trim())) {
      setTriedSubmit(true)
      setError('')
      return
    }
    if (isPastDate) {
      setError('Interview date is in the past. Please select today or a future date.')
      return
    }
    setBusy(true); setError(''); setSuccess('')
    try {
      const fd = new FormData()
      fd.append('name', effectiveName)
      fd.append('service_type', serviceType)
      if (bookingSlot?.date) fd.append('date', bookingSlot.date)
      if (bookingSlot?.time) fd.append('time', bookingSlot.time)
      if (bookingSlot?.time_end) fd.append('time_end', bookingSlot.time_end)
      if (bookingSlot?.interview_round) fd.append('interview_round', bookingSlot.interview_round)
      if (bookingSlot?.technology) fd.append('technology', bookingSlot.technology)
      if (serviceType === 'round_wise') fd.append('phone', roundWisePhone.trim())
      fd.append('candidate_id', selected?.id || '')
      if (paymentProofIds.length) fd.append('payment_proof_ids', paymentProofIds.join(','))
      // Confirmation must be idempotent: a retry or double submit has to resolve
      // to the same booking rather than creating a second one.
      fd.append(
        'idempotency_key',
        [
          effectiveName.trim().toLowerCase(),
          serviceType,
          roundWisePhone.trim(),
          bookingSlot?.date || '',
          bookingSlot?.time || '',
          bookingSlot?.time_end || '',
          bookingSlot?.interview_round || '',
          paymentProofIds.join(','),
        ].join('|'),
      )
      fd.append('file', slotFile)
      // /public/slots/book is retired and answers 410. /bookings/confirm is the
      // only public booking creation boundary.
      const res = await fetch(`${API_BASE}/bookings/confirm`, { method: 'POST', body: fd })
      const data = await res.json()
      if (!res.ok) { setError(data.payment_due ? (data.message || 'Payment required.') : (data.message || 'Could not book slot')); return }
      if (slotPreview) URL.revokeObjectURL(slotPreview)
      setSlotFile(null); setSlotPreview(''); setParsedSlot(null); setManualDate(''); setManualTime(''); setInterviewRound(''); setServiceType('profile_service'); resetPaymentProofs()
      setName(''); setRoundWisePhone('')
      setTriedSubmit(false)
      setSuccess(`Slot confirmed for ${data.candidate?.name || effectiveName}.`)
      // Refresh data first, then switch to confirmed tab after 2 seconds
      await refresh()
      setTimeout(() => { setTab('confirmed'); setSuccess('') }, 2000)
    } catch { setError('Network error — try again') }
    finally { setBusy(false) }
  }

  const TrustBadges = () => (
    <div className="sbs-trust">
      <div className="sbs-trust__item">
        <span className="sbs-trust__icon sbs-trust__icon--green"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></span>
        <div><div className="sbs-trust__title">Secure &amp; Private</div><div className="sbs-trust__sub">Your data is safe with us</div></div>
      </div>
      <div className="sbs-trust__item">
        <span className="sbs-trust__icon sbs-trust__icon--purple"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2" strokeLinecap="round"/></svg></span>
        <div><div className="sbs-trust__title">Smart Detection</div><div className="sbs-trust__sub">We read date &amp; time automatically</div></div>
      </div>
      <div className="sbs-trust__item">
        <span className="sbs-trust__icon sbs-trust__icon--blue"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 6L9 17l-5-5"/></svg></span>
        <div><div className="sbs-trust__title">Instant Confirmation</div><div className="sbs-trust__sub">Get confirmation as soon as you book</div></div>
      </div>
    </div>
  )

  return (
    <div className="sbs-screen">
      <div className="sbs-glow" aria-hidden="true" />
      <div className="sbs-card">
        <header className="sbs-header">
          <div className="sbs-header__text">
            <h1 className="sbs-header__title">Book Interview Slot</h1>
            <p className="sbs-header__sub">Pick the right slot, upload invite, and confirm.</p>
          </div>
          <div className="sbs-header__icon" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
              <rect x="3" y="4" width="18" height="18" rx="3"/>
              <path d="M16 2v4M8 2v4M3 10h18" strokeLinecap="round"/>
            </svg>
          </div>
        </header>

        <div className="sbs-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'book'}
            className={`sbs-tab${tab === 'book' ? ' sbs-tab--active' : ''}`}
            onClick={() => { setTab('book'); setError(''); setSuccess('') }}>
            Book slot
          </button>
          <button type="button" role="tab" aria-selected={tab === 'confirmed'}
            className={`sbs-tab${tab === 'confirmed' ? ' sbs-tab--active' : ''}`}
            onClick={() => { setTab('confirmed'); setError(''); setSuccess('') }}>
            Confirmed slots
          </button>
        </div>

        {loading ? (
          <div className="sbs-loading"><Spinner size={28} /></div>
        ) : tab === 'confirmed' ? (
          /* ── Confirmed slots tab ─────────────────────────── */
          <div className="sbs-body">
            <section className="sbs-section">
              <div className="sbs-step-head">
                <div>
                  <h2 className="sbs-step-title">Confirmed upcoming slots</h2>
                  <p className="sbs-step-sub">{booked.length > 0 ? `${booked.length} interview${booked.length !== 1 ? 's' : ''} scheduled` : 'No confirmed slots yet.'}</p>
                </div>
              </div>
              {booked.length === 0 ? (
                <div className="sbs-confirmed-empty">
                  <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.25" opacity="0.3">
                    <rect x="3" y="4" width="18" height="18" rx="3"/><path d="M16 2v4M8 2v4M3 10h18" strokeLinecap="round"/>
                  </svg>
                  <p>No confirmed slots yet. Book your first slot.</p>
                  <button type="button" className="sbs-cta sbs-cta--ready" style={{maxWidth:'200px'}}
                    onClick={() => { setTab('book'); setError(''); setSuccess('') }}>
                    Book a slot
                  </button>
                </div>
              ) : (
                <div className="sbs-slot-list">
                  {groupSlotsByDate(booked).map(({ date, items }) => (
                    <div key={date} className="sbs-date-group">
                      <div className="sbs-date-group__header">
                        <span className="sbs-date-group__label">{formatDayHeader(date)}</span>
                        <span className="sbs-date-group__count">{items.length} slot{items.length !== 1 ? 's' : ''}</span>
                      </div>
                      <div className="sbs-date-group__cards">
                        {items.map((slot, i) => (
                          <div key={i} className="sbs-confirmed-card">
                            <div className="sbs-slot-card__icon sbs-slot-card__icon--active">
                              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                                <rect x="3" y="4" width="18" height="18" rx="3"/>
                                <path d="M16 2v4M8 2v4M3 10h18" strokeLinecap="round"/>
                              </svg>
                            </div>
                            <div className="sbs-slot-card__body">
                              <div className="sbs-slot-card__name">{slot.name}</div>
                              <div className="sbs-slot-card__time">
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2" strokeLinecap="round"/></svg>
                                <span>{formatFriendlyTime(slot.time)}{slot.time_end ? ` – ${formatFriendlyTime(slot.time_end)}` : ''}</span>
                              </div>
                            </div>
                            <div className="sbs-confirmed-card__right">
                              {slot.interview_round && <span className={`sbs-slot-card__round sbs-slot-card__round--${(slot.interview_round || '').toLowerCase().replace(/\s+/g, '')}`}>{slot.interview_round}</span>}
                              <span className="sbs-confirmed-card__status">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
                                Booked
                              </span>
                              {(() => {
                                const meta = bookingSourceMeta(slot.interview_booking_source)
                                return (
                                  <span
                                    className={`sbs-source-badge sbs-source-badge--${meta.tone}`}
                                    title={meta.title}
                                  >
                                    {meta.label}
                                  </span>
                                )
                              })()}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
            <TrustBadges />
          </div>
        ) : (
          /* ── Book slot tab — direct booking form only ─────── */
          <div className="sbs-body">
            <form className="sbs-form" onSubmit={submitBook}>
              <div className="sbs-field">
                <span className="sbs-label">Service type</span>
                <div className="sbs-select-wrap sbs-select-wrap--custom">
                  <button type="button" className="sbs-select sbs-select--custom" onClick={() => setShowServiceDrop(v => !v)} disabled={busy || parsing}>
                    <span>{serviceType === "round_wise" ? "Round-wise" : "Profile service"}</span>
                    <svg className="sbs-select__arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6"/></svg>
                  </button>
                  {showServiceDrop && (
                    <ul className="sbs-dropdown">
                      <li className={`sbs-dropdown__item${serviceType === "round_wise" ? " sbs-dropdown__item--active" : ""}`} onMouseDown={e => e.preventDefault()} onClick={e => { e.stopPropagation(); setServiceType("round_wise"); setShowServiceDrop(false); setName(""); resetPaymentProofs(); }}>Round-wise</li>
                      <li className={`sbs-dropdown__item${serviceType === "profile_service" ? " sbs-dropdown__item--active" : ""}`} onMouseDown={e => e.preventDefault()} onClick={e => { e.stopPropagation(); setServiceType("profile_service"); setShowServiceDrop(false); setName(""); resetPaymentProofs(); }}>Profile service</li>
                    </ul>
                  )}
                </div>
              </div>

              <label className="sbs-field">
                <span className="sbs-label">Client name</span>
                {serviceType === "round_wise" ? (
                  <input className="sbs-input" type="text" value={name} onChange={e => { setName(e.target.value); resetPaymentProofs(); }} placeholder="Type client name" disabled={busy || parsing} />
                ) : (
                  <SlotCandidatePicker candidates={candidates} value={name} onChange={v => { setName(v); resetPaymentProofs() }} disabled={busy || parsing} />
                )}
                {triedSubmit && !effectiveName
                  ? <span className="sbs-hint sbs-hint--warn">Enter client name to confirm.</span>
                  : <span className="sbs-hint">{serviceType === "round_wise" ? "Type the client name for this round." : "Pick from the list or type a new client name."}</span>}
              </label>

              {serviceType === "round_wise" && (
                // /bookings/confirm rejects a round-wise booking without a valid
                // phone identity, so it has to be collected here.
                <label className="sbs-field">
                  <span className="sbs-label">Candidate phone <span className="sbs-required" aria-hidden="true">*</span></span>
                  <input
                    className="sbs-input"
                    type="tel"
                    inputMode="tel"
                    value={roundWisePhone}
                    onChange={e => setRoundWisePhone(e.target.value)}
                    placeholder="10-digit phone number"
                    disabled={busy || parsing}
                  />
                  {triedSubmit && !roundWisePhone.trim()
                    ? <span className="sbs-hint sbs-hint--warn">Required — round-wise booking needs the candidate phone.</span>
                    : <span className="sbs-hint">Identifies the candidate across rounds.</span>}
                </label>
              )}

              <label className="sbs-field">
                <span className="sbs-label">Interview round <span className="sbs-required" aria-hidden="true">*</span></span>
                <div className={`sbs-select-wrap${triedSubmit && !interviewRound ? ' sbs-select-wrap--required' : ''}`}>
                  <select className="sbs-select" value={interviewRound} onChange={e => setInterviewRound(e.target.value)} disabled={busy || parsing} required>
                    <option value="">Select round (L1, L2…)</option>
                    {ROUND_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
                {triedSubmit && !interviewRound && <span className="sbs-hint sbs-hint--warn">Required — select a round to confirm.</span>}
              </label>

              {selected?.needs_payment_proof && (
                <div className="sbs-pay-card">
                  <div className="sbs-pay-head"><span>Payment due</span><strong>₹{(selected.balance_due || 0).toLocaleString('en-IN')}</strong></div>
                  {paymentProofIds.length > 0 && (
                    <>
                      <p className={paymentComplete ? 'sbs-pay-ok' : 'sbs-pay-partial'}>
                        {paymentComplete
                          ? `Payment proof on file ✓ · ₹${(paymentTotals?.verified_total || 0).toLocaleString('en-IN')} across ${paymentProofIds.length} screenshot${paymentProofIds.length === 1 ? '' : 's'}`
                          : `₹${(paymentTotals?.verified_total || 0).toLocaleString('en-IN')} verified so far · ₹${(paymentTotals?.remaining_due || 0).toLocaleString('en-IN')} still to upload`}
                      </p>
                      {paymentAiResults.map((ai, index) => (
                        <PaymentAiResultCard key={ai.utr_number || ai.transaction_id || index} ai={ai} />
                      ))}
                    </>
                  )}
                  {paymentRejected.map((item, index) => (
                    <span className="sbs-hint sbs-hint--warn" key={`${item.filename}-${index}`}>
                      {item.filename}: {item.message}
                    </span>
                  ))}
                  {!paymentComplete && (
                    <>
                      {/* Split payments are normal here — one screenshot per
                          transfer, and the AI totals them for this booking. */}
                      <SubmitSlotFileDrop
                        compact
                        multiple
                        label={paymentProofIds.length ? 'Add remaining payment screenshots' : 'Payment screenshots'}
                        hint="Paid in parts? Attach every payment screenshot — they are added up for this booking."
                        files={paymentFiles}
                        disabled={busy || parsing}
                        busy={busy || paymentAnalysing}
                        onFiles={next => { setPaymentFiles(next); setPaymentRejected([]) }}
                      />
                      <button type="button" className="sbs-secondary-btn" disabled={busy || parsing || paymentAnalysing || !paymentFiles.length} onClick={uploadPaymentProof}>
                        {paymentAnalysing
                          ? <><Spinner size={14} />&nbsp;Analysing {paymentFiles.length > 1 ? `${paymentFiles.length} screenshots` : ''}…</>
                          : `Save payment proof${paymentFiles.length > 1 ? 's' : ''}`}
                      </button>
                      {triedSubmit && needsPaymentProof && <span className="sbs-hint sbs-hint--warn">Upload and save payment proof to confirm.</span>}
                    </>
                  )}
                </div>
              )}

              <div className="sbs-field">
                <span className="sbs-label">Interview invite screenshot</span>
                <SubmitSlotFileDrop hint="Teams, Gmail, Calendar, or Zoom — date and time must be visible." file={slotFile} previewUrl={slotPreview} disabled={busy} busy={parsing} onFile={onSlotFileChange} />
                {triedSubmit && !slotFile && <span className="sbs-hint sbs-hint--warn">Upload your interview invite screenshot to confirm.</span>}
              </div>

              {parsing && <div className="sbs-status sbs-status--loading"><Spinner size={18} /><span>Reading invite with AI… this may take a few minutes</span></div>}

              {aiBlocked && <div className="sbs-alert sbs-alert--error" role="alert">{aiBlocked}</div>}

              {aiExtraction && !aiBlocked && aiExtraction.confidence_score > 0 && (
                <div className="sbs-detected">
                  <span className={`sbs-detected__badge ${aiExtraction.confidence_score >= 90 ? 'sbs-detected__badge--green' : aiExtraction.confidence_score >= 70 ? 'sbs-detected__badge--yellow' : 'sbs-detected__badge--red'}`}>
                    {aiExtraction.detected_by
                      ? `Detected by ${aiExtraction.detected_by} · ${aiExtraction.confidence_score}%`
                      : `AI · ${aiExtraction.confidence_score}%`}
                  </span>
                  <div className="sbs-detected__main">
                    {aiExtraction.interview_date && <span className="sbs-detected__date">{formatFriendlyDate(aiExtraction.interview_date)}</span>}
                    {aiExtraction.start_time && <span className="sbs-detected__time">{aiExtraction.start_time}{aiExtraction.end_time ? ` – ${aiExtraction.end_time}` : ''}</span>}
                  </div>
                  <div className="sbs-detected__chips">
                    {uniqueNonEmptyTags([
                      aiExtraction.interview_round,
                      aiExtraction.technology,
                      aiExtraction.meeting_platform ? platformLabel(aiExtraction.meeting_platform) : '',
                      aiExtraction.screenshot_source,
                    ]).map((tag, i) => (
                      <span key={i} className={`sbs-chip${i > 0 ? ' sbs-chip--muted' : ''}`}>{tag}</span>
                    ))}
                  </div>
                  {aiExtraction.warnings && aiExtraction.warnings.length > 0 && (
                    <div className="sbs-detected__warnings">{aiExtraction.warnings.map((w, i) => <span key={i} className="sbs-hint sbs-hint--warn">{w}</span>)}</div>
                  )}
                </div>
              )}

              {!aiExtraction && parsedSlot?.date && parsedSlot?.time && (
                <div className="sbs-detected">
                  <span className="sbs-detected__badge">Detected</span>
                  <div className="sbs-detected__main">
                    <span className="sbs-detected__date">{formatFriendlyDate(parsedSlot.date)}</span>
                    <span className="sbs-detected__time">{formatFriendlyTime(parsedSlot.time)}{parsedSlot.time_end ? ` – ${formatFriendlyTime(parsedSlot.time_end)}` : ''}</span>
                  </div>
                  <div className="sbs-detected__chips">
                    {parsedSlot.interview_round && <span className="sbs-chip">{parsedSlot.interview_round}</span>}
                    {parsedSlot.technology && <span className="sbs-chip sbs-chip--muted">{parsedSlot.technology}</span>}
                    {parsedSlot.platform && <span className="sbs-chip sbs-chip--muted">{platformLabel(parsedSlot.platform)}</span>}
                  </div>
                </div>
              )}

              {showManualSlotFields && (
                <div className="sbs-manual">
                  <p className="sbs-manual__hint">{parsedSlot?.date ? 'Verify detected date & time — correct below if wrong.' : 'Include the date line in your screenshot or enter manually.'}</p>
                  <div className="sbs-manual__grid">
                    <label className="sbs-field"><span className="sbs-label">Interview date</span><input className="sbs-input" type="date" value={manualDate || parsedSlot?.date || ''} onChange={e => { setManualDate(e.target.value); setUserEditedFields(f => ({...f, date: true})); }} disabled={busy || parsing} /></label>
                    <label className="sbs-field"><span className="sbs-label">Start time</span><input className="sbs-input" type="text" placeholder="e.g. 02:00 PM" value={normalizeTo12h(manualTime || parsedSlot?.time || '')} onChange={e => { setManualTime(e.target.value); setUserEditedFields(f => ({...f, time: true})); }} disabled={busy || parsing} /></label>
                  </div>
                  {isPastDate && <span className="sbs-hint sbs-hint--warn">Interview date is in the past. Please select today or a future date.</span>}
                </div>
              )}

              {error && <p className="sbs-alert sbs-alert--error" role="alert">{error}</p>}
              {success && <div className="sbs-alert sbs-alert--success sbs-success-anim"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{flexShrink:0}}><path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round"/></svg><span>{success}</span></div>}

              <button type="submit" className="sbs-cta sbs-cta--ready" disabled={busy || parsing || isPastDate || !!aiBlocked}>
                {busy ? <Spinner size={18} /> : parsing ? 'Reading invite...' : 'Confirm booking'}
              </button>
            </form>
            <TrustBadges />
          </div>
        )}
      </div>
      <p className="sbs-foot">TeleAutomation · secure slot booking</p>
    </div>
  )
}
