import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  canonicalTechnology,
  fetchActiveRoster,
  triggerRosterDownload,
} from './candidatesRosterUtils.js'

function formatStage(stage) {
  if (!stage) return '—'
  return String(stage).replace(/_/g, ' ')
}

function rowMonth(row) {
  const raw = String(row?.date || '').trim()
  if (raw.length >= 7 && raw[4] === '-') return raw.slice(0, 7)
  return ''
}

function filterByMonth(rows, month) {
  if (!month || month === 'all') return rows
  if (month === 'undated') return rows.filter((row) => !rowMonth(row))
  return rows.filter((row) => rowMonth(row) === month)
}

function groupByTech(rows) {
  const groups = {}
  for (const row of rows) {
    const tech = canonicalTechnology(row.technology)
    if (!groups[tech]) groups[tech] = []
    groups[tech].push(row)
  }
  return groups
}

export function CandidatesActiveRoster({
  open,
  onClose,
  reference = 'all',
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [roster, setRoster] = useState(null)
  const [techFilter, setTechFilter] = useState('all')
  const [month, setMonth] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchActiveRoster({ month: 'all', reference })
      setRoster(data)
      setTechFilter('all')
      setMonth('all')
    } catch (e) {
      setError(String(e.message || e))
      setRoster(null)
    } finally {
      setLoading(false)
    }
  }, [reference])

  useEffect(() => {
    if (!open) return
    load()
  }, [open, load])

  useEffect(() => {
    if (!open) return
    function onKey(e) {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const allRows = roster?.candidates || []

  const monthOptions = useMemo(() => {
    const months = new Set()
    let undated = 0
    for (const row of allRows) {
      const m = rowMonth(row)
      if (m) months.add(m)
      else undated += 1
    }
    const sorted = [...months].sort().reverse()
    const opts = [{ value: 'all', label: `All months (${allRows.length})` }]
    for (const m of sorted) {
      const count = allRows.filter((row) => rowMonth(row) === m).length
      opts.push({ value: m, label: `${m} (${count})` })
    }
    if (undated > 0) {
      opts.push({ value: 'undated', label: `No date (${undated})` })
    }
    return opts
  }, [allRows])

  const monthFiltered = useMemo(
    () => filterByMonth(allRows, month),
    [allRows, month],
  )

  const techGroups = useMemo(
    () => groupByTech(monthFiltered),
    [monthFiltered],
  )

  const techOptions = useMemo(() => {
    const entries = Object.entries(techGroups).sort(
      (a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]),
    )
    return [
      { value: 'all', label: `All technologies (${monthFiltered.length})` },
      ...entries.map(([tech, items]) => ({ value: tech, label: `${tech} (${items.length})` })),
    ]
  }, [techGroups, monthFiltered.length])

  const visibleRows = useMemo(() => {
    if (techFilter === 'all') return monthFiltered
    return techGroups[techFilter] || []
  }, [monthFiltered, techFilter, techGroups])

  if (!open) return null

  const scopeParts = []
  if (reference && reference !== 'all') scopeParts.push(reference)

  return (
    <div
      className="cand-roster-backdrop"
      onClick={e => e.target === e.currentTarget && onClose?.()}
      role="presentation"
    >
      <div
        className="cand-roster-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cand-roster-title"
        onClick={e => e.stopPropagation()}
      >
        <header className="cand-roster-header">
          <div>
            <h3 id="cand-roster-title" className="cand-roster-title">Active candidates</h3>
            <p className="cand-roster-sub">
              All in-progress profiles ({allRows.length} total) — independent of the main page month filter
              {scopeParts.length > 0 ? ` · ${scopeParts.join(' · ')}` : ''}
            </p>
          </div>
          <div className="cand-roster-header-actions">
            <button
              type="button"
              className="cand-btn cand-btn--ghost cand-btn--sm"
              onClick={load}
              disabled={loading}
            >
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
            <button
              type="button"
              className="cand-btn cand-btn--primary cand-btn--sm"
              onClick={() => triggerRosterDownload({ month, reference, rows: visibleRows })}
              disabled={loading || visibleRows.length === 0}
            >
              Download CSV
            </button>
            <button
              type="button"
              className="cand-roster-close"
              onClick={onClose}
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </header>

        {error && <p className="cand-roster-error" role="alert">{error}</p>}

        {roster && !error && (
          <div className="cand-roster-tech-bar" role="region" aria-label="Technology summary">
            {Object.entries(techGroups)
              .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
              .map(([tech, items]) => (
                <button
                  key={tech}
                  type="button"
                  className={`cand-roster-tech-chip${techFilter === tech ? ' cand-roster-tech-chip--active' : ''}`}
                  onClick={() => setTechFilter(prev => (prev === tech ? 'all' : tech))}
                  title={`Filter to ${tech}`}
                >
                  <span className="cand-roster-tech-name">{tech}</span>
                  <span className="cand-roster-tech-count">{items.length}</span>
                </button>
              ))}
          </div>
        )}

        <div className="cand-roster-toolbar">
          <select
            className="cand-input cand-input--compact"
            value={month}
            onChange={e => { setMonth(e.target.value); setTechFilter('all') }}
            aria-label="Filter by deal month"
          >
            {monthOptions.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <select
            className="cand-input cand-input--compact"
            value={techFilter}
            onChange={e => setTechFilter(e.target.value)}
            aria-label="Filter by technology"
          >
            {techOptions.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <span className="cand-roster-count">
            {visibleRows.length}
            {' '}
            active
          </span>
        </div>

        <div className="cand-roster-table-wrap">
          <table className="cand-table cand-roster-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Name</th>
                <th>Technology</th>
                <th>Task</th>
                <th>Phone</th>
              </tr>
            </thead>
            <tbody>
              {loading && !roster && (
                <tr>
                  <td colSpan={5} className="cand-table-empty">Loading active roster…</td>
                </tr>
              )}
              {!loading && visibleRows.length === 0 && (
                <tr>
                  <td colSpan={5} className="cand-table-empty">No active candidates for these filters.</td>
                </tr>
              )}
              {visibleRows.map((row, idx) => (
                <tr
                  key={row.id}
                  className={`cand-row${row.needs_followup ? ' cand-row--pending' : ''}`}
                >
                  <td className="cand-cell-mono">{idx + 1}</td>
                  <td className="cand-cell-name">
                    <span className="cand-name">{row.name}</span>
                  </td>
                  <td><strong>{canonicalTechnology(row.technology) || '—'}</strong></td>
                  <td>{formatStage(row.task)}</td>
                  <td className="cand-cell-mono">{row.phone || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
