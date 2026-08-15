/**
 * 1. NEW INCOMING DM — bright two-note glass chime.
 *
 * Identity: very short (~0.35s), two rising sine partials with a glassy
 * inharmonic shimmer on top. Pleasant and quiet enough to fire many times an
 * hour without becoming punishment. Nothing else in the app is this brief or
 * this bright.
 */

import { masterGain, pluck, withAudioContext } from '../soundEngine.js'

const LOW = 1396.91 // F6
const HIGH = 2093.0 // C7

export function playDmChime() {
  return withAudioContext((ctx, t0) => {
    const master = masterGain(ctx, 0.4, t0)
    master.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.42)

    // Two struck notes, a fifth apart, overlapping slightly.
    pluck(ctx, master, { type: 'sine', freq: LOW, start: t0, duration: 0.16, peak: 0.85 })
    pluck(ctx, master, { type: 'sine', freq: HIGH, start: t0 + 0.075, duration: 0.24, peak: 0.7 })

    // Inharmonic partials are what make a strike read as glass rather than
    // as a pure tone. Quiet, and detuned off any musical interval.
    pluck(ctx, master, { type: 'triangle', freq: LOW * 2.76, start: t0, duration: 0.1, peak: 0.12 })
    pluck(ctx, master, { type: 'triangle', freq: HIGH * 2.76, start: t0 + 0.075, duration: 0.12, peak: 0.1 })
  })
}
