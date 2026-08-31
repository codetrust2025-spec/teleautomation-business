import { API } from '../config.js'

function parseFetchError(res, data, fallback) {
  if (res.status === 401) return 'Sign in required — refresh the page or log in again.'
  if (res.status === 403) return 'Admin access required for this section.'
  return data?.detail || data?.message || fallback || `Request failed (${res.status})`
}

async function credsRequest(method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    credentials: 'include',
    headers: body != null ? { 'Content-Type': 'application/json' } : undefined,
    body: body != null ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (data.status !== 'ok') {
    throw new Error(data.message || data.detail || `Request failed (${res.status})`)
  }
  return data.credentials
}

/** Load dashboard credentials for Data room Logins / Vault tabs. */
export async function fetchCredentials({ signal } = {}) {
  const res = await fetch(`${API}/data-room/credentials`, {
    credentials: 'include',
    signal,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(parseFetchError(res, data, 'Failed to load credentials'))
  }
  if (data.status !== 'ok') {
    throw new Error(data.message || 'Failed to load credentials')
  }
  return data.credentials || null
}

export function createHandler(body) {
  return credsRequest('POST', '/data-room/credentials/handlers', body)
}

export function updateHandler(username, body) {
  return credsRequest('PATCH', `/data-room/credentials/handlers/${encodeURIComponent(username)}`, body)
}

export function deleteHandler(username) {
  return credsRequest('DELETE', `/data-room/credentials/handlers/${encodeURIComponent(username)}`)
}

export function createVaultItem(section, body) {
  return credsRequest('POST', `/data-room/credentials/vault/${encodeURIComponent(section)}`, body)
}

export function updateVaultItem(section, itemId, body) {
  return credsRequest(
    'PATCH',
    `/data-room/credentials/vault/${encodeURIComponent(section)}/${encodeURIComponent(itemId)}`,
    body,
  )
}

export function deleteVaultItem(section, itemId) {
  return credsRequest(
    'DELETE',
    `/data-room/credentials/vault/${encodeURIComponent(section)}/${encodeURIComponent(itemId)}`,
  )
}
