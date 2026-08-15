/**
 * Developer-only preview harness for the ten notification sounds.
 *
 * Automated tests can prove two sounds are different code; they cannot prove
 * they are distinguishable to a person. This exposes each sound on the console
 * so they can be auditioned one at a time.
 *
 * Never registered in a production build - the caller guards on DEV, and this
 * adds no UI, no route and no product surface.
 */

import { NOTIFICATION_IDS, NOTIFICATION_SOUNDS, soundEntry } from './soundRegistry.js'
import { previewNotification } from './notificationSounds.js'
import { unlockNotificationSound } from '../utils/notificationSound.js'

const GLOBAL_KEY = '__teleautomationSounds'

function buildApi() {
  return {
    /** Print the catalogue. */
    list() {
      const rows = NOTIFICATION_IDS.map((id, index) => ({
        '#': index + 1,
        id,
        sound: NOTIFICATION_SOUNDS[id].label,
        looping: NOTIFICATION_SOUNDS[id].loop,
      }))
      console.table(rows)
      return NOTIFICATION_IDS
    },

    /** Play one sound, ignoring quiet hours and the CRM toggle. */
    play(id) {
      unlockNotificationSound()
      if (!NOTIFICATION_SOUNDS[id]) {
        console.warn(`Unknown sound "${id}". Known ids:`, NOTIFICATION_IDS)
        return false
      }
      return previewNotification(id)
    },

    /** Stop a looping sound (call ring, ghost ambience, SLA tiers). */
    stop(id) {
      const entry = soundEntry(id)
      if (entry.stop) entry.stop()
      return true
    },

    /** Stop every looping sound. */
    stopAll() {
      for (const id of NOTIFICATION_IDS) {
        const entry = NOTIFICATION_SOUNDS[id]
        if (entry.stop) entry.stop()
      }
      return true
    },

    /**
     * Walk the whole catalogue with a gap between each, for A/B comparison.
     * Looping sounds are cut off after `holdMs` so the sequence keeps moving.
     */
    playAll({ gapMs = 3500, holdMs = 3000 } = {}) {
      unlockNotificationSound()
      NOTIFICATION_IDS.forEach((id, index) => {
        window.setTimeout(() => {
          const entry = NOTIFICATION_SOUNDS[id]
          console.log(`[${index + 1}/${NOTIFICATION_IDS.length}] ${id} - ${entry.label}`)
          previewNotification(id)
          if (entry.stop) window.setTimeout(() => entry.stop(), holdMs)
        }, index * gapMs)
      })
      return NOTIFICATION_IDS.length
    },
  }
}

export function installSoundPreview() {
  if (typeof window === 'undefined') return false
  if (window[GLOBAL_KEY]) return true
  window[GLOBAL_KEY] = buildApi()
  console.info(
    `[TeleAutomation] Notification sound preview ready - ${GLOBAL_KEY}.list(), .play(id), .playAll(), .stopAll()`,
  )
  return true
}
