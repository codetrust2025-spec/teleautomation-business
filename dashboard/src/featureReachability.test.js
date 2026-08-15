/**
 * Guards against silent feature loss.
 *
 * When the split rewrote the shells, modules stopped being imported and their
 * features vanished from the shipped bundle while every unit test and the
 * production build still passed. An unimported module simply is not bundled,
 * so nothing fails — the capability is just gone.
 *
 * These tests therefore walk the real import graph from the entry point and
 * assert reachability, not file existence.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, dirname, resolve, extname, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(SRC, '..', '..')

function resolveImport(spec, fromFile) {
  if (!spec.startsWith('.')) return null
  const base = resolve(dirname(fromFile), spec.split('?')[0])
  for (const c of [base, `${base}.js`, `${base}.jsx`, join(base, 'index.js'), join(base, 'index.jsx')]) {
    if (existsSync(c) && statSync(c).isFile()) return c
  }
  return null
}

function reachableFromEntry() {
  const seen = new Set()
  const queue = [join(SRC, 'main.jsx')]
  while (queue.length) {
    const file = queue.pop()
    if (!file || seen.has(file)) continue
    seen.add(file)
    const code = readFileSync(file, 'utf8')
    const specs = [
      ...code.matchAll(/(?:import|export)[^'"]*?from\s*['"]([^'"]+)['"]/g),
      ...code.matchAll(/\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g),
      ...code.matchAll(/^\s*import\s+['"]([^'"]+)['"]/gm),
    ].map((m) => m[1])
    for (const s of specs) {
      const r = resolveImport(s, file)
      if (r) queue.push(r)
    }
  }
  return seen
}

const reachable = reachableFromEntry()
const reachableRel = new Set([...reachable].map((f) => relative(SRC, f).replace(/\\/g, '/')))
const reachableCode = [...reachable]
  .filter((f) => /\.jsx?$/.test(f))
  .map((f) => readFileSync(f, 'utf8'))
  .join('\n')

const REQUIRED_MODULES = {
  candidates: 'components/CandidatesPanel.jsx',
  'bookings and daily ops': 'dailyOps/DailyOpsPanel.jsx',
  'data room': 'components/DataRoomPanel.jsx',
  'recruitment mail': 'components/RecruitmentMailPanel.jsx',
  'mail monitoring notifications': 'components/MailMonitoringNotifications.jsx',
  'staff handler kit': 'components/HandlerKitPanel.jsx',
  'outcome audit': 'components/OutcomeAuditPanel.jsx',
  'payment reconciliation': 'components/PaymentReconciliationPanel.jsx',
  'BGV register': 'components/BgvRegisterPanel.jsx',
  'daily briefing': 'components/DailyBriefingCard.jsx',
  'notification sounds': 'notifications/GlobalNotificationSounds.jsx',
  'OCR policy admin': 'components/OcrPolicyPanel.jsx',
}

describe('operations feature reachability', () => {
  for (const [feature, module] of Object.entries(REQUIRED_MODULES)) {
    it(`${feature} is reachable from main.jsx`, () => {
      expect(reachableRel.has(module)).toBe(true)
    })
  }

  it('keeps only its own recruitment-domain notification sounds', () => {
    // The split copied Marketing's messaging sounds (call ring, DM chime, SLA
    // pulses, unread ambience) into this repo. They are intentionally unused:
    // Operations owns mail and interview events, not messaging. Quiet hours is
    // likewise Marketing-only, which is why every entry here is quietHours:false.
    const registry = readFileSync(join(SRC, 'notifications', 'soundRegistry.js'), 'utf8')
    for (const own of ['interviewBookingBell', 'selectionFanfare', 'gmailFault', 'interviewReminder']) {
      expect(registry).toContain(own)
    }
    for (const foreign of ['callRing', 'dmChime', 'sla10Pulse', 'unreadGhost']) {
      expect(registry).not.toContain(foreign)
    }
  })
})

function backendRoutes() {
  const skipDirs = new Set(['node_modules', '.git', 'dashboard', 'static', 'data',
    'logs', 'android', '__pycache__', 'tests', '.venv', '.pytest_cache'])
  const files = []
  const walk = (d) => {
    for (const e of readdirSync(d)) {
      if (skipDirs.has(e)) continue
      const p = join(d, e)
      let st
      try { st = statSync(p) } catch { continue }
      if (st.isDirectory()) walk(p)
      else if (extname(p) === '.py') files.push(p)
    }
  }
  walk(REPO)
  const routes = new Set()
  for (const f of files) {
    for (const m of readFileSync(f, 'utf8')
      .matchAll(/@(?:app|router)\.(?:get|post|put|patch|delete)\(\s*["'`]([^"'`]+)["'`]/g)) {
      routes.add(m[1])
    }
  }
  return [...routes]
}

describe('operations backend routes are reachable from the UI', () => {
  const NON_UI = [
    /^\/internal\//, /^\/health$/, /^\/version$/, /^\/$/, /^\/ws/, /^\/openapi/,
    /^\/docs/, /^\/webhook/, /^\/static/, /^\/favicon/, /^\/login$/, /^\/logout$/,
    /^\/auth\/login/, /^\/auth\/logout/, /\{full_path/,
    /oauth\/google\/callback/,   // provider redirect target, not fetched
    /pubsub/,                    // Gmail push delivery endpoint
  ]

  // The client builds parameterised routes as template literals, so the literal
  // "{candidate_id}" never appears in source. Match interpolations instead.
  function referenced(route) {
    const pattern = route
      .split(/\{[^}]+\}/)
      .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
      .join('[^\'"`\\s]*')
    return new RegExp(pattern).test(reachableCode)
  }

  const uiRoutes = backendRoutes().filter((r) => !NON_UI.some((re) => re.test(r)))

  it('reports which UI-facing routes no reachable module references', () => {
    const unreferenced = uiRoutes.filter((r) => !referenced(r))
    // Locked to the state verified on 2026-08-15. This must not grow: a new
    // entry means a feature became unreachable. Shrinking it is an improvement
    // and the baseline should be lowered to match.
    expect(unreferenced.length).toBeLessThanOrEqual(BASELINE_UNREFERENCED)
  })
})

// Measured 2026-08-15 after the parity pass, then lowered when the OCR policy
// admin screen was rebuilt here (it had lived inside Marketing's AI-settings
// overlay in the monolith, which is why the split lost it). Every remaining
// entry is unreachable in the monolith too, so no unexplained loss survives.
// See docs/feature-parity-matrix.md.
const BASELINE_UNREFERENCED = 36
