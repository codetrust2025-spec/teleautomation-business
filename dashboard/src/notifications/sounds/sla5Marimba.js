/**
 * 4. REPLY SLA — 5 MINUTES — soft wooden marimba knock.
 *
 * Identity: two muted wooden strikes, low and warm, with a lowpass so nothing
 * sparkles. This is the calm end of the SLA ladder and must never be mistaken
 * for the 10-minute pulse or the 20-minute siren: it is acoustic where they are
 * electronic, and it descends where they do not.
 *
 * Implemented independently of every other SLA sound — see sla10Pulse.js and
 * sla20Siren.js, which share no signature generator with this file.
 */

import { masterGain, noiseBurst, pluck, withAudioContext } from '../soundEngine.js'

const FIRST = 523.25 // C5
const SECOND = 392.0 // G4

/** A marimba bar: fundamental plus the 4th-harmonic overtone bars are tuned to. */
function woodStrike(ctx, dest, freq, start) {
  noiseBurst(ctx, dest, {
    start,
    duration: 0.03,
    peak: 0.18,
    filterType: 'bandpass',
    frequency: freq * 2,
    Q: 0.7,
  })
  pluck(ctx, dest, { type: 'sine', freq, start, duration: 0.34, peak: 0.75 })
  pluck(ctx, dest, { type: 'sine', freq: freq * 4, start, duration: 0.12, peak: 0.14 })
}

export function playSla5Marimba() {
  return withAudioContext((ctx, t0) => {
    const master = masterGain(ctx, 0.32, t0)

    const warmth = ctx.createBiquadFilter()
    warmth.type = 'lowpass'
    warmth.frequency.setValueAtTime(2600, t0)
    warmth.connect(master)

    woodStrike(ctx, warmth, FIRST, t0)
    woodStrike(ctx, warmth, SECOND, t0 + 0.17)

    master.gain.setValueAtTime(0.32, t0 + 0.45)
    master.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.6)
  })
}
