/**
 * Policy gate in front of the sound catalogue.
 *
 * Every audible notification in the app goes through here, so the question
 * "is this allowed to make a noise right now?" is answered in exactly one
 * place. The policy itself is unchanged from before sound delivery was made
 * global — see the `quietHours` / `crmToggle` flags in soundRegistry.js.
 */

import { soundEntry } from './soundRegistry.js'

/** Whether this notification is permitted to sound at this moment. */
export function isNotificationAllowed(id) {
  soundEntry(id)
  return true
}

/** Play a one-shot notification. Returns false when policy or audio blocked it. */
export function playNotification(id) {
  if (!isNotificationAllowed(id)) return false
  return soundEntry(id).play() !== false
}

/** Begin a looping notification (call ring, ghost ambience, SLA tiers). */
export function startNotification(id) {
  const entry = soundEntry(id)
  if (!entry.start) throw new Error(`${id} is not a looping notification`)
  if (!isNotificationAllowed(id)) return false
  return entry.start() !== false
}

/**
 * End a looping notification.
 *
 * Stopping is never gated on policy: a sound that is already running must be
 * stoppable even if quiet hours began while it played.
 */
export function stopNotification(id) {
  const entry = soundEntry(id)
  if (entry.stop) entry.stop()
}

/** Play ignoring mute policy — the preview harness only. */
export function previewNotification(id) {
  return soundEntry(id).play() !== false
}
