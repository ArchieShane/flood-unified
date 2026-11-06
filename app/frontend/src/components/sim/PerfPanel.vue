<script setup>
/* 效果分析页右侧面板（Figma「Performance Analysis」高保真）：既有（第一态）对方案态。
   数字全部来自 /states/perf（sim_perf.py），这里只套文案：
   - 摘要与建议是按数字套规则的模板，不是大模型生成，页面照实标注；
   - 对照表每态一列（Defence line 1 / 2 Current / 2 Planned / 2 Suggested / 2 Alternative），小字是相对 Line 1 的变化；
   - 峰值 / 面积只在各态共同演算的时段里取，时刻档（2 / 4 / 5 h）的数是同一时刻切片；
   - 服务水平要多场雨才定得出，照设计稿写「待定」；退水时间两态都未退干就写「未退干」，不出变化。
   - 对照表（2026-09-28 按用户要求由「人能看到的 / 管网能接住的」两张合成一张）只比四项：溢水量（井口溢到地表）、
     最大积水面积、最大积水深度、系统储水能力（管网 + 蓄水节点，交付统计值口径，见 sim_perf）；
   - Line 2 Suggested 列（Mussafah 是 L2+suggested：Parsons 之上的 6 座箱涵）默认全选即演算原值；一座不勾就不出，
     每多勾一座数字跟着变；全选 = 演算结果原样；部分勾选按**所选面积占比 s** 分摊该列相对其 parent 态（L2+planned）的效果，各项 = parent +（全选 − parent）× s，
     个数四舍五入；调蓄容量 / 峰值蓄量按所选各池如实相加。这是估算、不是演算（池间相互作用、选址差异都不在里面），
     表头与注脚都标「估算」。
   - 每座蓄水池可展开看配套设备（进水管 / 闸孔 / 溢流堰 / 排空泵，sim_perf 的 `devices`）：左边是 .inp 里的能力，
     右边是耦合报告里的演算表现；设备随所属池子一起勾选，列表下方按所选池子汇总件数与排空泵额定流量。 */
import { computed, ref, watch } from 'vue'

import { locale as lang, t } from '../../i18n'
import { fmtInt, stateCode } from '../../utils/sim'

const props = defineProps({
  data: { type: Object, default: null },
  error: { type: String, default: '' },
  h: { type: Number, default: null },
  rain: { type: String, default: '' },   // 降雨情景文案（如「61 mm in 2 h」），摘要第一句用
  hotspots: { type: Object, default: null },   // /hotspots：带 scopes 时出「口径」标签（C1）
})
const emit = defineEmits(['h'])

const d = computed(() => props.data)
const hrs = m => Math.round((m / 60) * 10) / 10
const v0 = x => fmtInt(Math.round(x))
const ha = m2 => (m2 / 1e4).toFixed(2)
const pct = (a, b) => (a > 0 && b != null ? (100 * (b - a)) / a : null)
const sign = x => (x > 0 ? '+' : x < 0 ? '−' : '±')

/** 变化列：{ txt, cls }；lowerBetter 决定红绿，storage 这类「多了是投入」的不上色 */
function chg(a, b, { kind = 'pct', unit = '', digits = 1, lowerBetter = true } = {}) {
  if (a == null || b == null) return { txt: '—', cls: '' }
  let x, txt
  if (kind === 'pct') {
    x = pct(a, b)
    if (x == null) return { txt: '—', cls: '' }
    txt = `${sign(x)}${Math.abs(x).toFixed(digits)}%`
  } else {
    x = b - a
    txt = `${sign(x)}${kind === 'int' ? fmtInt(Math.abs(Math.round(x))) : Math.abs(x).toFixed(digits)}${unit ? ` ${unit}` : ''}`
  }
  if (!/[1-9]/.test(txt)) return { txt: '0', cls: '' }   // 按显示精度舍入后是 0，不写「−0.00」
  return { txt, cls: lowerBetter == null ? 'neu' : (x < 0) === lowerBetter ? 'good' : 'bad' }
}

/** 对照表：列 = 各态（Defence line 1 / Defence line 2 Current / Defence line 2 Planned / Defence line 2 Suggested），小字是相对 Line 1 的变化。
 *  get(列) 取数；null 写「—」或 alt(列)；delta 选项同 chg，cmp = false 表示全程量不可比、不出变化。 */
const zeroless = c => (c.txt === '0' || c.txt === '—' ? null : c)   // 与 Line 1 相同就不写小字
/* ---------------- 蓄水池勾选 → 第四列 ---------------- */
const sugg = computed(() => d.value?.columns.find(c => c.column === 'line2_suggested' && c.new_storage?.length) || null)
const ponds = computed(() => sugg.value?.new_storage || [])
const picked = ref(new Set())  // 默认全选：第四列直接出 L2+suggested 演算值
watch(() => ponds.value.map(u => u.id).join(), ids => {
  picked.value = new Set(ids ? ids.split(',') : [])   // 换任务 / 换方案就回到全选
}, { immediate: true })
function toggle(id) {
  const n = new Set(picked.value)
  n.has(id) ? n.delete(id) : n.add(id)
  picked.value = n
}
const allPicked = computed(() => ponds.value.length > 0 && picked.value.size === ponds.value.length)
const toggleAll = () => { picked.value = new Set(allPicked.value ? [] : ponds.value.map(u => u.id)) }
const areaOf = ids => ponds.value.filter(u => ids.includes(u.id)).reduce((x, u) => x + (u.area_m2 || 0), 0)
const areaAll = computed(() => areaOf(ponds.value.map(u => u.id)))
const pickedArea = computed(() => areaOf([...picked.value]))

