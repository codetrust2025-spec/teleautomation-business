/**
 * 7. INTERVIEW BOOKING MAIL — ascending three-note bell.
 *
 * Identity: three separate bell strikes climbing an E major triad, each ringing
 * out before the next lands. Single melodic line, no chord, no sweep. The
 * companion selection/offer sound is a *chord* — see selectionFanfare.js —
 * so the two are distinguishable in the first 200 ms.
 *
 * Replaces the shared klaxon generator, which produced these two alerts as
 * three-sweep and five-sweep variants of one timbre.
 */

import { masterGain, pluck, withAudioContext } from '../soundEngine.js'

const NOTES = [659.25, 830.61, 987.77] // E5, G#5, B5
const STEP = 0.17

export function playInterviewBookingBell() {
  return withAudioContext((ctx, t0) => {
    const master = masterGain(ctx, 0.55, t0)

    NOTES.forEach((freq, index) => {
      const at = t0 + index * STEP
      const last = index === NOTES.length - 1
      // Bell timbre: fundamental plus a quiet 2.4x partial, which is
      // inharmonic and therefore reads as metal rather than as an organ.
      pluck(ctx, master, { type: 'sine', freq, start: at, duration: last ? 0.9 : 0.4, peak: 0.8 })
      pluck(ctx, master, { type: 'sine', freq: freq * 2.4, start: at, duration: last ? 0.5 : 0.22, peak: 0.16 })
    })

    const end = t0 + (NOTES.length - 1) * STEP + 0.9
    master.gain.setValueAtTime(0.55, end - 0.15)
    master.gain.exponentialRampToValueAtTime(0.0001, end)
  })
}
