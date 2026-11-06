import { locale, t } from '../i18n'

/** 台账里「这一列没填」的哨兵值。
 *
 *  ⚠ 这是**协议值**，不是文案：后端 `registry.facets()` 生成它、
 *  `ScenarioRequest.matches()` 拿它比对筛选条件，两边逐字相等才筛得中。
 *  切英文时只换显示名（`critLabel`），这个串本身**永远不翻译**。 */
export const UNFILLED = '未填写'

export const fmt = (n, d = 0) =>
  n === null || n === undefined || Number.isNaN(n)
    ? '—'
    : Number(n).toLocaleString(locale.value === 'zh' ? 'zh-CN' : 'en-US', {
        maximumFractionDigits: d,
        minimumFractionDigits: d,
      })

/** Vue 的插值自带转义；这个只给仍需拼 HTML 串的地方（地图 popup / tooltip）用。 */
export const esc = s =>
  String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

/** 时间戳。两种语言同一种写法 —— 它进 CSV 与版本表，换个语言就换个格式没有好处。 */
export const nowStamp = () => {
  const d = new Date()
  const p = n => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  )
}

/** 台账里的英文枚举 → 当前语言的显示名。查不到就原样返回，不吞掉未知取值。 */
const pick = (ns, key) => {
  if (key === null || key === undefined || key === '') return key
  const v = t(`${ns}.${key}`)
  return v === `${ns}.${key}` ? key : v
}

/** 成因 / 严重度都可能是 `未填写` 哨兵 —— 那一档单独走 `crit.unfilled` 键，
 *  免得把协议值写进 messages 的键名里，也免得英文界面里漏出一个中文词。 */
export const causeLabel = k => (!k || k === UNFILLED ? t('crit.unfilled') : pick('cause', k))
export const muniLabel = k => pick('muni', k)
export const critLabel = k => (!k || k === UNFILLED ? t('crit.unfilled') : pick('crit', k))

/** 筛选字段名（提示条里「已撤掉 严重度 / 成因 筛选」那几个词）。 */
export const filterFieldLabel = f => t(`filterField.${f}`)