/* ---------------- 配套设备 ---------------- */
const open = ref(new Set())
function toggleOpen(id) {
  const n = new Set(open.value)
  n.has(id) ? n.delete(id) : n.add(id)
  open.value = n
}
const KINDS = ['conduit', 'orifice', 'weir', 'pump']
const f1 = x => (x == null ? '—' : (Math.round(x * 10) / 10).toLocaleString(lang.value === 'zh' ? 'zh-CN' : 'en-US'))
const f2 = x => (x == null ? '—' : Number(x).toFixed(2))
const clock = m => `${Math.floor(m / 60)}:${String(Math.round(m % 60)).padStart(2, '0')}`
const span = (xs, f = f1) => {
  const v = xs.filter(x => x != null)
  if (!v.length) return null
  const a = Math.min(...v), b = Math.max(...v)
  return a === b ? f(a) : `${f(a)}–${f(b)}`
}
const sizeOf = x => {
  if (x.size_m == null) return '—'
  if (x.shape === 'CIRCULAR' || x.shape === 'FORCE_MAIN') return `DN${Math.round(x.size_m * 1000)}`
  return x.size2_m ? `${f2(x.size_m)}×${f2(x.size2_m)} m` : `${f2(x.size_m)} m`
}
const uniq = xs => [...new Set(xs)]

/** 一座池子的配套：同类设备合并一行（泵逐台列），规格取 .inp、演算取报告 */
function groups(u) {
  const out = []
  for (const k of KINDS) {
    const xs = (u.devices || []).filter(x => x.kind === k)
    if (!xs.length) continue
    const flows = xs.map(x => x.sim?.max_flow_lps)
    const peak = span(flows)
    const moved = flows.some(q => q > 0)
    if (k === 'pump') {
      for (const x of xs) {
        const s = x.sim || {}
        const off = Math.max(s.off_curve_low_pct || 0, s.off_curve_high_pct || 0)
        out.push({ k, n: 1, id: x.id,
          spec: t('sim.perf.dev.pumpSpec', { q: f1(x.q_max_lps), to: x.to }),
          sim: s.startups ? t('sim.perf.dev.pumpSim', { pct: f1(s.utilized_pct), n: s.startups, v: v0(s.volume_m3 || 0) }) : t('sim.perf.dev.pumpOff'),
          warn: !s.startups, extra: off > 0 ? t('sim.perf.dev.offCurve', { pct: f1(off) }) : '' })
      }
      continue
    }
    const x = xs[0]
    const spec = k === 'conduit'
      ? t('sim.perf.dev.conduitSpec', { size: uniq(xs.map(sizeOf)).join(' / '), len: span(xs.map(y => y.length_m)), from: uniq(xs.map(y => y.from)).join(lang.value === 'zh' ? '、' : ', ') })
      : k === 'orifice'
        ? t('sim.perf.dev.orificeSpec', { size: uniq(xs.map(sizeOf)).join(' / '), type: t(`sim.perf.dev.types.${x.orifice_type}`) || x.orifice_type, cd: uniq(xs.map(y => y.cd)).join(' / ') })
        : t('sim.perf.dev.weirSpec', { len: span(xs.map(y => y.size2_m), f2), crest: span(xs.map(y => y.crest_m), f2), to: uniq(xs.map(y => y.to)).join(', ') })
    out.push({ k, n: xs.length, id: xs.length === 1 ? x.id : '', spec,
      sim: peak == null ? '' : moved ? t('sim.perf.dev.peak', { q: peak }) : t('sim.perf.dev.noFlow') })
  }
  return out
}
const ruleText = r => [...r.if, ...r.then].join(' ')

/** 一批池子的配套件数（按类型）＋排空泵额定合计，拼成一句；没有配套返回 '' */
function devItems(devs) {
  const items = KINDS.map(k => [k, devs.filter(x => x.kind === k).length]).filter(([, n]) => n)
    .map(([k, n]) => t('sim.perf.dev.sumItem', { k: t(`sim.perf.dev.${k}`), n }))
  const q = devs.filter(x => x.kind === 'pump').reduce((a, x) => a + (x.q_max_lps || 0), 0)
  if (q > 0) items.push(t('sim.perf.dev.pumpTotal', { q: f1(q) }))
  return items.join(' · ')
}
/** 所选池子的配套汇总：件数按类型，外加排空泵额定合计 */
const devSum = computed(() => {
  const sel = ponds.value.filter(u => picked.value.has(u.id))
  const items = devItems(sel.flatMap(u => u.devices || []))
  return items ? t('sim.perf.dev.sum', { n: sel.length, items }) : ''
})
const hasDevices = computed(() => ponds.value.some(u => u.devices?.length))

