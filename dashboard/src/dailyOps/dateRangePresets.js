const PRESETS = [
  { id: 'today', label: 'Today' },
  { id: 'upcoming', label: 'Upcoming' },
  { id: 'thisWeek', label: 'This week' },
  { id: 'last7', label: 'Last 7 days' },
]

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function addDaysIso(iso, days) {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

function startOfWeekIso(iso) {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`)
  const day = d.getDay()
  const diff = day === 0 ? -6 : 1 - day
  d.setDate(d.getDate() + diff)
  return d.toISOString().slice(0, 10)
}

function endOfWeekIso(iso) {
  const d = new Date(`${startOfWeekIso(iso)}T12:00:00`)
  d.setDate(d.getDate() + 6)
  return d.toISOString().slice(0, 10)
}

function currentMonthRangeIso(iso) {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`)
  const from = new Date(d.getFullYear(), d.getMonth(), 1, 12)
  const to = new Date(d.getFullYear(), d.getMonth() + 1, 0, 12)
  return {
    from: from.toISOString().slice(0, 10),
    to: to.toISOString().slice(0, 10),
  }
}

export function resolvePresetRange(presetId) {
  const today = todayIso()
  switch (presetId) {
    case 'today':
      return { from: today, to: today }
    case 'upcoming':
      return { from: today, to: addDaysIso(today, 30) }
    case 'thisWeek':
      return { from: startOfWeekIso(today), to: endOfWeekIso(today) }
    case 'last7':
      return { from: addDaysIso(today, -7), to: today }
    default:
      return null
  }
}

export function detectPresetFromRange(from, to) {
  for (const preset of PRESETS) {
    const range = resolvePresetRange(preset.id)
    if (range?.from === from && range?.to === to) return preset.id
  }
  return 'custom'
}

export { PRESETS, todayIso }
