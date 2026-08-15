/**
 * 11. SCHEDULED CALL REMINDER — insistent rising triangle nudge (looping).
 *
 * Identity: a short two-note figure that *glides* upward on a triangle wave,
 * then drops onto a soft low thump. Half a second of sound every 3.5 s.
 *
 * Deliberately nothing like the incoming-call ring, which it used to borrow:
 * that is two sine tones held together for two whole seconds with a mechanical
 * 20 Hz warble on a six-second telephone cadence. This is a single gliding
 * voice, a third of the length, on a faster cadence. The distinction matters
 * because the two demand opposite things — a call arriving must be answered
 * now, a reminder means you owe someone a call.
 *
 * Triangle with portamento is unused elsewhere: the siren glides but is
 * sawtooth, the Gmail fault steps but is square and descends, and the DM chime
 * is a struck sine with no glide at all.
 */

import {
  getSharedAudioContext,
  masterGain,
  pluck,
  releaseNodes,
  withAudioContext,
} from '../soundEngine.js'

const LOW = 330
const MID = 660
const HIGH = 880
const THUMP = 165
const CADENCE_MS = 3500

let timer = null
let nodes = []

function nudge(ctx, t0) {
  const master = masterGain(ctx, 0.42, t0)
  nodes.push(master)

  // Rising glide — the "asking" gesture.
  pluck(ctx, master, {
    type: 'triangle',
    freq: LOW,
    endFreq: MID,
    start: t0,
    duration: 0.2,
    peak: 0.75,
  })
  // A brighter tap on top, landing where the glide arrives.
  pluck(ctx, master, { type: 'triangle', freq: HIGH, start: t0 + 0.19, duration: 0.13, peak: 0.6 })
  // Low thump for weight, so it carries across a room.
  pluck(ctx, master, { type: 'triangle', freq: THUMP, start: t0 + 0.3, duration: 0.22, peak: 0.4 })

  master.gain.setValueAtTime(0.42, t0 + 0.48)
  master.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.58)
}

/** One nudge, no loop — preview harness and tests. */
export function playCallReminder() {
  return withAudioContext((ctx, t0) => nudge(ctx, t0))
}

export function startCallReminder() {
  if (timer != null) return true
  const started = withAudioContext((ctx, t0) => nudge(ctx, t0))
  if (!started) return false
  timer = window.setInterval(() => {
    const ctx = getSharedAudioContext()
    if (ctx && timer != null) nudge(ctx, ctx.currentTime)
  }, CADENCE_MS)
  return true
}

export function stopCallReminder() {
  if (timer != null) {
    clearInterval(timer)
    timer = null
  }
  const ctx = getSharedAudioContext()
  releaseNodes(nodes, ctx ? ctx.currentTime : 0)
  nodes = []
}

export function isCallReminderLooping() {
  return timer != null
}
