import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { AuthGate } from './components/AuthGate.jsx'
import { SubmitSlotPage } from './pages/SubmitSlotPage.jsx'
import { ConfirmProvider } from './context/ConfirmContext.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import './index.css'
import './businessShell.css'
import './dailyOps.css'
import './recruitmentMail.css'
import './components/ui/CommonModal.css'

const submitSlot = window.location.pathname.replace(/\/+$/, '') === '/submit-slot'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {submitSlot ? <SubmitSlotPage /> : <AuthProvider><ConfirmProvider><AuthGate><App /></AuthGate></ConfirmProvider></AuthProvider>}
  </React.StrictMode>,
)