/** 按面积占比 s 分摊的 Suggested 列：形状同后端的列，数字是 parent +（全选 − parent）× s（c0 = parent 列） */
function estimate(c0, c, ids) {
  const s = areaAll.value > 0 ? areaOf(ids) / areaAll.value : 0
  if (s === 1) return c
  const lin = (a, b) => (a == null || b == null ? null : a + (b - a) * s)
  const cnt = (a, b) => (a == null || b == null ? null : Math.round(lin(a, b)))
  const sel = c.new_storage.filter(u => ids.includes(u.id))
  const sum = k => sel.reduce((x, u) => x + u[k], 0)
  const lin2 = (a, b, x, tot) => (a == null || b == null ? null : a + (b - a) * (tot > 0 ? x / tot : 0))
  const r0 = c0.recession
  const r1 = c.recession
  return {
    ...c,
    est: { n: ids.length, total: c.new_storage.length, pct: s * 100 },
    at_h: { reached: c.at_h.reached, surface_volume_m3: lin(c0.at_h.surface_volume_m3, c.at_h.surface_volume_m3),
      active: cnt(c0.at_h.active, c.at_h.active) },
    window: {
      peak_surface_volume_m3: lin(c0.window.peak_surface_volume_m3, c.window.peak_surface_volume_m3),
      max_depth_m: lin(c0.window.max_depth_m, c.window.max_depth_m),
      inundated_area_m2: lin(c0.window.inundated_area_m2, c.window.inundated_area_m2),
    },
    recession: { median_h: r0.median_h != null && r1.median_h != null ? lin(r0.median_h, r1.median_h) : s > 0 ? r1.median_h : r0.median_h },
    network: {
      ...c.network,
      flooded_node_count: cnt(c0.network.flooded_node_count, c.network.flooded_node_count),
      overflow_to_surface_m3: lin(c0.network.overflow_to_surface_m3, c.network.overflow_to_surface_m3),
      storage_capacity_m3: c0.network.storage_capacity_m3 + sum('capacity_m3'),
      storage_peak_m3: c0.network.storage_peak_m3 + sum('peak_m3'),
      // 系统储水能力含管网，不是各池容量直接相加：按所选池容量占比分摊 parent 与全选之差
      system_storage_m3: lin2(c0.network.system_storage_m3, c.network.system_storage_m3, sum('capacity_m3'), c.new_storage.reduce((x, u) => x + u.capacity_m3, 0)),
    },
  }
}

/* ---------------- 口径标签（C1 这类交付自带统计口径的任务） ---------------- */
const SCOPE_ORDER = ['domain', 'catchment', 'near_200m']   // 标签固定顺序，不跟接口键序
const scope = ref('domain')
const scopeTabs = computed(() => (props.hotspots?.scopes ? SCOPE_ORDER.filter(k => k in props.hotspots.scopes) : null))
watch(() => d.value?.base.key, () => (scope.value = 'domain'))
const inScope = computed(() => !!scopeTabs.value && scope.value !== 'domain')
const sc = c => props.hotspots?.scopes?.[scope.value]?.[c.key] || {}
const fmtHa = v => (Number(v) >= 10 ? Number(v).toFixed(1) : Number(v).toFixed(2))

/** 表里的列：第四列一座不勾就不出（口径视图不做蓄水池估算，取演算原值） */
const cols = computed(() => {
  const all = d.value?.columns || []
  if (inScope.value) return all
  if (!sugg.value) return all
  return all.flatMap(c => {
    if (c !== sugg.value) return [c]
    // 估算以该列叠在其上的那一态（parent，L2+suggested 是 L2+planned）为底，不是第一列
    return picked.value.size ? [estimate(all.find(x => x.key === c.parent) || all[0], c, [...picked.value])] : []
  })
})
const estCol = computed(() => cols.value.find(c => c.est) || null)
const estBase = computed(() => {
  const all = d.value?.columns || []
  const p = all.find(x => x.key === sugg.value?.parent) || all[0]
  return p ? t(`sim.perf.colNames.${p.column}`) : ''
})
function row(k, get, { fmt = v0, alt, delta, cmp = true, unit = '', sub, cls, title } = {}) {
  const c0 = cols.value[0]
  const a = c0 ? get(c0) : null
  return {
    k, unit, sub, title,
    cells: cols.value.map((c, i) => {
      const v = get(c)
      return {
        key: c.key,
        txt: v == null ? (alt ? alt(c) : '—') : fmt(v),
        cls,
        d: i === 0 || !delta || !cmp || v == null ? null : zeroless(chg(a, v, delta)),
      }
    }),
  }
}

/** 对照表：溢水量 / 最大积水面积 / 最大积水深度 / 系统储水能力，每态一列 */
const rows = computed(() => {
  if (!d.value) return []
  if (inScope.value) {
    const sub = t('sim.perf.scopeSub')
    return [
      row(t('sim.perf.scopeRows.ponding'), c => sc(c).ponding, { delta: {} }),
      row(t('sim.perf.scopeRows.overflow'), c => sc(c).overflow_m3, { unit: 'm³', delta: {} }),
      row(t('sim.perf.scopeRows.a10'), c => sc(c).area_ge_0_10_ha, { unit: 'ha', sub, fmt: fmtHa, delta: {} }),
      row(t('sim.perf.scopeRows.a30'), c => sc(c).area_ge_0_30_ha, { unit: 'ha', sub, fmt: fmtHa, delta: {} }),
      row(t('sim.perf.scopeRows.depth'), c => sc(c).max_depth_m, { unit: 'm', fmt: x => x.toFixed(2), delta: { kind: 'num', unit: 'm', digits: 2 } }),
      row(t('sim.perf.scopeRows.sysStorage'), c => c.network.system_storage_m3, { unit: 'm³', sub: t('sim.perf.scopeRows.sysStorageSub'), delta: { lowerBetter: false } }),
    ]
  }
  const cmp = d.value.network.comparable
  const tri = d.value.columns[0]?.window.basis === 'triangle'
  return [
    row(t('sim.perf.rows.overflow'), c => c.network.overflow_to_surface_m3, { unit: 'm³', sub: t('sim.perf.rows.overflowSub'), delta: {}, cmp }),
    row(t('sim.perf.rows.area'), c => c.window.inundated_area_m2, { unit: 'ha', sub: t(tri ? 'sim.perf.rows.areaSubTri' : 'sim.perf.rows.areaSub'), fmt: ha, delta: {} }),
    row(t('sim.perf.rows.depth'), c => c.window.max_depth_m, { unit: 'm', fmt: x => x.toFixed(2), delta: { kind: 'num', unit: 'm', digits: 2 } }),
    row(t('sim.perf.rows.sysStorage'), c => c.network.system_storage_m3, { unit: 'm³',
      sub: t(d.value.columns[0]?.network.system_storage_basis === 'delivery' ? 'sim.perf.rows.sysStorageSub' : 'sim.perf.rows.sysStorageSubInp'),
      delta: { lowerBetter: false } }),
  ]
})

