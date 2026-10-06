// 轻量 API 客户端：令牌存 localStorage，请求自动带上，错误统一抛出
const TOKEN_KEY = 'guard_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request(method, path, body) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  let data = null
  const text = await res.text()
  if (text) {
    try { data = JSON.parse(text) } catch { data = { raw: text } }
  }
  if (!res.ok) {
    const err = (data && data.error) || { code: 'HTTP_ERROR', message: `请求失败 ${res.status}` }
    const e = new Error(err.message)
    e.code = err.code
    e.details = err
    e.status = res.status
    throw e
  }
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b ?? {}),
}

export function nonce() {
  if (crypto.randomUUID) return crypto.randomUUID()
  return 'n-' + Date.now() + '-' + Math.random().toString(16).slice(2)
}
