/** Bumped on each production deploy so Vite emits a new app-[hash].js (cache bust). */
export const BUILD_STAMP = '2026-07-18T100127Z'

/** API base — Vite dev uses the dev-server proxy configured in vite.config.js. */
export const isDevFrontend = import.meta.env.DEV || window.location.port === '3000'
export const API = isDevFrontend
  ? ''
  : `${window.location.protocol}//${window.location.host}`
export const WS = isDevFrontend
  ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
export const OPERATIONS_PUBLIC_URL = (import.meta.env.VITE_OPERATIONS_PUBLIC_URL || API).replace(/\/$/, '')
export const MARKETING_PUBLIC_URL = (import.meta.env.VITE_MARKETING_PUBLIC_URL || '').replace(/\/$/, '')

export const COUNTRY_CODES = ['+91', '+1', '+44', '+971', '+61', '+65', '+60']
export const SAVED_PHONES = []
