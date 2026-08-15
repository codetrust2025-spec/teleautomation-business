/**
 * The shared AudioContext and the browser autoplay unlock.
 *
 * This file used to hold the DM chime, the unread ambience and the call ring as
 * well. Those moved to src/notifications/sounds/, one identity per file, so
 * that no two notifications can share a generator by accident. What stays here
 * is the part every sound needs and none of them owns: a single context for the
 * tab, and the near-silent blip that satisfies autoplay policy.
 */

let audioCtx = null
let unlocked = false

function getContext() {
  const Ctx = window.AudioContext || window.webkitAudioContext
  if (!Ctx) return null
  if (!audioCtx) audioCtx = new Ctx()
  return audioCtx
}

/** Play a near-silent blip — browsers require audio during the gesture that unlocks. */
function playSilentUnlockBlip(ctx) {
  try {
    const t0 = ctx.currentTime
    const osc = ctx.createOscillator()
    const g = ctx.createGain()
    g.gain.setValueAtTime(0.001, t0)
    osc.connect(g)
    g.connect(ctx.destination)
    osc.start(t0)
    osc.stop(t0 + 0.02)
  } catch {
    /* ignore */
  }
}

/**
 * Call on user interaction (click/key) so later notification sounds are allowed.
 * Safe to call repeatedly.
 */
export function unlockNotificationSound() {
  const ctx = getContext()
  if (!ctx) return

  const finish = () => {
    playSilentUnlockBlip(ctx)
    unlocked = true
  }

  if (ctx.state === 'suspended') {
    ctx.resume().then(finish).catch(() => {})
  } else {
    finish()
  }
}

export function isNotificationSoundUnlocked() {
  return unlocked
}

export function getSharedAudioContext() {
  return getContext()
}