/* ---------------- 摘要与建议（按设计稿「AI SUMMARY / Recommended」模版） ----------------
   三段：① 既有（第一列）现状 ② 方案做了什么、少了多少、还剩多少（高亮）③ 症结在哪。
   建议第一条列方案新增的设备，后面按残留积水原因与调蓄满度给。
   数字一律取对照表里同一列（部分勾选时即估算列），不另算、不编服务水平。 */
const colName = c => t(`sim.perf.colNames.${c.column}`)
/** 方案列：取表里那一列（估算时跟着勾选走）；一座不勾时退回演算原值 */
const vsCol = computed(() => cols.value.find(c => c.key === d.value?.vs.key) || d.value?.columns.find(c => c.key === d.value?.vs.key) || null)
/** 方案相对第一列新增的调蓄：沿 parent 往上把每一态的新增池子都算上；Suggested 列只算勾上的 */
const addedPonds = computed(() => {
  const all = d.value?.columns || []
  const out = []
  for (let c = all.find(x => x.key === d.value?.vs.key); c && c !== all[0]; c = all.find(x => x.key === c.parent)) {
    const xs = c.new_storage || []
    out.push(...(c === sugg.value ? xs.filter(u => picked.value.has(u.id)) : xs))
  }
  return out
})

/** 方案态自己相对 parent 态新增的：调蓄池 + 全部设备（管段 / 泵，含池子配套之外的转输 / 取水泵）；Suggested 的池子只算勾上的。
    不沿 parent 往上累加：Planned 加的东西属于 Planned，不能记到 Suggested 头上 */
const own = computed(() => {
  const c = d.value?.columns.find(x => x.key === d.value?.vs.key)
  if (!c) return { ponds: [], links: [], pumps: null }
  const ponds = c === sugg.value ? c.new_storage.filter(u => picked.value.has(u.id)) : (c.new_storage || [])
  const drop = new Set((c.new_storage || []).filter(u => !ponds.includes(u)).flatMap(u => (u.devices || []).map(x => x.id)))
  return { ponds, links: (c.new_links || []).filter(x => !drop.has(x.id)), pumps: c.new_pumps || null }
})
/** 方案态自己新增设备的一句话：泵 / 进水管 / 闸孔 / 堰各几件 + 泵额定合计 */
const ownItems = computed(() => {
  const ps = own.value.pumps
  if (!ps?.length) return devItems(own.value.links)
  // 交付设施 GIS 的泵点：按类型（转输泵 / 地面取水泵…）合并，地面取水泵在二维取水、不在 .inp 里
  const by = new Map()
  for (const x of ps) {
    const g = by.get(x.type) || { n: 0, u: 0, q: 0 }
    g.n += 1; g.u += x.units || 1; g.q += x.q_lps || 0
    by.set(x.type, g)
  }
  const items = [...by].map(([k, g]) => t('sim.perf.dev.pumpGroup', { k, n: g.n, u: g.u }))
  items.push(t('sim.perf.dev.pumpTotalAll', { q: f1([...by.values()].reduce((a, g) => a + g.q, 0)) }))
  return items.join(' · ')
})

/** 把一句文案切成段，hi 里的占位符单独成段并带高亮 cls */
function rich(key, params, hi = {}) {
  const M = '\u0000'
  const p = { ...params }
  for (const k of Object.keys(hi)) p[k] = M + k + M
  return t(key, p).split(M).map((x, i) => (i % 2 ? hi[x] : { txt: x })).filter(x => x?.txt)
}

/** 方案效果的主指标：溢水量可比就用溢水量，否则退到积水面积 */
const effect = computed(() => {
  const c0 = cols.value[0], c1 = vsCol.value
  if (!c0 || !c1) return null
  const ov = d.value.network.comparable
  const get = ov ? c => c.network.overflow_to_surface_m3 : c => c.window.inundated_area_m2
  const a = get(c0), b = get(c1)
  if (a == null || b == null) return null
  return { m: ov ? 'ov' : 'area', a, b, pct: pct(a, b), left: ov ? v0(b) : ha(b) }
})

