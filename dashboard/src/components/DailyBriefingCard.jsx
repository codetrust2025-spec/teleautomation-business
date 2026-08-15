import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API } from '../config.js'
import './dailyBriefing.css'
import { useDialogA11y } from "../hooks/useDialogA11y.js";

const METRICS = [
  ['interviews_today', "Today's Interviews", 'normal'],
  ['pending_attendance', 'Pending Attendance', 'attention'],
  ['payments_due', 'Payments Due', 'important'],
  ['missing_resumes', 'Missing Resumes', 'attention'],
  ['followups_due', 'Follow-ups Due', 'attention'],
  ['stale_leads', 'Stale Leads', 'important'],
  ['important_cancellations', 'Cancellations', 'important'],
]

function timeLabel(value) {
  if (!value) return 'Not generated'
  try { return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }
  catch { return 'Updated' }
}

export function DailyBriefingCard() {
  const [briefing, setBriefing] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [expanded, setExpanded] = useState(false)
  const closeBriefing = useCallback(() => setExpanded(false), []);
  const briefingDialogRef = useDialogA11y(expanded, closeBriefing);
  const [selected, setSelected] = useState('interviews_today')
  const [speaking, setSpeaking] = useState(false)
  const utterance = useRef(null)

  const load = useCallback(async (refresh = false) => {
    refresh ? setRefreshing(true) : setLoading(true)
    setError(''); setNotice(refresh ? 'Updating briefing…' : '')
    try {
      const res = await fetch(`${API}/ai/daily-briefing${refresh ? '/refresh' : ''}`, {
        method: refresh ? 'POST' : 'GET', credentials: 'include',
      })
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Briefing request failed')
      setBriefing(data.briefing); setNotice(refresh ? 'Briefing updated' : 'Briefing is ready')
    } catch (e) {
      setError('Daily briefing is temporarily unavailable.')
      setNotice('Briefing generation failed')
    } finally { setLoading(false); setRefreshing(false) }
  }, [])

  useEffect(() => { load(false); return () => window.speechSynthesis?.cancel() }, [load])

  const spokenText = useMemo(() => briefing ? [
    'Good morning.', briefing.summary?.overview, briefing.summary?.attention,
    'Recommended actions.', briefing.summary?.recommended,
  ].filter(Boolean).join(' ') : '', [briefing])

  function listen() {
    if (!spokenText || !window.speechSynthesis) return
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(spokenText)
    utterance.current = u; u.lang = 'en-IN'; u.rate = 1
    u.onstart = () => setSpeaking(true)
    u.onend = u.onerror = () => { utterance.current = null; setSpeaking(false) }
    window.speechSynthesis.speak(u)
  }
  function pause() { if (window.speechSynthesis?.speaking) { window.speechSynthesis.pause(); setSpeaking(false) } }
  function stop() { window.speechSynthesis?.cancel(); utterance.current = null; setSpeaking(false) }

  if (loading && !briefing) return <section className="daily-briefing daily-briefing--loading" aria-live="polite"><div className="daily-briefing__skeleton"/><p>Preparing your daily briefing…</p></section>

  const metrics = briefing?.metrics || {}
  const records = briefing?.records || {}
  const activeRows = records[selected] || []
  const activeLabel = METRICS.find(([key]) => key === selected)?.[1] || 'Details'

  return <>
    <section className="daily-briefing" aria-labelledby="daily-briefing-title">
      <header className="daily-briefing__header">
        <div><h2 id="daily-briefing-title">Daily AI Briefing</h2><p>Your operational summary for today</p></div>
        <div className="daily-briefing__actions">
          <span>Updated at {timeLabel(briefing?.updated_at)}</span>
          <button onClick={()=>load(true)} disabled={refreshing} aria-label="Refresh daily briefing">{refreshing?'Updating…':'Refresh'}</button>
          <button onClick={speaking?pause:listen} disabled={!briefing} aria-label={speaking?'Pause briefing':'Listen to briefing'}>{speaking?'Pause':'Listen'}</button>
          <button onClick={()=>setExpanded(true)} disabled={!briefing} aria-label="View full daily briefing">Expand</button>
        </div>
      </header>
      {error ? <div className="daily-briefing__error" role="alert"><p>{error}</p><button onClick={()=>load(false)}>Retry</button>{briefing&&<span>Showing last successful briefing.</span>}</div> : briefing&&<>
        <div className="daily-briefing__copy">
          <p><strong>Operational Overview</strong>{briefing.summary?.overview}</p>
          <p><strong>Important Attention</strong>{briefing.summary?.attention}</p>
          <p><strong>Recommended Actions</strong>{briefing.summary?.recommended}</p>
        </div>
        <div className="daily-briefing__metrics" aria-label="Daily briefing metrics">
          {METRICS.map(([key,label,tone])=><button key={key} className={`daily-briefing__metric daily-briefing__metric--${tone}`} onClick={()=>{setSelected(key);setExpanded(true)}} aria-label={`${label}: ${metrics[key]||0}. View details`}><span>{label}</span><strong>{metrics[key]||0}</strong></button>)}
        </div>
      </>}
      <span className="daily-briefing__notice" aria-live="polite">{notice}</span>
    </section>
    {expanded&&briefing&&<div className="daily-briefing-modal" role="presentation">
      <button className="daily-briefing-modal__backdrop" onClick={()=>setExpanded(false)} aria-label="Close full briefing"/>
      <section ref={briefingDialogRef} className="daily-briefing-modal__panel" role="dialog" aria-modal="true" aria-labelledby="daily-briefing-modal-title">
        <header><div><h2 id="daily-briefing-modal-title">Daily AI Briefing</h2><p>{briefing.date} · {briefing.timezone}</p></div><button onClick={()=>setExpanded(false)} aria-label="Close">×</button></header>
        <div className="daily-briefing-modal__tabs">{METRICS.map(([key,label])=><button key={key} className={selected===key?'active':''} onClick={()=>setSelected(key)}>{label}<strong>{metrics[key]||0}</strong></button>)}</div>
        <div className="daily-briefing-modal__details"><h3>{activeLabel}</h3>{activeRows.length?<ul>{activeRows.map((row,i)=><li key={`${row.id}-${i}`}><strong>{row.name}</strong><span>{row.detail}</span></li>)}</ul>:<p>No records require attention in this section.</p>}</div>
        <footer><button onClick={listen}>Replay</button><button onClick={pause}>Pause</button><button onClick={stop}>Stop</button></footer>
      </section>
    </div>}
  </>
}
