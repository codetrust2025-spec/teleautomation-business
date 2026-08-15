# Feature parity matrix — monolith vs split

Date: 2026-08-15
Method: static import-graph walk from each `dashboard/src/main.jsx`, plus a
comparison of every backend route against the reachable module set. No
`import.meta.glob` or dynamic `import()` exists in any repository, so static
reachability is exact.

Route matching is interpolation-aware: the client builds parameterised routes as
template literals, so `/account/{slot}/forward-message/start` never appears
literally. An earlier substring comparison missed **every** parameterised route
and wrongly reported zero losses. The numbers below come from the corrected
matcher.

## Marketing

| Module | Monolith entry path | Current feature | Verdict | Evidence |
|---|---|---|---|---|
| `components/GroupsUpload.jsx` | `main → App → GroupsUpload` | Group master-list merge/replace with backup | **MUST RESTORE → restored** | Sole consumer of `POST /groups/update`; backend live |
| `components/FleetDefaultsPanel.jsx` | `main → App → FleetDefaultsPanel` | Fleet-wide campaign/forwarding defaults | **MUST RESTORE → restored** | Sole consumer of `GET/POST /fleet/defaults`, `/fleet/apply-campaign`, `/fleet/apply-forwarding`, `/fleet/apply-source-url` |
| `components/ChangePasswordModal.jsx` | `main → App → HandlerKitPanel → ChangePasswordModal` | Dashboard password change | **MUST RESTORE → restored** | Sole consumer of `POST /auth/change-password`; parent is Operations-owned so it needed a new home |
| `components/ForwardMessagePanel.jsx` | `main → App → ModesSetupPanel → ForwardMessagePanel` | One-off message send to selected groups | **MUST RESTORE → restored** | Sole consumer of `/account/{slot}/forward-message/{start,cancel,groups,job}` and `/account/{slot}/forward-cycle/selection` |
| `components/crm/IncomingCallModal.jsx` | `main → App → IncomingCallModal` | Incoming Telegram call ring + modal | **MUST RESTORE → restored** | Driven by the `incoming_call` WebSocket message that `services/phone_call_service.py` still emits; no endpoint, so route analysis could not see it |
| `/start-test` | inline in monolith `App.jsx` | Start fleet in test mode | **MUST RESTORE → restored** | Header action |
| `/account/{slot}/clear-logs` | inline in monolith `App.jsx` | Clear an account's log history | **MUST RESTORE → restored** | Logs view action |
| `mobile/MobileApp.jsx`, `MobileDashboardHome`, `MobHealthRing`, `utils/useMobileShell` | `main → App → MobileApp → …` | Mobile shell | **ALREADY REPLACED** | New `App.jsx` is one responsive shell: `mobileNavOpen` + `@media (max-width: 900px)` in `messagingShell.css` |
| `FleetHealthPanel`, `ProgressHubPanel`, `ProgressSection`, `ProgressStatsPanel`, `DailyStatsPanel`, `AccountFleetGrid`, `AccountPerformanceChart` | via `ProgressHubPanel` | Fleet health and progress | **ALREADY REPLACED** | `DesktopDashboardHome` uses `buildFleetHealthRows`; `App.jsx` uses `aggregateFleetStats` |
| `desktop/DesktopApp.jsx`, `DesktopHeader`, `DesktopSidebar` | `main → App → DesktopApp` | Desktop shell | **ALREADY REPLACED** | Superseded by `App.jsx` + `MessagingSidebar` |
| `components/GroupsModal.jsx` | `main → App → GroupsModal` | Read-only group list viewer with download | **ALREADY REPLACED** | Header CSV download (`/groups/total-list`) plus the searchable preview in the restored Groups panel |
| `SetupMainPanel`, `SetupAccountPicker`, `SetupAccountFilter`, `AccountModeSwitcher`, `ModesSetupPanel`, `GlobalActions`, `DashboardColumn`, `ResizableDashboard` | via monolith `App.jsx` | Account setup surfaces | **ALREADY REPLACED** | `AccountPanel` with `modeFilter`/`workspaceMode` covers accounts, forwarding and campaigns |
| `/account/status` | inline in monolith `App.jsx` | Account info refresh | **ALREADY REPLACED** | Split polls `/state`, which carries the same `account_info` |
| `/auth/verify-admin` | `candidatesModule`, `dailyOps` | Admin re-verification | **INTENTIONALLY REMOVED (from Marketing)** | Only consumers were Operations-owned modules; route is a candidate for removal from the Marketing backend |
| `AccountStatusHero`, `AccountsLoginGuide`, `ConfirmDialog`, `GlobalProgressSection`, `GlobalWorkspaceMode`, `GroupCountLegend`, `SoundQuietHoursToggle`, `AISmartReplySettings`, `SectionContainer`, `deskDashboardWidgets`, `InboxMediaImage`, `deskPerformanceSeries`, `replyBuzzerSound` | — | — | **DEAD/LEGACY** | Unreachable in the monolith too |
| 27 further routes (`/voice/*`, `/groups/health*`, `/stats/daily`, `/message/preview`, …) | — | — | **DEAD/LEGACY** | Unreachable in the monolith too; recorded in `featureReachability.test.js` |

