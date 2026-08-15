/**
 * 3. INCOMING VOICE CALL — telephone ring cadence (looping).
 *
 * Identity: the two ringback tones sound *together*, not in sequence, which is
 * what makes a telephone sound like a telephone. Two seconds ringing, four
 * seconds silence, repeating — a cadence no alert or chime in this app shares.
 */

import { masterGain, releaseNodes, withAudioContext, getSharedAudioContext } from '../soundEngine.js'

const RING_A = 440
const RING_B = 480
const BURST_MS = 2000
const CADENCE_MS = 6000

let timer = null
let nodes = []

/** One 2-second ring: both tones together, pulsed at 20 Hz like a real bell. */
function ringBurst(ctx, t0) {
  const master = masterGain(ctx, 0.5, t0)
  nodes.push(master)

  // A 20 Hz tremolo across the pair is the mechanical warble of a ringer.
  const trem = ctx.createOscillator()
  trem.type = 'sine'
  trem.frequency.setValueAtTime(20, t0)
  const tremDepth = ctx.createGain()
  tremDepth.gain.setValueAtTime(0.35, t0)
  trem.connect(tremDepth)

  const body = ctx.createGain()
  body.gain.setValueAtTime(0.55, t0)
  tremDepth.connect(body.gain)
  body.connect(master)
  nodes.push(trem, tremDepth, body)

  for (const freq of [RING_A, RING_B]) {
    const osc = ctx.createOscillator()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(freq, t0)
    osc.connect(body)
    osc.start(t0)
    osc.stop(t0 + BURST_MS / 1000)
    nodes.push(osc)
  }

  trem.start(t0)
  trem.stop(t0 + BURST_MS / 1000)

  master.gain.setValueAtTime(0.5, t0 + BURST_MS / 1000 - 0.05)
  master.gain.exponentialRampToValueAtTime(0.0001, t0 + BURST_MS / 1000)
}

export function startCallRing() {
  if (timer != null) return true
  return withAudioContext((ctx, t0) => {
    ringBurst(ctx, t0)
    timer = window.setInterval(() => {
      const live = getSharedAudioContext()
      if (!live || timer == null) return
      ringBurst(live, live.currentTime)
    }, CADENCE_MS)
  })
}

export function stopCallRing() {
  if (timer != null) {
    clearInterval(timer)
    timer = null
  }
  const ctx = getSharedAudioContext()
  releaseNodes(nodes, ctx ? ctx.currentTime : 0)
  nodes = []
}

export function isCallRinging() {
  return timer != null
}