const summary = computed(() => {
  const x = d.value
  const c0 = cols.value[0], c1 = vsCol.value
  if (!x || !c0 || !c1) return []
  const out = []
  // ① 现状
  out.push(rich('sim.perf.sumNow', { base: colName(c0), rain: props.rain || '—',
    ov: v0(c0.network.overflow_to_surface_m3), d: c0.window.max_depth_m.toFixed(2), a: ha(c0.window.inundated_area_m2) }))
  // ② 方案效果 + 同一时刻切片
  const e = effect.value
  const p2 = []
  if (e && e.pct != null) {
    const eq = ownItems.value
    const lead = eq ? t('sim.perf.sumLeadSto', { vs: colName(c1), items: eq })
      : t('sim.perf.sumLeadNo', { vs: colName(c1) })
    const P = (e.m === 'ov' ? v0 : ha)(Math.abs(e.a - e.b))   // 写具体数值（m³ / ha），不写百分比
    if (e.pct < 0 && e.b > 0) p2.push(...rich('sim.perf.sumEffect', { lead }, {
      cut: { txt: t(`sim.perf.cut.${e.m}`, { pct: P }), cls: 'good' },
      left: { txt: t(`sim.perf.left.${e.m}`, { v: e.left }), cls: 'bad' } }))
    else if (e.pct < 0) p2.push(...rich('sim.perf.sumEffectDone', { lead }, { cut: { txt: t(`sim.perf.cut.${e.m}`, { pct: P }), cls: 'good' } }))
    else p2.push(...rich('sim.perf.sumEffectDone', { lead }, { cut: { txt: t(`sim.perf.up.${e.m}`, { pct: P }), cls: 'bad' } }))
  }
  if (!x.at_h.reached || !c1.at_h.reached) p2.push({ txt: t('sim.perf.sumNotReached', { h: x.h }) })
  else p2.push({ txt: t('sim.perf.sumAtH', { h: x.h, v0: v0(c0.at_h.surface_volume_m3), v1: v0(c1.at_h.surface_volume_m3),
    n0: c0.at_h.active, n1: c1.at_h.active ?? '—' }) })
  out.push(p2)
  // ③ 症结：演算末残留在哪一类 + 新增调蓄满没满
  const p3 = []
  const r = x.residual
  if (r && r.surface_volume_m3 > 0) {
    const [k, c] = Object.entries(r.by_class).sort((p, q) => q[1].volume_m3 - p[1].volume_m3)[0]
    const tot = Object.values(r.by_class).reduce((s, y) => s + y.volume_m3, 0) || 1
    p3.push({ txt: t('sim.perf.sumResidual', { end: hrs(r.end_minutes), v: v0(r.surface_volume_m3),
      pct: Math.round((100 * c.volume_m3) / tot), cls: t(`sim.residual.cls.${k}`) }) })
  }
  const fill = maxFill.value
  if (fill != null) p3.push({ txt: t(fill < 90 ? 'sim.perf.sumGapIntake' : 'sim.perf.sumGapCap', { fill: Math.round(fill) }) })
  if (p3.length) out.push(p3)
  return out
})
const maxFill = computed(() => {
  const fp = vsCol.value?.network?.storage_fill_pct || {}
  const xs = own.value.ponds.map(u => fp[u.id] ?? u.fill_pct).filter(v => v != null)
  return xs.length ? Math.max(...xs) : null
})

/** 建议 = 方案态自己新增的设备，别的不写：交付设施 GIS 有泵点就按类型逐类列（处数 / 台数 / 额定 / 演算抽水量），
    没有就按 .inp 新增管段的类型列；Suggested 的调蓄池另起一条 */
const recs = computed(() => {
  if (!d.value || !vsCol.value) return []
  const out = []
  const ps = own.value.pumps
  if (ps?.length) {
    const by = new Map()
    for (const x of ps) by.set(x.type, (by.get(x.type) || 0) + 1)
    const q = ps.reduce((a, x) => a + (x.q_lps || 0), 0), v = ps.reduce((a, x) => a + (x.volume_m3 || 0), 0)
    out.push({ t: t('sim.perf.dev.pumpAll', { n: ps.length }),
      ss: [[...by].map(([k, n]) => `${k} ${n}`).join(' · '), t('sim.perf.dev.rated', { q: f1(q) }), t('sim.perf.dev.pumped', { v: v0(v) })] })
  } else {
    for (const k of KINDS) {
      const xs = own.value.links.filter(x => x.kind === k)
      if (!xs.length) continue
      const ss = []
      const q = xs.reduce((a, x) => a + (x.q_max_lps || 0), 0)
      if (k === 'pump' && q > 0) ss.push(t('sim.perf.dev.rated', { q: f1(q) }))
      out.push({ t: t('sim.perf.dev.sumItem', { k: t(`sim.perf.dev.${k}`), n: xs.length }), ss })
    }
  }
  const pn = own.value.ponds
  if (pn.length) out.unshift({ t: t('sim.perf.recs.build.pond', { n: pn.length, cap: v0(pn.reduce((x, u) => x + u.capacity_m3, 0)) }), ss: [] })
  return out
})

/** 口径视图的一句话摘要：方案态相对第一态，溢水量与 ≥ 0.30 m 面积各变多少 */
const scopeSummary = computed(() => {
  if (!inScope.value || !d.value) return ''
  const a = sc(d.value.columns[0]), b = sc(d.value.columns.find(c => c.key === d.value.vs.key) || {})
  const ch = (x, y) => (x == null || y == null || !x ? null : `${sign(y - x)}${Math.abs((100 * (y - x)) / x).toFixed(0)}%`)
  return t('sim.perf.scopeSummary', {
    scope: t(`sim.levels.scope.${scope.value}`), base: stateCode(d.value.base.key), vs: stateCode(d.value.vs.key),
    ov0: a.overflow_m3 == null ? '—' : v0(a.overflow_m3), ov1: b.overflow_m3 == null ? '—' : v0(b.overflow_m3), ovc: ch(a.overflow_m3, b.overflow_m3) || '—',
    a0: a.area_ge_0_30_ha == null ? '—' : fmtHa(a.area_ge_0_30_ha), a1: b.area_ge_0_30_ha == null ? '—' : fmtHa(b.area_ge_0_30_ha), ac: ch(a.area_ge_0_30_ha, b.area_ge_0_30_ha) || '—',
    d0: a.max_depth_m == null ? '—' : f2(a.max_depth_m), d1: b.max_depth_m == null ? '—' : f2(b.max_depth_m),
  })
})

