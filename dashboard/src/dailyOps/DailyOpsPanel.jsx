import React, { useCallback, useEffect, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { InterviewRoster } from './InterviewRoster.jsx'
import { PendingWorksStrip } from './PendingWorksStrip.jsx'
import { PRESETS, detectPresetFromRange, resolvePresetRange } from './dateRangePresets.js'

const ATTENDEES = ['Nikhila', 'Bhavana', 'Tool']
const ROUNDS = ['L1', 'L2', 'HR', 'Final', 'Screening']

function KpiCard({ label, value, tone = 'default', loading = false, active = false, onClick }) {
  return (
    <button
      type="button"
      className={`ops-dash-kpi ops-dash-kpi--${tone}${loading ? ' ops-dash-kpi--loading' : ''}${active ? ' ops-dash-kpi--active' : ''}`}
      onClick={onClick}
      aria-pressed={active}
    >
      <span className="ops-dash-kpi__label">{label}</span>
      <strong className="ops-dash-kpi__value">{loading ? '…' : value}</strong>
    </button>
  )
}

const PIE_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#a855f7', '#06b6d4', '#f43f5e', '#84cc16']
const candidateColor = (index, count) => count <= PIE_COLORS.length ? PIE_COLORS[index] : `hsl(${Math.round((index * 360) / count)} 78% 55%)`

function BookingPie({ overview = {}, selectedTechnology = '', onTechnologySelect }) {
  const [open, setOpen] = useState(false)
  const [hovered, setHovered] = useState(null)
  const [selected, setSelected] = useState(null)
  useEffect(() => {
    if (!open) return undefined
    const closeOnEscape = event => event.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [open])
  const total = Number(overview.total || 0)
  const rawCandidates = overview.by_candidate || []
  const candidates = rawCandidates
  let cursor = 0
  const stops = candidates.map((item, index) => {
    const start = cursor
    cursor += total ? (Number(item.count || 0) / total) * 100 : 0
    return `${candidateColor(index, candidates.length)} ${start}% ${cursor}%`
  })
  const background = total && stops.length ? `conic-gradient(${stops.join(', ')})` : 'conic-gradient(#273244 0 100%)'
  let segmentCursor = 0
  const segments = candidates.map((item, index) => {
    const percent = total ? (Number(item.count || 0) / total) * 100 : 0
    const segment = { ...item, percent, offset: segmentCursor, color: candidateColor(index, candidates.length) }
    segmentCursor += percent
    return segment
  })
  const activeCandidate = hovered || selected
  const displayedLevels = selected?.levels || overview.by_level || []
  const displayedTechnologies = selected?.technologies || overview.by_technology || []
  const selectCandidate = segment => setSelected(current => current?.name === segment.name ? null : segment)

  return (
    <><section className="ops-booking-pie" aria-label={`${total} interviews booked across all candidates`}>
      <div className="ops-booking-pie__chart" style={{ background }}>
        <span><strong>{total}</strong><small>booked</small></span>
      </div>
      <div className="ops-booking-pie__details">
        <strong className="ops-booking-pie__title">Bookings by candidate</strong>
        <div className="ops-booking-pie__legend">
          {candidates.map((item, index) => <span key={item.name}><i style={{ background: candidateColor(index, candidates.length) }} /><b>{item.name}</b><em>{item.count}</em></span>)}
        </div>
        <div className="ops-booking-pie__levels"><small>Levels</small>{(overview.by_level || []).map(item => <span key={item.name}><b>{item.name}</b>{item.count}</span>)}</div>
      </div>
      <button type="button" className="ops-booking-pie__open" onClick={() => setOpen(true)}>Open analytics</button>
    </section>
    {open && <div className="ops-booking-modal" role="presentation" onMouseDown={event => event.target === event.currentTarget && setOpen(false)}>
      <section className="ops-booking-modal__panel" role="dialog" aria-modal="true" aria-label="Interview booking analytics">
        <header><div><h2>Interview booking analytics</h2><p>Candidate distribution and interview levels for the selected period</p></div><button type="button" onClick={() => setOpen(false)} aria-label="Close analytics">&#10005;</button></header>
        <div className="ops-booking-modal__body">
          <div className="ops-booking-modal__donut-wrap">
            <svg className="ops-booking-modal__donut" viewBox="0 0 120 120" role="img" aria-label="Bookings by candidate">
              <circle cx="60" cy="60" r="46" pathLength="100" className="ops-booking-modal__track" />
              {segments.map(segment => <circle key={segment.name} cx="60" cy="60" r="46" pathLength="100" fill="none" stroke={segment.color} strokeWidth={activeCandidate?.name === segment.name ? 19 : 16} strokeDasharray={`${segment.percent} ${100 - segment.percent}`} strokeDashoffset={-segment.offset} transform="rotate(-90 60 60)" className="ops-booking-modal__segment" onMouseEnter={() => setHovered(segment)} onMouseLeave={() => setHovered(null)} onClick={() => selectCandidate(segment)}><title>{segment.name}: {segment.count} bookings ({segment.percent.toFixed(1)}%)</title></circle>)}
            </svg>
            <div className="ops-booking-modal__center">{activeCandidate ? <><strong>{activeCandidate.count}</strong><span>{activeCandidate.name}</span><small>{activeCandidate.percent.toFixed(1)}%</small></> : <><strong>{total}</strong><span>Total bookings</span><small>Click a candidate</small></>}</div>
          </div>
          <div className="ops-booking-modal__legend">{segments.map(segment => <button type="button" key={segment.name} onMouseEnter={() => setHovered(segment)} onMouseLeave={() => setHovered(null)} onClick={() => selectCandidate(segment)} className={activeCandidate?.name === segment.name ? 'is-active' : ''} aria-pressed={selected?.name === segment.name}><i style={{ background: segment.color }} /><span>{segment.name}</span><strong>{segment.count}</strong><em>{segment.percent.toFixed(1)}%</em></button>)}</div>
        </div>
        <footer className="ops-booking-modal__levels"><h3>Interview levels {selected ? `· ${selected.name}` : '· All candidates'}</h3><div>{displayedLevels.map(item => <span key={item.name}><b>{item.name}</b><strong>{item.count}</strong></span>)}</div></footer>
        <footer className="ops-booking-modal__levels ops-booking-modal__technologies"><h3>Tech stack {selected ? `· ${selected.name}` : '· All candidates'} <small>Click to filter the table</small></h3><div>{displayedTechnologies.map(item => <button type="button" key={item.name} className={selectedTechnology === item.name ? 'is-active' : ''} onClick={() => onTechnologySelect?.(selectedTechnology === item.name ? '' : item.name)}><b>{item.name}</b><strong>{item.count}</strong></button>)}</div></footer>
      </section>
    </div>}</>
  )
}

export function DailyOpsPanel({
  loggedInSlots = [],
  activeAccount,
  accountInfo = {},
  onSelectAccount,
  onStartAll,
  startAllBusy = false,
  showFleetControls = false,
  onNavCandidates,
}) {
  const { role, reference } = useAuth()
  // Daily Ops is shared across all authenticated operators.  Do not prefill
  // a handler's own name as an attendee filter or their roster looks empty
  // whenever another handler owns the booked slot.
  const handlerScoped = false

  const initialRange = resolvePresetRange('upcoming')
  const [fromDate, setFromDate] = useState(initialRange.from)
  const [toDate, setToDate] = useState(initialRange.to)
  const [rangePreset, setRangePreset] = useState('upcoming')
  const [attendeeFilter, setAttendeeFilter] = useState('')
  const [roundFilter, setRoundFilter] = useState('')
  const [technologyFilter, setTechnologyFilter] = useState('')
  const [candidateSearch, setCandidateSearch] = useState('')
  const [candidateFilter, setCandidateFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [globalStats, setGlobalStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [rosterCounts, setRosterCounts] = useState(null)

  function applyMonth(monthValue) {
    if (monthValue === 'all') {
      setFromDate('2000-01-01')
      setToDate('2100-12-31')
      setRangePreset('allTime')
      return
    }
    const [year, month] = monthValue.split('-').map(Number)
    const from = new Date(year, month - 1, 1, 12)
    const to = new Date(year, month, 0, 12)
    setFromDate(from.toISOString().slice(0, 10))
    setToDate(to.toISOString().slice(0, 10))
    setRangePreset(`month:${monthValue}`)
  }

  const upcomingOnly = rangePreset === 'upcoming'

  function applyPreset(presetId) {
    const range = resolvePresetRange(presetId)
    if (!range) return
    setRangePreset(presetId)
    setFromDate(range.from)
    setToDate(range.to)
  }

  function applyManualFrom(value) {
    setFromDate(value)
    setRangePreset(detectPresetFromRange(value, toDate))
  }

  function applyManualTo(value) {
    setToDate(value)
    setRangePreset(detectPresetFromRange(fromDate, value))
  }

  // When range is custom, disable upcoming_only filter to show all interviews in the range
  const effectiveUpcomingOnly = rangePreset === 'upcoming' ? upcomingOnly : false

  const loadGlobal = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ from: fromDate, to: toDate })
      if (attendeeFilter) params.set('attendee', attendeeFilter)
      if (roundFilter) params.set('round', roundFilter)
      if (technologyFilter) params.set('technology', technologyFilter)
      const search = candidateFilter || candidateSearch.trim()
      if (search) params.set('search', search)
      if (effectiveUpcomingOnly) params.set('upcoming_only', 'true')
      const res = await fetch(`${API}/candidates/interviews/global?${params}`, { credentials: 'include' })
      if (!(res.headers.get('content-type') || '').includes('application/json')) {
        throw new Error(`Global data ${res.status}`)
      }
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Failed to load global data')
      setGlobalStats(data)
      setError('')
    } catch (err) {
      setError(err.message || 'Failed to load dashboard')
    } finally {
      setLoading(false)
    }
  }, [fromDate, toDate, attendeeFilter, roundFilter, technologyFilter, candidateSearch, candidateFilter, effectiveUpcomingOnly])

  useEffect(() => { loadGlobal() }, [loadGlobal])

  const interviews = globalStats?.interviews || rosterCounts || {}
  const technologyOptions = (interviews.by_technology || []).map(item => item.name).sort()
  const candidateOptions = interviews.by_candidate || []
  const monthOptions = React.useMemo(() => {
    const options = [{ value: 'all', label: 'All time' }, ...(globalStats?.available_months || [])]
    const selected = rangePreset.startsWith('month:') ? rangePreset.slice(6) : ''
    if (selected && !options.some(option => option.value === selected)) options.push({ value: selected, label: selected })
    return options
  }, [globalStats?.available_months, rangePreset])
  const activeFilterCount = [attendeeFilter, roundFilter, technologyFilter, candidateSearch.trim(), candidateFilter].filter(Boolean).length

  function clearFilters() {
    setAttendeeFilter('')
    setRoundFilter('')
    setTechnologyFilter('')
    setCandidateSearch('')
    setCandidateFilter('')
  }

  return (
    <div className="daily-ops-page daily-ops-page--dashboard">

      {/* ── Compact top bar: title + KPIs + pending pill ─────────────── */}
      <div className="ops-topbar">
        <div className="ops-topbar__left">
          <h1 className="ops-dash-title">Daily ops</h1>
          <span className="ops-dash-sub ops-topbar__sub">Interview roster</span>
        </div>

        <div className="ops-topbar__kpis">
          <KpiCard label="Scheduled"    value={interviews.count             ?? 0} tone="blue"  loading={loading} active={statusFilter === ''} onClick={() => setStatusFilter('')} />
          <KpiCard label="Attended"     value={interviews.attended_count    ?? 0} tone="green" loading={loading} active={statusFilter === 'attended'} onClick={() => setStatusFilter(statusFilter === 'attended' ? '' : 'attended')} />
          <KpiCard label="Pending"      value={interviews.pending_count     ?? 0} tone="amber" loading={loading} active={statusFilter === 'pending'} onClick={() => setStatusFilter(statusFilter === 'pending' ? '' : 'pending')} />
          <KpiCard label="Not attended" value={interviews.not_attended_count ?? 0} tone="red"   loading={loading} active={statusFilter === 'not_attended'} onClick={() => setStatusFilter(statusFilter === 'not_attended' ? '' : 'not_attended')} />
        </div>

        <div className="ops-topbar__right">
          <BookingPie overview={globalStats?.booking_overview} selectedTechnology={technologyFilter} onTechnologySelect={setTechnologyFilter} />
          <PendingWorksStrip compact onOpenCandidates={onNavCandidates} />
        </div>
      </div>

      {/* ── Controls row: all filters in one line ───────────────────── */}
      <div className="ops-roster-controls" aria-label="Roster controls">
        <div className="ops-roster-controls__range">
        <div className="ops-roster-control-group ops-roster-control-group--period">
        <span className="ops-roster-control-group__label">Period</span>
        <div className="ops-date-range__presets" role="tablist" aria-label="Date range">
          {PRESETS.map(preset => (
            <button
              key={preset.id}
              type="button"
              role="tab"
              aria-selected={rangePreset === preset.id}
              className={`ops-date-range__preset${rangePreset === preset.id ? ' ops-date-range__preset--active' : ''}`}
              onClick={() => applyPreset(preset.id)}
            >
              {preset.label}
            </button>
          ))}
        </div>
        </div>

        <label className="ops-roster-control ops-roster-control--month"><span>Month</span><select className="cand-input ops-ctrl-select" value={rangePreset === 'allTime' ? 'all' : rangePreset.startsWith('month:') ? rangePreset.slice(6) : ''} onChange={e => applyMonth(e.target.value)} aria-label="Filter interviews by month"><option value="" disabled>Select month</option>{monthOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>

        <div className="ops-date-range__inputs ops-date-range__inputs--redesigned ops-date-range__inputs--removed" aria-hidden="true">
          <span className="ops-date-range__label">Range</span>
          <input className="cand-input ops-ctrl-date" type="date" value={fromDate} onChange={e => applyManualFrom(e.target.value)} aria-label="From date" />
          <span className="ops-date-range__sep">—</span>
          <input className="cand-input ops-ctrl-date" type="date" value={toDate}   onChange={e => applyManualTo(e.target.value)}   aria-label="To date" />
        </div>

        </div>

        <div className="ops-roster-controls__filters">
        <label className="ops-roster-search"><span className="ops-roster-search__icon" aria-hidden="true">&#8981;</span><input placeholder="Search candidate or phone..." value={candidateSearch} onChange={e => setCandidateSearch(e.target.value)} aria-label="Candidate search" /></label>
        <label className="ops-roster-control ops-roster-control--candidate"><span>Candidate</span><select className="cand-input ops-ctrl-select" value={candidateFilter} onChange={e => { setCandidateFilter(e.target.value); setCandidateSearch('') }} aria-label="Candidate filter"><option value="">All candidates</option>{candidateOptions.map(item => <option key={item.name} value={item.name}>{item.name} ({item.scheduled})</option>)}</select></label>

        {!handlerScoped && (
          <label className="ops-roster-control"><span>Attendee</span><select
            className="cand-input ops-ctrl-select"
            value={attendeeFilter}
            onChange={e => setAttendeeFilter(e.target.value)}
            aria-label="Attendee filter"
          >
            <option value="">Everyone</option>
            {ATTENDEES.map(name => <option key={name} value={name}>{name}</option>)}
          </select></label>
        )}
        <label className="ops-roster-control ops-roster-control--level"><span>Level</span><select
          className="cand-input ops-ctrl-select"
          value={roundFilter}
          onChange={e => setRoundFilter(e.target.value)}
          aria-label="Candidate interview level filter"
        >
          <option value="">All levels</option>
          {ROUNDS.map(r => <option key={r} value={r}>{r}</option>)}
        </select></label>
        <label className="ops-roster-control"><span>Profile</span><select
          className="cand-input ops-ctrl-select"
          value={technologyFilter}
          onChange={e => setTechnologyFilter(e.target.value)}
          aria-label="Technology filter"
        >
          <option value="">All profiles</option>
          {technologyOptions.map(t => <option key={t} value={t}>{t}</option>)}
        </select></label>
        <div className="ops-roster-controls__actions">
          {activeFilterCount > 0 && <button type="button" className="ops-roster-clear" onClick={clearFilters}>Clear <span>{activeFilterCount}</span></button>}
          <button type="button" className="ops-roster-refresh" onClick={loadGlobal} disabled={loading}><span aria-hidden="true">&#8635;</span>{loading ? 'Updating' : 'Refresh'}</button>
        </div>
        </div>
      </div>

      {error && <p className="admin-error ops-dash-error" role="alert">{error}</p>}

      {/* ── Table fills the rest ─────────────────────────────────────── */}
      <div className="ops-dashboard ops-dashboard--v3 ops-table-area">
        <InterviewRoster
          key={`${fromDate}|${toDate}|${upcomingOnly}`}
          variant="dashboard"
          dashboardFromDate={fromDate}
          dashboardToDate={toDate}
          dashboardAttendeeFilter={attendeeFilter}
          dashboardRoundFilter={roundFilter}
          dashboardTechnologyFilter={technologyFilter}
          dashboardCandidateSearch={candidateFilter || candidateSearch}
          dashboardStatusFilter={statusFilter}
          upcomingOnly={upcomingOnly}
          onRosterCountsChange={setRosterCounts}
          onRosterMutate={loadGlobal}
        />
      </div>
    </div>
  )
}
