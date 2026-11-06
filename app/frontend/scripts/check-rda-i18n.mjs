// 查 RDA 页的英文词典是否覆盖了页面里所有 tr('中文原串')：缺一条就退出码 1。
// 用法：node scripts/check-rda-i18n.mjs      （改了 views/RdaView.vue 里的中文原串后跑一次）
import { readFileSync } from 'node:fs'

const FILES = ['../src/views/RdaView.vue', '../src/components/rda/RdaPanels.vue', '../src/views/SimulationHome.vue', '../src/views/SimulationDetail.vue']
const view = FILES.map(f => readFileSync(new URL(f, import.meta.url), 'utf8')).join('\n')
const dictSrc = readFileSync(new URL('../src/i18n/rda.js', import.meta.url), 'utf8')

const used = new Set([...view.matchAll(/\btr\('((?:[^'\\]|\\.)*)'/g)].map(m => m[1]))
const keys = new Set([...dictSrc.matchAll(/^\s*'((?:[^'\\]|\\.)*)':/gm)].map(m => m[1]))
const miss = [...used].filter(k => /[一-鿿]/.test(k) && !keys.has(k))
if (miss.length) {
  console.error(`RDA 英文词典缺 ${miss.length} 条：\n` + miss.map(k => '  ' + k).join('\n'))
  process.exit(1)
}
console.log(`ok：页面 ${used.size} 条 tr() 全部有英文`)