const foot = computed(() => {
  const x = d.value
  if (!x) return ''
  const c1 = vsCol.value
  return [t('sim.perf.foot.from', { vs: stateCode(x.vs.key), rain: props.rain || '—' }),
    t('sim.perf.foot.vs', { base: colName(cols.value[0] || x.columns[0]), h: x.h }),
    c1?.est ? t('sim.perf.est', { n: c1.est.n, total: c1.est.total }) : null,
    t('sim.perf.foot.review')].filter(Boolean)
})

const note = computed(() => {
  const x = d.value
  if (!x) return ''
  const w = hrs(x.common_end_minutes)
  const ends = x.columns.map(c => `${stateCode(c.key)} ${hrs(c.network.sim_end_minutes)} h`).join(lang.value === 'zh' ? '、' : ', ')
  const base = t(`sim.perf.colNames.${x.columns[0].column}`)
  return t('sim.perf.note', { w, ends, base }) +
    ' ' + t(x.network.comparable ? 'sim.perf.noteOk' : 'sim.perf.noteNc', { w }) +
    (estCol.value ? ' ' + t('sim.perf.estNote', { base: estBase.value, col: t('sim.perf.colNames.line2_suggested'), pct: estCol.value.est.pct.toFixed(1) }) : '')
})
</script>

