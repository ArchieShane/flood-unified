import { locale, t } from '../i18n'

/** 后端地址。空串 = 同源（走 Vite 代理或生产反代），见 vite.config.js 的注释。 */
export const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

/** 后端 4xx/5xx 的 `detail` 可能是字符串，也可能是 FastAPI 校验错误的数组。 */
function detailMessage(payload, fallback) {
  const d = payload && payload.detail
  if (typeof d === 'string') return d
  if (d) return JSON.stringify(d)
  return fallback
}

/** 给每个请求挂上 `?lang=`。后端据此本地化它自己产出的那批展示文案
 *  （IDF 口径说明、limitations、caveats、出处标签、泵分档来源）。 */
function withLang(path) {
  // 调用方已点名语言（如任务列表固定 lang=en）就不再覆盖
  if (/[?&]lang=/.test(path)) return path
  return path + (path.includes('?') ? '&' : '?') + 'lang=' + locale.value
}

async function request(path, init) {
  let res
  try {
    res = await fetch(`${API_BASE}${withLang(path)}`, init)
  } catch (e) {
    // 分离之后后端是另一个进程，连不上是常态错误，值得单独说清楚
    throw new Error(t('api.unreachable', { url: (API_BASE || t('api.sameOrigin')) + path, msg: e.message }))
  }
  const payload = await res.json().catch(() => null)
  if (!res.ok) throw new Error(detailMessage(payload, res.statusText))
  return payload
}

export const get = path => request(path, { method: 'GET' })

const send = method => (path, body) =>
  request(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const post = send('POST')
export const put = send('PUT')
export const patch = send('PATCH')
/** DELETE 成功是 204 无返回体，request 里的 json() 失败会落成 null。 */
export const del = path => send('DELETE')(path)