**Marketing result: 0 unexplained feature losses.**

## Operations

| Module / route | Monolith entry path | Current feature | Verdict | Evidence |
|---|---|---|---|---|
| `POST /bookings/confirm` | `main → SubmitSlotPage` | **Public slot booking** | **MUST RESTORE → restored** | The split posted to `/public/slots/book`, which the backend answers **HTTP 410 "retired"**. Public booking was fully broken. Also added the required `phone` identity, `candidate_id`, and `idempotency_key` |
| `notifications/sounds/{callRing,dmChime,callReminder,sla5Marimba,sla10Pulse,sla20Siren,unreadGhost}` | via `replyAlertSounds` | Messaging alert sounds | **INTENTIONALLY REMOVED** | Marketing-domain sounds. Operations' registry holds only its own four recruitment sounds |
| `utils/soundQuietHours.js` | via `notificationEvents` | Quiet hours | **INTENTIONALLY REMOVED** | Applies only to `quietHours: true` sounds; all four Operations sounds are `quietHours: false` |
| `components/ui/{Button,ModalShell,SegmentedControl}.jsx` | various | Shared primitives | **DEAD/LEGACY** | Unused component library copies |
| `GET /ai/ocr-policy` | `main → App → adminModule → OcrPolicySection` | OCR policy admin config | **NEEDS DECISION** | Lived inside Marketing's AI-settings overlay in the monolith; `admin/OcrPolicySection.jsx` was not copied to Operations. The only remaining unexplained gap |

**Operations result: 1 unresolved gap (`/ai/ocr-policy` admin screen).**

## Reachability, before and after

| Repo | Reachable before | Reachable after | Orphaned before | Orphaned after | Unexplained losses after |
|---|---:|---:|---:|---:|---:|
| Monolith (baseline) | 215 | 215 | 24 | 24 | — |
| Marketing | 115 | 124 | 53 | 44 | 0 |
| Operations | 61 | 61 | 21 | 21 | 1 |

Unreferenced UI-facing backend routes: Marketing 37 → **30**, Operations 39 → **38**.

Orphan counts are deliberately not driven to zero. What matters is that every
remaining orphan is either unreachable in the monolith as well, or explicitly
superseded above.

## Guard against recurrence

`dashboard/src/featureReachability.test.js` in both repositories walks the import
graph and fails if a listed capability stops being reachable, or if the set of
unreferenced routes grows. `SubmitSlotBookingBoundary.test.js` additionally locks
the public booking boundary.

These exist because the previous state passed every unit test and every
production build while the features were missing: an unimported module is simply
never bundled, so nothing fails.