<template>
  <aside class="sim-card ro pf">
    <div class="pf-head">
      <h2 class="ro-title">{{ t('sim.perf.crumb') }}</h2>
      <div v-if="d && !inScope" class="pf-seg" role="tablist">
        <button v-for="x in d.checkpoints_h" :key="x" role="tab" :class="{ on: x === d.h }" :aria-selected="x === d.h"
                @click="emit('h', x)">{{ x }} h</button>
      </div>
    </div>

    <div v-if="scopeTabs" class="pf-scope" role="tablist" :title="t('sim.levels.scope.tip')">
      <button v-for="k in scopeTabs" :key="k" role="tab" :class="{ on: k === scope }" :aria-selected="k === scope"
              @click="scope = k">{{ t(`sim.levels.scope.${k}`) }}</button>
    </div>

    <p v-if="error" class="pf-err">{{ error }}</p>
    <p v-else-if="!d" class="ro-note">{{ t('sim.perf.loading') }}</p>
    <template v-else>
      <p v-if="d.vs.same_as" class="pf-warn">{{ t('sim.perf.sameAs', { vs: stateCode(d.vs.key), as: stateCode(d.vs.same_as) }) }}</p>

      <section v-if="inScope" class="ro-sec pf-sumsec">
        <h3>{{ t('sim.perf.summary') }}</h3>
        <p class="pf-sum">{{ scopeSummary }}</p>
        <p class="ro-note">{{ t(`sim.levels.scopeNote.${scope}`) }} {{ t('sim.perf.scopeOnly') }}</p>
      </section>

      <section v-if="!inScope" class="ro-sec pf-sumsec">
        <h3>{{ t('sim.perf.summary') }}</h3>
        <p v-for="(ps, i) in summary" :key="i" class="pf-sum">
          <template v-for="(s, j) in ps" :key="j"><b v-if="s.cls" :class="s.cls">{{ s.txt }}</b><template v-else>{{ s.txt }}</template></template>
        </p>
      </section>

      <section v-if="!inScope && recs.length" class="pf-rec">
        <h3>{{ t('sim.perf.rec') }}</h3>
        <ol>
          <li v-for="(r, i) in recs" :key="i">
            <span class="no">{{ i + 1 }}</span>
            <div>
              <div class="tt">{{ r.t }}</div>
              <div class="ss"><template v-for="(x, j) in r.ss" :key="j"><span v-if="j" class="sep" />{{ x }}</template></div>
            </div>
          </li>
        </ol>
      </section>
      <p v-if="!inScope" class="pf-foot">ⓘ <template v-for="(x, j) in foot" :key="j"><span v-if="j" class="sep" />{{ x }}</template></p>

      <section v-if="sugg && !inScope" class="ro-sec pf-ponds">
        <h3>{{ t('sim.perf.ponds', { col: t('sim.perf.colNames.line2_suggested'), key: stateCode(sugg.key) }) }}</h3>
        <p class="pf-hint">{{ t('sim.perf.pondsHint', { base: stateCode(sugg.parent || cols[0]?.key) }) }}</p>
        <label class="pf-pond all">
          <input type="checkbox" :checked="allPicked" :indeterminate.prop="picked.size > 0 && !allPicked" @change="toggleAll" />
          <span class="id">{{ t('sim.perf.all') }}</span>
          <span class="a">{{ t('sim.perf.pondArea') }}</span><span class="c">{{ t('sim.perf.pondCap') }}</span><span class="c">{{ t('sim.perf.pondPeak') }}</span>
          <span />
        </label>
        <template v-for="u in ponds" :key="u.id">
          <label class="pf-pond">
            <input type="checkbox" :checked="picked.has(u.id)" @change="toggle(u.id)" />
            <span class="id">{{ u.id }}</span>
            <span class="a">{{ v0(u.area_m2) }} m²</span>
            <span class="c">{{ v0(u.capacity_m3) }} m³</span>
            <span class="c">{{ v0(u.peak_m3) }} m³</span>
            <button v-if="u.devices?.length" type="button" class="pf-caret" :class="{ on: open.has(u.id) }"
                    :title="t('sim.perf.dev.expand')" :aria-expanded="open.has(u.id)" @click.prevent.stop="toggleOpen(u.id)">▸</button>
            <span v-else />
          </label>
          <div v-if="open.has(u.id)" class="pf-dev" :class="{ off: !picked.has(u.id) }">
            <div class="pf-dev-row">
              <div class="k">{{ t('sim.perf.dev.body') }} <small>{{ u.id }}</small></div>
              <div class="sp">{{ t('sim.perf.dev.bodySpec', { d: f2(u.depth_m) }) }}</div>
              <div v-if="u.sim_max_depth_m != null" class="sm">{{ t('sim.perf.dev.bodySim', { d: f2(u.sim_max_depth_m), q: f1(u.sim_max_inflow_lps), t: clock(u.sim_max_inflow_minutes || 0) }) }}</div>
            </div>
            <div v-for="g in groups(u)" :key="g.k + g.id" class="pf-dev-row">
              <div class="k">{{ t(`sim.perf.dev.${g.k}`) }}<template v-if="g.n > 1"> ×{{ g.n }}</template> <small v-if="g.id">{{ g.id }}</small></div>
              <div class="sp">{{ g.spec }}</div>
              <div v-if="g.sim" class="sm" :class="{ warn: g.warn }">{{ g.sim }}</div>
              <div v-if="g.extra" class="sm warn">⚠ {{ g.extra }}</div>
            </div>
            <details v-if="u.rules?.length" class="pf-rules">
              <summary>{{ t('sim.perf.dev.rules', { n: u.rules.length }) }}</summary>
              <div v-for="r in u.rules" :key="r.name" class="r"><b>{{ r.name }}</b> {{ ruleText(r) }}</div>
            </details>
          </div>
        </template>
        <p class="pf-share">{{ t('sim.perf.share', { n: picked.size, total: ponds.length, a: v0(pickedArea), pct: areaAll ? ((100 * pickedArea) / areaAll).toFixed(1) : '0' }) }}</p>
        <p v-if="devSum" class="pf-share">{{ devSum }}</p>
        <p v-if="hasDevices" class="pf-hint pf-devnote">{{ t('sim.perf.dev.note', { w: hrs(d.common_end_minutes) }) }}</p>
      </section>

      <section class="ro-sec">
        <h3>{{ stateCode(d.base.key) }} vs {{ stateCode(d.vs.key) }}</h3>
        <table class="pf-tbl">
          <thead>
            <tr>
              <th>{{ t('sim.perf.cols.param') }}</th>
              <th v-for="c in cols" :key="c.key" :class="{ on: c.key === d.vs.key }" :title="stateCode(c.label)">
                {{ t(`sim.perf.colNames.${c.column}`) }}<small>{{ stateCode(c.key) }}</small>
                <small v-if="c.est" class="est">{{ t('sim.perf.est', { n: c.est.n, total: c.est.total }) }}</small>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.k" :title="r.title || ''">
              <td class="lb">{{ r.k }}<small v-if="r.unit || r.sub">{{ [r.unit, r.sub].filter(Boolean).join(' · ') }}</small></td>
              <td v-for="(c, i) in r.cells" :key="c.key" :class="[c.cls, { on: cols[i].key === d.vs.key }]">
                <span class="v">{{ c.txt }}</span>
                <span v-if="c.d" class="dl" :class="c.d.cls">{{ c.d.txt }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="inScope" class="ro-note">{{ t('sim.levels.scopeNote.common') }}</p>
        <p v-else class="ro-note">{{ note }}</p>
      </section>
    </template>

  </aside>
</template>

<style scoped>
.pf-scope { display: flex; flex-wrap: wrap; gap: 4px; margin: -6px 0 16px; }
.pf-scope button { padding: 5px 11px; font-size: 12.5px; border-radius: 14px; border: 1px solid var(--line-2); background: transparent; color: var(--fg-2); cursor: pointer; }
.pf-scope button.on { background: var(--blue, #4c8dff); border-color: transparent; color: #fff; }
/* 外框与 RunOverview 同款（那边是 scoped，这里照抄） */
.ro { padding: 22px 22px 18px; display: flex; flex-direction: column; background: linear-gradient(180deg, #1f2124, #17181b); }
.ro-title { font-size: 20px; font-weight: 500; margin: 0; }
.ro-sec { margin-top: 18px; }
.ro-sec h3, .pf-rec h3 { font-size: 15px; font-weight: 700; margin: 0 0 10px; }
.ro-note { color: var(--muted); font-size: 11.5px; margin: 10px 0 0; line-height: 1.5; }
.ro-gap { margin-top: 18px; height: 44px; border: 0; border-radius: 8px; background: #4aa3ea; color: #fff;
  font-size: 15px; font-weight: 500; cursor: pointer; }
.pf-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding-bottom: 14px;
  border-bottom: 1px solid var(--line-2); }
.pf-seg { display: inline-flex; background: rgba(255, 255, 255, 0.05); border-radius: 6px; padding: 2px; }
.pf-seg button { border: 0; background: none; color: var(--fg-2); font: inherit; font-size: 12px; padding: 4px 10px;
  border-radius: 4px; cursor: pointer; font-variant-numeric: tabular-nums; }
.pf-seg button.on { background: var(--blue-bg); color: var(--blue-lt); }
.pf-sumsec h3 { text-transform: uppercase; letter-spacing: 0.02em; }
.pf-sum { font-size: 14px; line-height: 1.55; margin: 0 0 12px; color: var(--fg); }
.pf-sum b { font-weight: 600; }
.pf-sum b.good { color: var(--blue-lt); }
.pf-sum b.bad { color: #f08a8d; }
.pf-rec .sep, .pf-foot .sep { display: inline-block; width: 1px; height: 0.9em; background: currentColor; opacity: 0.5;
  margin: 0 8px; vertical-align: -0.1em; }
.pf-rec { margin-top: 6px; padding: 18px 20px; border-radius: 16px;
  background: linear-gradient(135deg, rgba(71, 134, 201, 0.22), rgba(71, 134, 201, 0.06)); border: 1px solid rgba(71, 134, 201, 0.25); }
.pf-rec ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.pf-rec li { display: flex; gap: 10px; align-items: flex-start; }
.pf-rec .no { flex: none; width: 20px; height: 20px; border-radius: 50%; background: rgba(255, 255, 255, 0.1);
  font-size: 11px; display: flex; align-items: center; justify-content: center; margin-top: 1px; }
.pf-rec .tt { font-size: 14px; line-height: 1.45; }
.pf-rec .ss { font-size: 12px; color: var(--muted); margin-top: 3px; }
.pf-foot { color: var(--faint); font-size: 11.5px; margin: 10px 0 0; }
.pf-tbl { width: 100%; border-collapse: collapse; font-size: 12px; table-layout: fixed;
  background: rgba(255, 255, 255, 0.03); border-radius: 8px; overflow: hidden; }
.pf-tbl th { font-weight: 500; color: var(--fg-2); text-align: right; padding: 10px 6px 6px; font-size: 11.5px;
  line-height: 1.25; vertical-align: bottom; }
.pf-tbl th small { display: block; color: var(--faint); font-weight: 400; font-size: 10.5px; margin-top: 2px; }
.pf-tbl th:first-child { text-align: left; width: 28%; padding-left: 10px; color: var(--muted); font-weight: 400; }
.pf-tbl th.on { color: var(--blue-lt); }
.pf-tbl td { padding: 8px 6px; text-align: right; border-top: 1px solid var(--line-2); font-variant-numeric: tabular-nums;
  vertical-align: top; }
.pf-tbl td.on { background: rgba(71, 134, 201, 0.08); }
.pf-tbl td.lb { text-align: left; color: var(--fg); padding-left: 10px; }
.pf-tbl td.lb small { display: block; color: var(--faint); font-size: 10.5px; }
.pf-tbl .v { display: block; }
.pf-tbl .dl { display: block; font-size: 10.5px; color: var(--muted); }
.pf-tbl .dl.good { color: var(--green); }
.pf-tbl .dl.bad { color: var(--red); }
.pf-tbl .dl.neu { color: var(--blue-lt); }
.pf-tbl td.pend .v { color: var(--amber); }
.pf-tbl th small.est { color: var(--amber); }
.ro-gap:disabled { opacity: 0.45; cursor: default; }
.pf-hint { color: var(--muted); font-size: 12px; line-height: 1.5; margin: 0 0 8px; }
.pf-pond { display: grid; grid-template-columns: 20px minmax(0, 1fr) 80px 80px 80px 18px; align-items: center; gap: 6px;
  font-size: 12.5px; line-height: 30px; padding: 0 6px; border-radius: 4px; cursor: pointer; font-variant-numeric: tabular-nums; }
.pf-pond:hover { background: rgba(255, 255, 255, 0.04); }
.pf-pond input { accent-color: var(--blue); margin: 0; }
.pf-pond .a, .pf-pond .c { text-align: right; color: var(--fg-2); }
.pf-pond.all { color: var(--muted); border-bottom: 1px solid var(--line-2); border-radius: 0; }
.pf-pond.all .a, .pf-pond.all .c { color: var(--muted); font-size: 11.5px; }
.pf-share { color: var(--fg-2); font-size: 12px; margin: 8px 0 0; }
.pf-caret { border: 0; background: none; color: var(--muted); font-size: 11px; padding: 0; cursor: pointer;
  transition: transform 0.15s; line-height: 1; }
.pf-caret.on { transform: rotate(90deg); color: var(--blue-lt); }
.pf-dev { margin: 2px 0 8px 26px; padding: 8px 10px; border-left: 2px solid var(--line-2);
  background: rgba(255, 255, 255, 0.025); border-radius: 0 6px 6px 0; display: flex; flex-direction: column; gap: 8px; }
.pf-dev.off { opacity: 0.5; }
.pf-dev-row { font-size: 12px; line-height: 1.45; }
.pf-dev-row .k { color: var(--fg); font-weight: 500; }
.pf-dev-row .k small { color: var(--faint); font-weight: 400; margin-left: 4px; font-size: 11px; }
.pf-dev-row .sp { color: var(--fg-2); }
.pf-dev-row .sm { color: var(--muted); font-size: 11.5px; }
.pf-dev-row .sm.warn { color: var(--amber); }
.pf-rules summary { color: var(--muted); font-size: 11.5px; cursor: pointer; }
.pf-rules .r { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10.5px; color: var(--fg-2);
  line-height: 1.5; margin-top: 4px; word-break: break-word; }
.pf-rules .r b { color: var(--fg); font-weight: 500; }
.pf-devnote { margin-top: 6px; font-size: 11.5px; }
.pf-warn { color: var(--amber); font-size: 12.5px; margin: 12px 0 0; }
.pf-err { color: var(--red); font-size: 12.5px; margin: 12px 0 0; }
</style>
