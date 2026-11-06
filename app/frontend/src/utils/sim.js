/* 仿真页（列表 + 向导）的格式化。时间一律按阿布扎比本地时间显示 ——
   后端返回带 +04:00 的 ISO 串，但浏览器可能不在 +04，不能直接 toLocaleString()。 */
import { locale, t, tIn } from '../i18n'

export const LOCAL_TZ = 'Asia/Dubai'
export const LINE_ORDER = ['line1', 'line2_current', 'line2_planned']

const parts = (iso, opts, lang = locale.value) => {
  const f = new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en-US', { timeZone: LOCAL_TZ, ...opts })
  return Object.fromEntries(f.formatToParts(new Date(iso)).map(p => [p.type, p.value]))
}

/** 25 Sep 22:00 ／ 9月25日 22:00 */
export function fmtWhen(iso, lang = locale.value) {
  if (!iso) return '—'
  const p = parts(iso, { day: 'numeric', month: lang === 'zh' ? 'numeric' : 'short',
                         hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }, lang)
  return lang === 'zh' ? `${p.month}月${p.day}日 ${p.hour}:${p.minute}` : `${p.day} ${p.month} ${p.hour}:${p.minute}`
}

/** 甘特轴上的日期：THU 25 ／ 周四 25 */
export function fmtDay(date) {
  const p = parts(date, { weekday: 'short', day: 'numeric' })
  return locale.value === 'zh' ? `${p.weekday} ${p.day}` : `${p.weekday.toUpperCase()} ${p.day}`
}

/** 星期几（「7 more by Sunday」） */
export const fmtWeekday = iso => parts(iso, { weekday: 'long' }).weekday

/** 阿布扎比当地的日期零点（毫秒）。+04 全年无夏令时，直接按固定偏移算。 */
export const localMidnight = ms => {
  const OFF = 4 * 3600e3
  return Math.floor((ms + OFF) / 86400e3) * 86400e3 - OFF
}

const num = x => (x === null || x === undefined ? '' : String(Number(x)))

/** 态名的显示：态 key「L1」「L2+planned」显示为「Line1」「Line2 planned」（不写「+」，用户 2026-09-28 定）。态 key、后端 label / variant_label 里带的都走这里；
    只动独立的「L数字」及其后紧跟字母的「+」，「规划方案 + 本系统箱涵」这种连接号不动；已是 Line1 的再过一遍不变。
    key 本身是协议值（URL ?state=、接口），不改。
    防线口径列的值由后端算好落库（run.defence_basis，simulation.defence_basis()），前端不再拼 */
export const stateCode = s => (s == null ? s : String(s).replace(/\bL(\d)(?![\dA-Za-z])(\+(?=[A-Za-z]))?/g, (_, d, p) => `Line${d}${p ? ' ' : ''}`))

/** 雨型名。预设格子显示名义雨量（61 mm），不显示实际雨深 61.3。lang 不给跟界面语言。 */
export function stormLabel(run, lang = locale.value) {
  if (!run) return '—'
  if (run.rain_source === 'observed_replay') return run.replay_event_ref || run.rain_label
  const d = run.storm_preset_key ? run.storm_preset_key.split('mm_')[0] : num(run.depth_mm)
  return tIn(lang, 'sim.storm', { d, h: num(run.duration_h) })
}

export const statusLabel = (run, lang = locale.value) =>
  run.status === 'running'
    ? tIn(lang, 'sim.status.running', { p: Math.round(Number(run.progress_pct || 0)) })
    : tIn(lang, `sim.status.${run.status}`)

export const analysisLabel = (a, lang = locale.value) =>
  a.kind === 'gap' ? tIn(lang, 'sim.analysis.gap', { h: num(a.gap_hours) }) : tIn(lang, `sim.analysis.${a.kind}`)

export const fmtInt = n =>
  n === null || n === undefined ? '—' : Number(n).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-US')

/** 溢水点位置：代表格（最深格）格心的「纬度, 经度」，保留 5 位（约 1 m）；没有坐标给 null */
export const fmtLatLon = c =>
  c?.lat == null || c?.lon == null ? null : `${Number(c.lat).toFixed(5)}, ${Number(c.lon).toFixed(5)}`

/** km² 的数字部分：全市几十 km² 留 1 位，片区级（15 m 网格、零点几 km²）按量级多留，免得全显示成 0.1 / 0.2 */
export const fmtKm2 = km2 => {
  if (km2 === null || km2 === undefined) return '—'
  const x = Number(km2)
  return x.toFixed(x >= 10 ? 1 : x >= 1 ? 2 : 3)
}

/** 「61 and 96 mm in 2 h」：已算过的雨型按历时归组 */
export function modelledStorms(list) {
  const byH = new Map()
  for (const p of list) {
    const h = num(p.duration_h)
    if (!byH.has(h)) byH.set(h, [])
    byH.get(h).push(num(p.depth_mm))
  }
  return [...byH].map(([h, ds]) => t('sim.storm', { d: ds.join(t('sim.and')), h })).join(' · ')
}

/** 头像底色：按缩写固定取一种，同一个人在各处颜色一致 */
export const avatarClass = initials =>
  [...String(initials || '')].reduce((a, c) => a + c.charCodeAt(0), 0) % 2 ? 'c1' : ''
