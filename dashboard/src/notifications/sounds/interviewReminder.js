/**
 * 10. INTERVIEW REMINDER — clock ticks resolving into a high ding.
 *
 * Identity: four dry wooden ticks at a steady half-second pulse — literally the
 * sound of time passing — and only then a single high bell. Nothing else in the
 * app opens with a rhythm of clicks, so the meaning ("something is about to
 * start") is carried before the bell even arrives.
 *
 * Distinct from the interview *booking* bell, which is three ascending notes
 * and no ticks, and from the selection fanfare, which is a chord.
 */

import { masterGain, noiseBurst, pluck, withAudioContext } from '../soundEngine.js'

const TICKS = 4
const TICK_GAP = 0.28
const DING = 1760 // A6

export function playInterviewReminder() {
  return withAudioContext((ctx, t0) => {
    const master = masterGain(ctx, 0.5, t0)

    for (let i = 0; i < TICKS; i += 1) {
      const at = t0 + i * TICK_GAP
      // Alternating tick/tock pitch, as a real escapement does.
      const woody = i % 2 === 0 ? 2400 : 1900
      noiseBurst(ctx, master, {
        start: at,
        duration: 0.035,
        peak: 0.35,
        filterType: 'bandpass',
        frequency: woody,
        Q: 3,
      })
      pluck(ctx, master, { type: 'triangle', freq: woody / 4, start: at, duration: 0.045, peak: 0.25 })
    }

    const dingAt = t0 + TICKS * TICK_GAP + 0.1
    pluck(ctx, master, { type: 'sine', freq: DING, start: dingAt, duration: 0.85, peak: 0.75 })
    pluck(ctx, master, { type: 'sine', freq: DING * 2.7, start: dingAt, duration: 0.3, peak: 0.12 })

    const end = dingAt + 0.85
    master.gain.setValueAtTime(0.5, end - 0.15)
    master.gain.exponentialRampToValueAtTime(0.0001, end)
  })
}
