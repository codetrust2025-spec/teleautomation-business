import React, { useMemo } from 'react'
import {
  navigatePendingWorkToCandidates,
  usePendingWorksContext,
} from './PendingWorksProvider.jsx'

export function PendingWorksStrip({ onOpenCandidates, maxPreview = 4, compact = false }) {
  const { works, count, candidateCount, loading, error } = usePendingWorksContext()
  const { preview, uniqueTotal } = useMemo(() => {
    const groups = new Map()
    for (const work of works) {
      const key = String(work.candidate_name || work.label || '').trim().toLowerCase() || work.id
      if (groups.has(key)) {
        groups.get(key).taskCount += 1
      } else {
        groups.set(key, { work, taskCount: 1 })
      }
    }
    const all = Array.from(groups.values())
    return { preview: all.slice(0, maxPreview), uniqueTotal: all.length }
  }, [works, maxPreview])

  if (!loading && count === 0) return null

  // Compact mode: single pill in the top bar
  if (compact) {
    return (
      <button
        type="button"
        className="pending-works-pill"
        onClick={() => navigatePendingWorkToCandidates(null, { onNavCandidates: onOpenCandidates })}
        aria-label={`Pending works: ${count} tasks`}
      >
        <span className="pending-works-pill__dot" aria-hidden />
        <span className="pending-works-pill__label">
          {loading ? 'Checking…' : `${count} pending`}
        </span>
        {!loading && candidateCount > 0 && (
          <span className="pending-works-pill__count">{candidateCount}</span>
        )}
      </button>
    )
  }

  return (
    <section className="pending-works-strip" aria-label="Pending works">
      <div className="pending-works-strip__row">
        <span className="pending-works-strip__pulse" aria-hidden />
        <div className="pending-works-strip__text">
          <strong className="pending-works-strip__title">Pending works</strong>
          <span className="pending-works-strip__meta">
            {loading
              ? 'Checking…'
              : `${count} task${count === 1 ? '' : 's'} · ${candidateCount} candidate${candidateCount === 1 ? '' : 's'}`}
          </span>
        </div>
        {!loading && count > 0 && (
          <button
            type="button"
            className="pending-works-strip__cta"
            onClick={() => navigatePendingWorkToCandidates(null, { onNavCandidates: onOpenCandidates })}
          >
            Open
          </button>
        )}
      </div>
      {error && <p className="pending-works-strip__error" role="alert">{error}</p>}
      {!loading && preview.length > 0 && (
        <div className="pending-works-strip__chips">
          {preview.map(({ work, taskCount }) => (
            <button
              type="button"
              key={work.id || `${work.candidate_name}-${work.label}`}
              className="pending-works-strip__chip"
              title={work.label}
              onClick={() => navigatePendingWorkToCandidates(work, { onNavCandidates: onOpenCandidates })}
            >
              {work.candidate_name || work.label}
              {taskCount > 1 ? ` (${taskCount})` : ''}
            </button>
          ))}
          {uniqueTotal > maxPreview && (
            <button
              type="button"
              className="pending-works-strip__chip pending-works-strip__chip--more"
              onClick={() => navigatePendingWorkToCandidates(null, { onNavCandidates: onOpenCandidates })}
            >
              +{uniqueTotal - maxPreview} more
            </button>
          )}
        </div>
      )}
    </section>
  )
}
