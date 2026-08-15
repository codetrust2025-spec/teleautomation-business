import React, { useEffect, useRef, useState } from 'react'
import { CandidatesPanel } from './components/CandidatesPanel.jsx'
import { DailyOpsPanel } from './dailyOps/DailyOpsPanel.jsx'
import { DataRoomPanel } from './components/DataRoomPanel.jsx'
import { DailyBriefingCard } from './components/DailyBriefingCard.jsx'
import { RecruitmentMailPanel } from './components/RecruitmentMailPanel.jsx'
import { MailMonitoringNotifications } from './components/MailMonitoringNotifications.jsx'
import { HandlerKitPanel } from './components/HandlerKitPanel.jsx'
import OutcomeAuditPanel from './components/OutcomeAuditPanel.jsx'
import PaymentReconciliationPanel from './components/PaymentReconciliationPanel.jsx'
import BgvRegisterPanel from './components/BgvRegisterPanel.jsx'
import { OcrPolicyPanel } from './components/OcrPolicyPanel.jsx'
import GlobalNotificationSounds from './notifications/GlobalNotificationSounds.jsx'
import {
  PendingWorksProvider,
  usePendingWorksContextOptional,
} from './dailyOps/PendingWorksProvider.jsx'
import { useAuth } from './context/AuthContext.jsx'

const VIEWS = [
  { id: 'daily-briefing', label: 'Daily briefing', icon: '☀' },
  { id: 'ai-recruitment', label: 'AI Mail Review', icon: 'AI' },
  { id: 'outcome-audit', label: 'Mail Audit', icon: '🔍' },
  { id: 'mail-notifications', label: 'Mail alerts', icon: '🔔' },
  { id: 'candidates', label: 'Candidates', icon: '▣', badge: 'works' },
  { id: 'daily-ops', label: 'Daily ops', icon: '▤', badge: 'interviews' },
  { id: 'payment-reconciliation', label: 'Payment reconciliation', icon: '₹' },
  { id: 'bgv-register', label: 'BGV register', icon: '✓' },
  { id: 'data-room', label: 'Data room', icon: '▥' },
  { id: 'handler-kit', label: 'Handler kit', icon: '◆' },
  { id: 'settings', label: 'Settings', icon: '⚙' },
  { id: 'slot-booking', label: 'Slot booking', icon: '▦', external: '/submit-slot' },
]

function countLabel(value) {
  return value > 99 ? '99+' : value
}

