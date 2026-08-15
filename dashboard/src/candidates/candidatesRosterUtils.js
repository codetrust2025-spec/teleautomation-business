import { API } from '../config.js'

const TECH_ALIASES = {
  'react js': 'React JS',
  reactjs: 'React JS',
  'mern stack': 'MERN stack',
  'aws devops': 'AWS DevOps',
  'automation testing': 'Automation Testing',
  testing: 'Testing',
  etl: 'ETL',
  'sap basis': 'SAP BASIS',
  unspecified: 'Unspecified',
}

/** Merge spelling variants (React Js vs React JS) for roster chips/filters. */
export function canonicalTechnology(tech) {
  const raw = String(tech || '').trim()
  if (!raw) return 'Unspecified'
  const key = raw.toLowerCase().replace(/_/g, ' ').replace(/-/g, ' ').replace(/\s+/g, ' ')
  return TECH_ALIASES[key] || raw
}

export function rosterQueryParams({ month = 'all', reference = 'all' } = {}) {
  const params = new URLSearchParams()
  if (month && month !== 'all') params.set('month', month)
  if (reference && reference !== 'all') params.set('reference', reference)
  return params
}

export async function fetchActiveRoster({ month, reference } = {}) {
  const qs = rosterQueryParams({ month, reference }).toString()
  const url = `${API}/candidates/roster${qs ? `?${qs}` : ''}`
  const res = await fetch(url, { credentials: 'include', cache: 'no-store' })
  const data = await res.json()
  if (data.status !== 'ok') {
    throw new Error(data.message || 'Failed to load active roster')
  }
  return data
}

export function activeRosterDownloadUrl({ month, reference } = {}) {
  const qs = rosterQueryParams({ month, reference }).toString()
  return `${API}/candidates/roster.csv${qs ? `?${qs}` : ''}`
}

/** Build Excel-friendly CSV from visible rows (matches server export format). */
export function buildRosterCsv(rows) {
  const sorted = [...rows].sort((a, b) => {
    const ta = canonicalTechnology(a.technology).toLowerCase()
    const tb = canonicalTechnology(b.technology).toLowerCase()
    if (ta !== tb) return ta.localeCompare(tb)
    return String(a.name || '').localeCompare(String(b.name || ''), undefined, { sensitivity: 'base' })
  })
  const escape = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const lines = [['#', 'Name', 'Technology'].map(escape).join(',')]
  sorted.forEach((row, i) => {
    lines.push([
      escape(i + 1),
      escape((row.name || '').trim()),
      escape(canonicalTechnology(row.technology)),
    ].join(','))
  })
  return `\ufeff${lines.join('\r\n')}\r\n`
}

export function downloadRosterCsv(rows, filename = 'active_candidates.csv') {
  const blob = new Blob([buildRosterCsv(rows)], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function triggerRosterDownload({ month, reference, rows = null } = {}) {
  if (Array.isArray(rows) && rows.length > 0) {
    const suffix = month && month !== 'all' ? `_${month}` : ''
    downloadRosterCsv(rows, `active_candidates${suffix}.csv`)
    return
  }
  const url = activeRosterDownloadUrl({ month, reference })
  const a = document.createElement('a')
  a.href = url
  a.rel = 'noopener'
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}
