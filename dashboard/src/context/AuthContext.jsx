import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

const AuthContext = createContext(null)

let fetchInterceptorInstalled = false

function installAuthFetchInterceptor() {
  if (fetchInterceptorInstalled || typeof window === 'undefined') return
  fetchInterceptorInstalled = true
  const nativeFetch = window.fetch.bind(window)
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input?.url || ''
    const res = await nativeFetch(input, {
      ...init,
      credentials: init.credentials ?? 'include',
    })
    if (res.status === 401 && !url.includes('/auth/login') && !url.includes('/auth/status')) {
      window.dispatchEvent(new CustomEvent('auth:required'))
    }
    return res
  }
}

const K1 = typeof window !== 'undefined' && window.location.port === '3000'
const API_BASE = K1 ? '' : (typeof window !== 'undefined'
  ? `${window.location.protocol}//${window.location.host}`
  : '')

export function AuthProvider({ children }) {
  const [state, setState] = useState({
    loading: true,
    enabled: false,
    authenticated: false,
    username: null,
    role: 'admin',
    reference: null,
    error: '',
  })

  const refresh = useCallback(async () => {
    try {
      const data = await (await fetch(`${API_BASE}/auth/status`, { credentials: 'include' })).json()
      setState({
        loading: false,
        enabled: !!data.enabled,
        authenticated: !!data.authenticated,
        username: data.username || null,
        role: data.role || 'admin',
        reference: data.reference || null,
        error: '',
      })
      return data
    } catch {
      setState((prev) => ({ ...prev, loading: false, error: 'Could not reach server' }))
      return null
    }
  }, [])

  useEffect(() => {
    installAuthFetchInterceptor()
    refresh()
    const onAuthRequired = () => {
      setState((prev) => ({ ...prev, authenticated: false, username: null }))
    }
    window.addEventListener('auth:required', onAuthRequired)
    return () => window.removeEventListener('auth:required', onAuthRequired)
  }, [refresh])

  const login = useCallback(async (username, password) => {
    setState((prev) => ({ ...prev, error: '' }))
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const error = data.detail || data.message || 'Invalid username or password'
      setState((prev) => ({ ...prev, error, authenticated: false }))
      return { ok: false, error }
    }
    setState({
      loading: false,
      enabled: true,
      authenticated: true,
      username: data.username || username,
      role: data.role || 'admin',
      reference: data.reference || null,
      error: '',
    })
    return { ok: true }
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' })
    } catch { /* ignore */ }
    setState((prev) => ({
      ...prev,
      authenticated: false,
      username: null,
      role: 'admin',
      reference: null,
    }))
  }, [])

  const value = useMemo(
    () => ({ ...state, refresh, login, logout }),
    [state, refresh, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}
