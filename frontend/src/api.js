// 极简 API 封装：所有请求自动带上当前登录人的专属账号请求头。
// 一人一账号，服务端据 X-User-Login 识别操作者，杜绝共用账号/代签。

let currentLogin = localStorage.getItem('login') || ''

export function setLogin(login) {
  currentLogin = login
  if (login) localStorage.setItem('login', login)
  else localStorage.removeItem('login')
}
export function getLogin() {
  return currentLogin
}

async function request(method, path, body) {
  const headers = { 'Content-Type': 'application/json' }
  if (currentLogin) headers['X-User-Login'] = currentLogin
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error(data.message || `请求失败（${res.status}）`)
    err.code = data.code
    err.status = res.status
    throw err
  }
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b),
}