function OperationsShell({ view, onNavigate }) {
  const auth = useAuth()
  const pending = usePendingWorksContextOptional()
  const [sidebarUserMenuOpen, setSidebarUserMenuOpen] = useState(false)
  const [headerUserMenuOpen, setHeaderUserMenuOpen] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const sidebarUserMenuRef = useRef(null)
  const headerUserMenuRef = useRef(null)
  const activeView = VIEWS.find(item => item.id === view) || VIEWS[0]
  const displayName = auth.username || 'Administrator'
  const userInitials = displayName.slice(0, 2).toUpperCase()

  useEffect(() => {
    const navigate = event => {
      const requested = event.detail?.view
      if (VIEWS.some(item => item.id === requested && !item.external)) onNavigate(requested)
    }
    window.addEventListener('teleautomation:navigate', navigate)
    return () => window.removeEventListener('teleautomation:navigate', navigate)
  }, [onNavigate])

  useEffect(() => {
    if (!sidebarUserMenuOpen && !headerUserMenuOpen) return undefined
    const close = event => {
      if (sidebarUserMenuRef.current && !sidebarUserMenuRef.current.contains(event.target)) {
        setSidebarUserMenuOpen(false)
      }
      if (headerUserMenuRef.current && !headerUserMenuRef.current.contains(event.target)) {
        setHeaderUserMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [sidebarUserMenuOpen, headerUserMenuOpen])

  const navigate = id => {
    const target = VIEWS.find(item => item.id === id)
    if (target?.external) {
      window.open(target.external, '_blank', 'noopener,noreferrer')
      setMobileNavOpen(false)
      return
    }
    onNavigate(id)
    setMobileNavOpen(false)
  }

  return (
    <div className="desktop-app business-desktop-app">
      <aside className={`desktop-sidebar desktop-sidebar--sigma${mobileNavOpen ? ' desktop-sidebar--open' : ''}`}>
        <button
          type="button"
          className="desktop-sidebar__brand"
          onClick={() => navigate('candidates')}
          aria-label="Go to candidates"
        >
          <span className="desktop-sidebar__logo" aria-hidden>⚡</span>
          <span className="desktop-sidebar__title">TeleAutomation</span>
        </button>

        <div className="business-sidebar__label">Operations</div>
        <nav className="desktop-sidebar__nav" aria-label="Operations navigation">
          {VIEWS.map(item => {
            const badgeValue = item.badge === 'works'
              ? pending?.count || 0
              : item.badge === 'interviews'
                ? pending?.pendingInterviewCount || 0
                : 0
            return (
              <button
                key={item.id}
                type="button"
                className={`desktop-sidebar__link${view === item.id ? ' desktop-sidebar__link--active' : ''}`}
                onClick={() => navigate(item.id)}
              >
                <span className="desktop-sidebar__link-icon" aria-hidden>{item.icon}</span>
                <span>{item.label}</span>
                {badgeValue > 0 && <span className="desktop-sidebar__badge">{countLabel(badgeValue)}</span>}
              </button>
            )
          })}
        </nav>

        <div className="desktop-sidebar__footer">
          <div className="desktop-sidebar__status">
            <span className="desktop-sidebar__status-check" aria-hidden>✓</span>
            Operations service online
          </div>
          <div className="desktop-sidebar__version">Independent operations workspace</div>
          <div className="desktop-sidebar__user-wrap" ref={sidebarUserMenuRef}>
            <button
              type="button"
              className="desktop-sidebar__user"
              aria-expanded={sidebarUserMenuOpen}
              onClick={() => setSidebarUserMenuOpen(open => !open)}
            >
              <span className="desktop-sidebar__user-avatar" aria-hidden>{userInitials}</span>
              <span className="desktop-sidebar__user-text">
                <span className="desktop-sidebar__user-name">{displayName}</span>
                <span className="desktop-sidebar__user-role">{auth.role || 'Administrator'}</span>
              </span>
              <span className="desktop-sidebar__user-chev" aria-hidden>▾</span>
            </button>
            {sidebarUserMenuOpen && (
              <div className="desk-user-menu desk-user-menu--sidebar" role="menu">
                {auth.enabled ? (
                  <button
                    type="button"
                    className="desk-user-menu__item desk-user-menu__item--danger"
                    role="menuitem"
                    onClick={auth.logout}
                  >
                    Sign out
                  </button>
                ) : (
                  <p className="desk-user-menu__hint">Login is not required on this server.</p>
                )}
              </div>
            )}
          </div>
        </div>
      </aside>

      {mobileNavOpen && (
        <button
          type="button"
          className="business-nav-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <div className="desktop-main">
        <header className="desktop-header desktop-header--sigma business-header">
          <button
            type="button"
            className="business-menu-button"
            aria-label="Open navigation"
            onClick={() => setMobileNavOpen(true)}
          >
            ☰
          </button>
          <div className="business-header__title-wrap">
            <p className="business-header__eyebrow">Operations workspace</p>
            <h1 className="business-header__title">{activeView.label}</h1>
          </div>
          <div className="desktop-header__status-center business-header__status">
            <span className="desktop-header__status-pulse" aria-hidden />
            <span className="desktop-header__status-text">Operations service connected</span>
          </div>
          <div className="desktop-header__actions business-header__user-wrap" ref={headerUserMenuRef}>
            <button
              type="button"
              className="desktop-header__user"
              aria-label="Open account menu"
              aria-expanded={headerUserMenuOpen}
              onClick={() => setHeaderUserMenuOpen(open => !open)}
            >
              {userInitials}
            </button>
            {headerUserMenuOpen && (
              <div className="desk-user-menu business-header__user-menu" role="menu">
                <p className="desk-user-menu__hint">Signed in as {displayName}</p>
                {auth.enabled ? (
                  <button
                    type="button"
                    className="desk-user-menu__item desk-user-menu__item--danger"
                    role="menuitem"
                    onClick={auth.logout}
                  >
                    Sign out
                  </button>
                ) : (
                  <p className="desk-user-menu__hint">Login is not required on this server.</p>
                )}
              </div>
            )}
          </div>
        </header>

        <main className={`desktop-body business-body${view === 'daily-ops' ? ' desktop-body--daily-ops' : ''}`}>
          {view === 'candidates' && <CandidatesPanel />}
          {view === 'daily-ops' && <DailyOpsPanel onNavCandidates={() => navigate('candidates')} />}
          {view === 'ai-recruitment' && <RecruitmentMailPanel />}
          {view === 'outcome-audit' && <OutcomeAuditPanel />}
          {view === 'mail-notifications' && <MailMonitoringNotifications />}
          {view === 'daily-briefing' && <div className="daily-briefing-page"><DailyBriefingCard /></div>}
          {view === 'payment-reconciliation' && <PaymentReconciliationPanel />}
          {view === 'bgv-register' && <BgvRegisterPanel />}
          {view === 'data-room' && <DataRoomPanel />}
          {view === 'handler-kit' && <HandlerKitPanel username={auth.username} reference={auth.reference} />}
          {view === 'settings' && <OcrPolicyPanel />}
        </main>
      </div>
    </div>
  )
}

export default function App() {
  const [view, setView] = useState('candidates')

  return (
    <PendingWorksProvider mainView={view}>
      <GlobalNotificationSounds />
      <OperationsShell view={view} onNavigate={setView} />
    </PendingWorksProvider>
  )
}
