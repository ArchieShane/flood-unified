/* 中英双语。零依赖：locale 是一个 ref，t() 读它，
   故模板里的插值、computed、乃至 store 里的 getter 都会在切换语言时自动重算。

   为什么不上 vue-i18n：结论文案有一半在 store 的 getter 与 utils 的纯函数里
   （holdSummary / lineLabel / csv 导出），那些地方拿不到组件实例。
   一个全局 ref + 一个纯函数比装个库再绕 `i18n.global.t` 更直。 */
import { computed, ref } from 'vue'

import en from './en'
import simEn from './sim.en'
import simZh from './sim.zh'
import zh from './zh'

// 仿真页的文案单独成文件，挂在 `sim` 键下，免得两份千行大文件继续膨胀
const MESSAGES = { zh: { ...zh, sim: simZh }, en: { ...en, sim: simEn } }
export const LOCALES = [
  { key: 'zh', label: '中' },
  { key: 'en', label: 'EN' },
]

const STORAGE_KEY = 'fm.locale'

function initial() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && MESSAGES[saved]) return saved
  } catch {
    // 隐私模式下 localStorage 会抛，按默认语言走即可
  }
  return (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

export const locale = ref(initial())

/** 后端 `?lang=` 用的值 —— 与前端 locale 同一套 key。 */
export const apiLang = computed(() => locale.value)

export function setLocale(key) {
  if (!MESSAGES[key] || key === locale.value) return
  locale.value = key
  try {
    localStorage.setItem(STORAGE_KEY, key)
  } catch {
    // 存不下就只在本次会话生效
  }
  document.documentElement.lang = key === 'zh' ? 'zh-CN' : 'en'
}

const lookup = (dict, key) => key.split('.').reduce((o, k) => (o == null ? undefined : o[k]), dict)

/** `t('a.b', { n: 3 })`。缺键回落到中文，再缺就原样返回键名 ——
 *  宁可屏幕上出现一个刺眼的 key，也不要静默地少一句话。 */
export function t(key, params) {
  return tIn(locale.value, key, params)
}

/** 按指定语言取文案，不跟界面语言走（如任务列表的行数据固定英文）。 */
export function tIn(lang, key, params) {
  let s = lookup(MESSAGES[lang] || MESSAGES[locale.value], key)
  if (s === undefined) s = lookup(MESSAGES.zh, key)
  if (s === undefined) return key
  if (typeof s !== 'string') return s
  if (!params) return s
  return s.replace(/\{(\w+)\}/g, (m, k) => (params[k] === undefined ? m : String(params[k])))
}

/** 取一整棵子树（数组或对象），例如 `tm('csv.pointHead')`。 */
export function tm(key) {
  const v = lookup(MESSAGES[locale.value], key)
  return v === undefined ? lookup(MESSAGES.zh, key) : v
}

export const isZh = () => locale.value === 'zh'

/** 组件里用：`const { t } = useI18n()`。t 本身是全局函数，这里只是顺手把 locale 递出去。 */
export function useI18n() {
  return { t, tm, locale, setLocale, isZh }
}

document.documentElement.lang = locale.value === 'zh' ? 'zh-CN' : 'en'
