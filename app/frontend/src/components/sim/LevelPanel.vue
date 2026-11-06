<script setup>
/* 任务详情右侧总览：四项 —— 溢水量 / 最大积水面积 / 最大积水深度 / 系统储水能力（用户 2026-09-28 定），外加峰值水深前 10；
   切右上角的防线（L 等级）标签，就出到该等级为止的对比：L1 只一行；L2 = L1 vs L2；规划态 = L1 / L2 / 沿 parent 链上的各规划态 / 该态
   （L2 suggested 叠在 L2 planned 上，出四行）。
   右上角的变化量一律是「当前等级比 L1」。取数分三种任务：
   · 多态任务（run.states，Mussafah）：全部取效果分析 /states/perf 的 columns（每态一份，与 vs 无关）。
     面积、水深取各态共同演算窗内的峰值（窗外的演算时长不同，全程量比不了）；溢水量是耦合 SWMM 全程量，
     储水能力取 system_storage_m3（管网 + 蓄水节点的几何容积，与效果分析页同口径），
     network.comparable 为假时照实标出。
   · 全市任务（run.has_l2，#100 / #101 / #102）：L1 的面积、水深取 /layers，溢水量取 /l2 的 l1_network；
     L2 没有二维演算，溢水量、面积、水深按 L1 × (1 − 减少比例) 折算（先取到显示精度再折算，屏幕上能复算），
     储水能力取 /l2 的 storage（与雨无关，三份都有）：L1 按本交付 .inp 自算管容，L2 = L1 + 一维报告里塘的增量（借用）。
   · 其余单一交付：只有 /layers 的面积、水深，溢水量与储水能力不出数。
   地图在时间过程下（播放 / 拖动，`now` 非空）四项换成本帧的当前值：已出现的溢水点、累计溢水量、同刻积水面积、
   本帧最深（储水能力按几何算，不随时刻变，照旧）。看全程（最大水深）时四项前面也出溢水点个数（/layers/ponding 的片区数，
   与地图「溢水点（N）」同一个数）：当前态取地图已取回的 ponding，多态任务的其余态各取一次；全市第二道沿用第一道。多态任务各态按同一时刻取各自的 /layers/timeline，
   某态没演算到这一时刻就不出数；全市任务 L1 取地图的 now，L2 仍按比例折算、溢水点沿用 L1。 */
import { computed, ref, watch } from 'vue'

import CityBalance from './CityBalance.vue'
import { fetchSimulationL2, fetchSimulationPerf, fetchSimulationPonding, fetchSimulationTimeline } from '../../api'
import { locale, t } from '../../i18n'
import { fmtInt, fmtKm2, fmtLatLon, stateCode, stormLabel } from '../../utils/sim'

const props = defineProps({
  run: { type: Object, required: true },       // 总览用的 run（多态时防线已换成当前态的）
  states: { type: Array, default: null },
  stateKey: { type: String, default: null },
  l2View: { type: Boolean, default: false },   // 全市任务的第二道现状视图（?line=l2）
  meta: { type: Object, default: null },       // /layers
  noOverflow: { type: Boolean, default: false },   // RD-A、全市任务：不出溢水点 / 溢水量两行（全市只留总积水量 / 最大面积 / 最大深度 / 储水能力）
  noPondPoints: { type: Boolean, default: false }, // 全市一二维（#1068）：模拟积水点已替代溢水点，只藏「溢水点」一行与前 10 名
  ponding: { type: Object, default: null },    // 地图已取回的 /layers/ponding（多态时是当前态自己的）
  active: { type: Number, default: null },
  now: { type: Object, default: null },        // 地图时间过程下的本帧当前值（ResultMap 的 now）；null = 看全程
  cityGe: { type: Boolean, default: false },   // 全市任务才用「L2 排掉 35%」的折算；区域推演（RD-A）两态都是模型真值
  volGe: { type: Object, default: null },      // /equipment 的 ponding_ge：{ threshold_m, volume_m3: { 态 → m³ } }
  hotspots: { type: Object, default: null },   // /hotspots：交付自带的积水点（有的任务才有）；带 scopes 时右上出「口径」标签
})
const emit = defineEmits(['perf', 'focus'])

/* ---------------- 取数 ---------------- */
const perf = ref(null)
const l2 = ref(null)
const err = ref('')
const tls = ref({})   // 多态：态 → /layers/timeline（播放时才取）
const pondN = ref({}) // 多态：态 → 全程溢水点个数（非当前态才取）
let seq = 0
async function load() {
  const my = ++seq
  err.value = ''
  perf.value = l2.value = null
  tls.value = {}
  pondN.value = {}
  try {
    if (props.states) {
      const d = await fetchSimulationPerf(props.run.id, null, null)
      if (my === seq) perf.value = d
    } else if (props.run.has_l2) {
      const d = await fetchSimulationL2(props.run.id)
      if (my === seq) l2.value = d
    }
  } catch (e) {
    if (my === seq) err.value = e.message
  }
}
watch([() => props.run.id, locale], load, { immediate: true })

/* ---------------- 四项 ---------------- */
const fmtClock = min => `T+${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, '0')}`
const PONDING = { k: 'ponding', u: '', better: 'lower', fmt: v => fmtInt(Math.round(v)) }
const SPEC = [
  { k: 'overflow', u: 'm³', better: 'lower', fmt: v => fmtInt(Math.round(v)) },
  { k: 'volume', u: 'm³', better: 'lower', fmt: v => fmtInt(Math.round(v)) },   // 总积水量：地表积水量的峰值（逐帧 Σ水深×面积 的最大值）
  { k: 'area', u: 'km²', better: 'lower', fmt: v => fmtKm2(v) },
  { k: 'depth', u: 'm', better: 'lower', fmt: v => Number(v).toFixed(2) },
  { k: 'storage', u: 'm³', better: 'higher', fmt: v => fmtInt(Math.round(v)) },
]
const SPEC_GE = { k: 'volGe', u: 'm³', better: 'lower', fmt: v => fmtInt(Math.round(v)) }   // 积水量（水深 ≥ 15 cm 的部分，峰值）
const COLORS = ['var(--red)', 'var(--amber)', 'var(--blue-lt)']
const lineName = k => t(`sim.detail.lineTab.${k}`)

/** 多态：perf.columns 一态一份 */
const colVals = c => ({
  overflow: c.network?.overflow_to_surface_m3 ?? null,
  volume: c.window?.peak_surface_volume_m3 ?? null,
  area: c.window?.inundated_area_m2 == null ? null : c.window.inundated_area_m2 / 1e6,
  depth: c.window?.max_depth_m ?? null,
  storage: c.network?.system_storage_m3 ?? null,   // 管网 + 蓄水节点，与效果分析页同口径（只取调蓄单元的话 L1 没有塘就是 0）
})
/** 某态在同一时刻（按分钟对齐）的当前值；没演算到这一时刻、或 timeline 还没到，给 null */
const atTime = (tl, tm) => {
  const i = tl ? tl.time_minutes.findIndex(x => Math.abs(x - tm) < 1e-6) : -1
  if (i < 0) return { ponding: null, overflow: null, volume: null, area: null, depth: null }
  const a = tl.wet_area_m2?.[i]
  return { ponding: tl.ponding_lit?.[i] ?? null, overflow: tl.overflow_cum_m3?.[i] ?? null, volume: tl.volume_m3?.[i] ?? null,
    area: a == null ? null : a / 1e6, depth: tl.max_depth_m?.[i] ?? null }
}
/** 全程溢水点个数：地图已取回的 ponding 的片区数 */
const pondCount = p => (p?.clusters ? p.clusters.length : null)
const nowVals = n => ({ ponding: n.ponding, overflow: n.overflow_m3, volume: n.volume_m3 ?? null, area: n.area_m2 == null ? null : n.area_m2 / 1e6, depth: n.depth_m })

const stateCols = computed(() => {
  const cols = perf.value?.columns
  if (!cols) return null
  const cur = props.states.find(s => s.key === props.stateKey) || props.states[0]
  const pick = [cols.find(c => c.column === 'line1')]
  if (cur.defence_line_key !== 'line1') pick.push(cols.find(c => c.column === 'line2_current'))
  if (cur.defence_line_key === 'line2_planned') {
    // 规划态沿 parent 往上补齐叠在 L2 之上的各态（L2 suggested 叠在 L2 planned 上，四行都出）
    const chain = []
    for (let c = cols.find(x => x.key === cur.key); c && c.column !== 'line2_current' && c.column !== 'line1'
      && !chain.includes(c); c = cols.find(x => x.key === c.parent)) chain.unshift(c)
    pick.push(...chain)
  }
  return pick.filter(Boolean)
})
// 播放时才取各态的逐帧当前值，取过的留着
watch(() => [props.now != null, stateCols.value?.map(c => c.key).join()], ([on]) => {
  if (!on || !stateCols.value) return
  const id = props.run.id
  for (const c of stateCols.value) {
    if (c.key in tls.value) continue
    tls.value = { ...tls.value, [c.key]: null }
    fetchSimulationTimeline(id, c.key)
      .then(x => { if (props.run.id === id) tls.value = { ...tls.value, [c.key]: x } })
      .catch(() => {})
  }
})
// 全程溢水点：非当前态各取一次 /layers/ponding
watch(() => [props.now == null, stateCols.value?.map(c => c.key).join()], ([on]) => {
  if (!on || !stateCols.value || props.noPondPoints || props.noOverflow) return    // 溢水点不出，就不去取它们
  const id = props.run.id
  const curKey = props.stateKey || props.states[0].key
  for (const c of stateCols.value) {
    if (c.key === curKey || c.key in pondN.value) continue
    pondN.value = { ...pondN.value, [c.key]: null }
    fetchSimulationPonding(id, c.key)
      .then(x => { if (props.run.id === id) pondN.value = { ...pondN.value, [c.key]: pondCount(x) } })
      .catch(() => {})
  }
})
const stateRows = computed(() => stateCols.value?.map(c => {
  const cur = c.key === (props.stateKey || props.states[0].key)
  const v = !props.now ? { ...colVals(c), ponding: cur ? pondCount(props.ponding) : pondN.value[c.key] ?? null }
    : { ...(cur ? nowVals(props.now) : atTime(tls.value[c.key], props.now.time_minutes)), storage: colVals(c).storage }
  return { key: c.key, label: stateCode(c.key), v, sameAs: c.same_as, peak: c.window?.basis === 'triangle' }
}) ?? null)

/** /layers 的面积：sww 交付有「同一时刻」的最大积水面积（三角形 ≥ 2 cm，交付口径），别的交付只有全程 ≥ 1 cm 的格并集 */
const layerArea = r => {
  const a = r.peak_inundated_area_m2 ?? r.inundated_area_ge_0_01m2
  return a == null ? null : a / 1e6
}

/** 全市：L1 实算，L2 按比例折算（储水能力除外） */
const keep = computed(() => (l2.value?.estimate ? 1 - l2.value.estimate.reduction_ratio : null))
const storageCmp = computed(() => l2.value?.storage || null)
const cityRows = computed(() => {
  const r = props.meta?.results
  if (!l2.value || !r) return null
  const shown = (k, v) => (v == null ? null : Number(SPEC.find(x => x.k === k).fmt(v).replace(/,/g, '')))
  const n = props.now && nowVals(props.now)
  const l1 = {
    ponding: n ? n.ponding ?? null : pondCount(props.ponding),
    overflow: shown('overflow', n ? n.overflow : l2.value.l1_network?.overflow_to_surface_m3),
    volume: shown('volume', n ? n.volume : r.peak_surface_volume_m3),      // 总积水量：交付没有水量序列就是 null，整块不出
    area: shown('area', n ? n.area : layerArea(r)),
    depth: shown('depth', n ? n.depth : r.maximum_depth_m),
    storage: storageCmp.value?.l1 ?? null,
  }
  const rows = [{ key: 'l1', label: lineName('line1'), v: l1, peak: r.peak_inundated_area_m2 != null }]
  if (props.l2View) {
    const f = x => (x == null || keep.value == null ? null : x * keep.value)
    rows.push({
      key: 'l2', label: lineName('line2_current'),
      v: { ponding: l1.ponding, overflow: f(l1.overflow), volume: f(l1.volume), area: f(l1.area), depth: f(l1.depth), storage: storageCmp.value?.l2 ?? null },
    })
  }
  return rows
})

/** 单一交付：只有 /layers */
const plainRows = computed(() => {
  const r = props.meta?.results
  if (!r) return null
  return [{
    key: 'run', label: props.run.defence_line_label || '', peak: r.peak_inundated_area_m2 != null,
    v: props.now ? { ...nowVals(props.now), storage: null }
      : { ponding: pondCount(props.ponding), overflow: null, storage: null, volume: r.peak_surface_volume_m3 ?? null, area: layerArea(r), depth: r.maximum_depth_m ?? null },
  }]
})

const rows = computed(() => (props.states ? stateRows.value : props.run.has_l2 ? cityRows.value : plainRows.value))

const dimLabel = x => {
  if (x.k === 'volume' && volGe.value && !props.now) return t('sim.levels.dim.volumeSurf')   // 已有「总积水量（≥ 15 cm）」，这行改叫地表积水量，免得重名
  if (!props.now) return t(x.k === 'area' && rows.value[0].peak ? 'sim.levels.dim.areaPeak' : `sim.levels.dim.${x.k}`)
  if (x.k === 'storage') return t('sim.levels.dim.storage')
  const k = x.k === 'area' ? (props.now.area_basis === 'triangle' ? 'areaTri' : 'areaCell') : x.k
  return t(`sim.levels.dimNow.${k}`, { t: fmtClock(props.now.time_minutes) })
}
/** 一组指标 × 各态 → 总览的方框（数、条形、比 L1 的变化）。`rs` 每态一行：{ key, label, v: { 指标 k → 数 } } */
const build = (specs, rs0, labelOf) => (rs0 || []).length ? specs.map(x => {
  // 当前态恒为浅蓝；中间的规划态（L2 planned）用深蓝
  const rs = rs0.map((r, i, a) => ({ ...r, val: r.v[x.k],
    color: i === a.length - 1 && i >= 2 ? COLORS[2] : i >= 2 ? 'var(--blue)' : COLORS[i] }))
  const max = Math.max(0, ...rs.map(r => r.val ?? 0))
  const base = rs[0].val
  const cur = rs.at(-1).val
  const diff = rs.length < 2 || base == null || cur == null ? null : cur - base
  const good = diff == null || diff === 0 ? null : (x.better === 'lower') === diff < 0
  return {
    ...x,
    label: labelOf(x),
    cur: cur == null ? '—' : x.fmt(cur),
    rows: rs.length > 1 && rs.some(r => r.val != null) ? rs.map(r => ({ ...r, text: r.val == null ? '—' : x.fmt(r.val), pct: max ? ((r.val ?? 0) / max) * 100 : 0 })) : [],
    dv: diff == null ? null : `${diff > 0 ? '+' : diff < 0 ? '−' : '±'}${x.fmt(Math.abs(diff))}`,
    dp: diff == null || !base ? null : `${diff > 0 ? '+' : diff < 0 ? '−' : '±'}${Math.abs((diff / base) * 100).toFixed(1)}%`,
    good,
  }
}) : []

/* ---------------- 口径标签（C1 这类交付自带统计口径的多态任务） ---------------- */
const SCOPE_ORDER = ['domain', 'catchment', 'near_200m']   // 标签固定顺序，不跟接口键序
const scope = ref('domain')
const scopeTabs = computed(() => (props.states && props.hotspots?.scopes ? SCOPE_ORDER.filter(k => k in props.hotspots.scopes) : null))
watch(() => props.run.id, () => (scope.value = 'domain'))
const inScope = computed(() => !!scopeTabs.value && scope.value !== 'domain')
const fmtHa = v => (Number(v) >= 10 ? Number(v).toFixed(1) : Number(v).toFixed(2))
const SCOPE_SPEC = [
  PONDING,
  { k: 'overflow', u: 'm³', better: 'lower', fmt: v => fmtInt(Math.round(v)) },
  SPEC.find(x => x.k === 'volume'),
  { k: 'a10', u: 'ha', better: 'lower', fmt: fmtHa },
  { k: 'a30', u: 'ha', better: 'lower', fmt: fmtHa },
  { k: 'depth', u: 'm', better: 'lower', fmt: v => Number(v).toFixed(2) },
  SPEC.find(x => x.k === 'storage'),
]
/** 该口径下各态的值；系统储水能力不分口径（整个系统的量），各口径都取全域值 */
const scopeRows = computed(() => stateCols.value?.map(c => {
  const d = props.hotspots.scopes[scope.value]?.[c.key] || {}
  return { key: c.key, label: stateCode(c.key), v: {
    ponding: d.ponding ?? null, overflow: d.overflow_m3 ?? null, volume: d.max_volume_m3 ?? null, a10: d.area_ge_0_10_ha ?? null,
    a30: d.area_ge_0_30_ha ?? null, depth: d.max_depth_m ?? null, storage: colVals(c).storage,
  } }
}) ?? null)

/** 水深 ≥ 阈值的积水量（全市 #1068 的应对设备配置用的口径，随 /equipment 来）：只在看全程、且有这一态的数时出 */
const volGe = computed(() => (props.states && !props.now && props.volGe?.volume_m3 ? props.volGe : null))
/** L1 不排水、L2 排掉约 35%（用户给的比例）：L2 取模型的剩余积水，L1 = L2 ÷ 65%，两态和应对设备配置页的总积水量同一个数 */
const L2_REMOVES = 0.35
const geVolume = k => {
  const v = volGe.value?.volume_m3
  if (!v) return null
  return props.cityGe && k === 'L1' && v.L2 != null ? v.L2 / (1 - L2_REMOVES) : v[k]
}
/** 全市 #1068 的 L2 水量账：总积水量（L1 不排水）→ L2 排掉 → 排完剩余 */
const l2Balance = computed(() => {
  if (props.now || !props.states || !volGe.value) return null
  const cur = props.states.find(s => s.key === props.stateKey) || props.states[0]
  if (!props.cityGe || cur.defence_line_key !== 'line2_current') return null
  const left = geVolume('L2'), need = geVolume('L1')
  return left == null || need == null ? null : { need, drain: need - left, left, total: need }
})
const keepDim = x => !(props.noOverflow && (x.k === 'ponding' || x.k === 'overflow')) && !(props.noPondPoints && x.k === 'ponding')
/** 总积水量：这份交付拿不出（没有逐帧水量序列）就整块不出，不放一个「—」占位 */
const hasVolume = rs => (rs || []).some(r => r.v.volume != null)
const dims = computed(() => inScope.value
  ? build(SCOPE_SPEC.filter(keepDim).filter(x => x.k !== 'volume' || hasVolume(scopeRows.value)), scopeRows.value, x => t(`sim.levels.scopeDim.${x.k}`))
  : build([PONDING, ...(volGe.value ? [SPEC_GE] : []), ...SPEC].filter(keepDim).filter(x => x.k !== 'volume' || hasVolume(rows.value)),
    volGe.value ? rows.value.map(r => ({ ...r, v: { ...r.v, volGe: geVolume(r.key) ?? null } })) : rows.value,
    x => (x.k === 'volGe' ? t('sim.levels.dim.volGe', { cm: Math.round(volGe.value.threshold_m * 100) }) : dimLabel(x))))

/* ---------------- 标题、降雨情景与脚注 ---------------- */
const lineText = computed(() => (props.l2View && !props.states ? lineName('line2_current') : props.run.defence_line_label || ''))
const title = computed(() => t('sim.panel.title', { line: lineText.value }))
/** 降雨情景一节（换 LevelPanel 时漏搬，9/28 补回）：雨量｜雨型、范围、防线（不列边界），与 RunSummary 同口径 */
const num = x => (x === null || x === undefined ? '—' : String(Number(x)))
const rainText = computed(() => {
  const r = props.run
  const s = stormLabel(r)
  return r.rain_source !== 'observed_replay' && r.temporal_pattern ? `${s} ｜ ${r.temporal_pattern}` : s
})
const notes = computed(() => {
  const out = []
  if (inScope.value) {
    out.push({ text: t(`sim.levels.scopeNote.${scope.value}`) })
    out.push({ text: t('sim.levels.scopeNote.common') })
    return out
  }
  if (props.now) {
    out.push({ text: t('sim.levels.noteNow', { t: fmtClock(props.now.time_minutes) }) })
    if (props.now.overflow_basis === 'record') out.push({ text: t('sim.levels.noteNowRecord') })
    if (props.l2View && !props.states) out.push({ text: t('sim.levels.noteNowL2') })
  }
  if (props.states && perf.value) {
    if (!props.now) out.push({ text: t(props.noOverflow ? 'sim.levels.noteStateNoOvf' : 'sim.levels.noteState', { w: Math.round(perf.value.common_end_minutes / 6) / 10 }) })
    if (!props.now && hasVolume(stateRows.value)) out.push({ text: t(volGe.value ? 'sim.levels.noteVolumeSurf' : 'sim.levels.noteVolume') })
    if (volGe.value) out.push({ text: t('sim.levels.noteVolGe', { cm: Math.round(volGe.value.threshold_m * 100) }) })
    if (!props.now && !perf.value.network.comparable) out.push({ text: t('sim.levels.notComparable'), warn: true })
    const same = stateRows.value?.find(r => r.sameAs)
    if (same) out.push({ text: t('sim.levels.sameAs', { s: same.key, as: same.sameAs }) })
  } else if (props.run.has_l2 && l2.value) {
    if (!props.now) out.push({ text: t('sim.levels.noteCity') })
    const e = l2.value.estimate
    if (props.l2View && e) {
      out.push({
        text: t(e.borrowed ? 'sim.levels.estBorrowed' : 'sim.levels.est', {
          pct: (e.reduction_ratio * 100).toFixed(1), d: Number(e.from_storm.depth_mm), h: Number(e.from_storm.duration_h),
        }),
        warn: true,
      })
    }
    out.push({ text: storageCmp.value ? t('sim.levels.storageCity', { pond: fmtInt(storageCmp.value.pond_m3) }) : t('sim.levels.noStorage') })
  } else if (!props.states && !props.run.has_l2 && props.meta) {
    out.push({ text: t('sim.levels.notePlain') })
  }
  return out
})
const loading = computed(() => !err.value && !rows.value)

/** 峰值水深前 10：≥ 0.30 m（片区入选的水深判据）标红，其余标橙。
 *  多态任务的溢水点就是当前态自己的；全市任务的第二道视图没有二维成果，沿用第一道的溢水点与名次，
 *  水深、退水时间按同一比例折算（先取到显示精度再折算），未退干的仍写未退干。
 *  时间过程下（地图给了 now.ponds）换成「本帧水深前 10」：只排本帧已成立且片区内有水的点，水深取本帧片区内最深
 *  （与地图悬浮卡同一个数），逐帧重排；退水时间一列仍是该点全程的结果。片区与名次不随帧重算。 */
const topLive = computed(() => !!props.now?.ponds && !!props.ponding?.clusters?.length)
const fmtH = h => (h == null ? '—' : Number(h).toFixed(1))
const top = computed(() => {
  const scaled = props.run.has_l2 && !props.states && props.l2View
  if (scaled && keep.value == null) return []
  const f = scaled ? v => v * keep.value : v => v
  const all = props.ponding?.clusters || []
  const live = props.now?.ponds
  const nowD = live && new Map(live.filter(p => p.st === 'wet' && p.depth_m != null).map(p => [p.rank, p.depth_m]))
  const depthOf = x => (nowD ? nowD.get(x.rank) : x.max_depth_m)
  const c = all.filter(x => depthOf(x) != null).sort((a, b) => depthOf(b) - depthOf(a)).slice(0, 10)
  const th = props.ponding?.criteria?.select_max_depth_m ?? 0.3
  const max = f(Number(depthOf(c[0] || {})?.toFixed(2))) || 1
  return c.map(x => {
    const d = f(Number(depthOf(x).toFixed(2)))
    return {
      ...x,
      d,
      h: x.drained ? f(Number(Number(x.recession_h).toFixed(1))) : null,
      pct: (d / max) * 100,
      color: d >= th ? 'var(--red)' : 'var(--amber)',
    }
  })
})
</script>

<template>
  <aside class="sim-card ro">
    <h2 class="ro-title">{{ title }}</h2>

    <section class="ro-sec ro-rain">
      <h3>{{ t('sim.l2.rainTitle') }}</h3>
      <div class="ro-kv">
        <span class="k">{{ t('sim.panel.rain') }}</span><span class="v">{{ rainText }}</span>
        <span class="k">{{ t('sim.panel.extent') }}</span>
        <span class="v">{{ run.extent || '—' }} {{ run.grid_m ? `${num(run.grid_m)} m` : '' }}</span>
        <span class="k">{{ t('sim.panel.basis') }}</span><span class="v">{{ lineText || '—' }}</span>
      </div>
    </section>

    <p v-if="err" class="ro-err">{{ err }}</p>
    <p v-else-if="loading" class="ro-note">{{ t('sim.l2.loading') }}</p>

    <div v-if="scopeTabs" class="ro-scope" role="tablist" :title="t('sim.levels.scope.tip')">
      <button v-for="k in scopeTabs" :key="k" role="tab" :class="{ on: k === scope }" :aria-selected="k === scope"
              @click="scope = k">{{ t(`sim.levels.scope.${k}`) }}</button>
    </div>

    <CityBalance v-if="l2Balance" :title="t('sim.balance.l2')" :need="l2Balance.need" :drain="l2Balance.drain" :left="l2Balance.left" :total="l2Balance.total" />

    <div v-for="x in dims" :key="x.k" class="ro-box lv">
      <div class="lv-head">
        <h4>{{ x.label }}</h4>
        <span v-if="x.dv != null" class="lv-delta" :class="{ good: x.good === true, bad: x.good === false }">
          <em>{{ t('sim.levels.vsL1') }}</em>{{ x.dv }} {{ x.u }}<small v-if="x.dp">{{ x.dp }}</small>
        </span>
      </div>
      <div class="lv-big">{{ x.cur }}<small v-if="x.cur !== '—'">{{ x.u }}</small></div>
      <div v-for="r in x.rows" :key="r.key" class="lv-bar">
        <span class="lb" :title="r.label">{{ r.label }}</span>
        <span class="track"><span v-if="r.val != null" class="bar" :style="{ width: r.pct + '%', background: r.color }" /></span>
        <span class="n">{{ r.text }}</span>
      </div>
    </div>

    <p v-for="(n, i) in notes" :key="i" :class="n.warn ? 'ro-warn' : 'ro-note'">{{ n.text }}</p>

    <section v-if="top.length || topLive" class="ro-sec">
      <h3>{{ topLive ? t('sim.panel.topNow', { t: fmtClock(now.time_minutes) }) : t('sim.panel.top') }}</h3>
      <p v-if="!top.length" class="ro-note">{{ t('sim.panel.topNone') }}</p>
      <div v-else class="ro-top">
        <button v-for="c in top" :key="c.rank" class="ro-row" :class="{ on: c.rank === active }"
                :title="[c.name, fmtLatLon(c)].filter(Boolean).join('\n')" @click="emit('focus', c.rank)">
          <span class="nm">{{ c.name || `#${c.rank}` }}<small v-if="fmtLatLon(c)">{{ fmtLatLon(c) }}</small></span>
          <span class="track"><span class="bar" :style="{ width: c.pct + '%', background: c.color }" /></span>
          <span class="d">{{ c.d.toFixed(2) }} m</span>
          <span class="h" :class="{ und: c.h == null }">{{ c.h != null ? `${fmtH(c.h)} h` : t('sim.panel.notDrained') }}</span>
        </button>
      </div>
    </section>

    <button v-if="states && states.length > 1" class="ro-gap" @click="emit('perf')">{{ t('sim.panel.gap') }}</button>
  </aside>
</template>

<style scoped>
/* 外框与 RunOverview 同款 */
.ro { padding: 22px 22px 18px; display: flex; flex-direction: column; background: linear-gradient(180deg, #1f2124, #17181b); }
.ro-scope { display: flex; flex-wrap: wrap; gap: 4px; margin: -6px 0 16px; }
.ro-scope button { padding: 5px 11px; font-size: 12.5px; border-radius: 14px; border: 1px solid var(--line-2); background: transparent; color: var(--fg-2); cursor: pointer; }
.ro-scope button.on { background: var(--blue, #4c8dff); border-color: transparent; color: #fff; }
.ro-title { font-size: 20px; font-weight: 500; margin: 0 0 18px; padding-bottom: 14px; border-bottom: 1px solid var(--line-2); }
.ro-box { background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 14px 16px; }
.ro-box + .ro-box { margin-top: 10px; }
.lv-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.lv-head h4 { font-size: 13px; font-weight: 600; margin: 0; color: var(--fg-2); }
.lv-delta { font-size: 12.5px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--fg-2); }
.lv-delta.good { color: var(--green); }
.lv-delta.bad { color: var(--amber); }
.lv-delta em { font-style: normal; font-weight: 400; color: var(--muted); margin-right: 6px; }
.lv-delta small { margin-left: 6px; font-weight: 400; opacity: 0.85; }
.lv-big { font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; margin: 4px 0 2px; }
.lv-big small { font-size: 13px; font-weight: 400; color: var(--fg-2); margin-left: 4px; }
.lv-bar { display: grid; grid-template-columns: 96px minmax(0, 1fr) 84px; align-items: center; gap: 10px; font-size: 12.5px; line-height: 28px; }
.lv-bar .lb { color: var(--fg-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lv-bar .n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.track { display: block; height: 7px; border-radius: 4px; background: rgba(255, 255, 255, 0.06); overflow: hidden; }
.bar { display: block; height: 100%; border-radius: 4px; min-width: 2px; }
.ro-note { color: var(--muted); font-size: 11.5px; margin: 10px 0 0; line-height: 1.5; }
.ro-warn { color: var(--amber); font-size: 11.5px; margin: 10px 0 0; line-height: 1.5; }
.ro-err { color: var(--red); font-size: 12px; margin: 0 0 10px; }
.ro-sec { margin-top: 18px; }
.ro-sec h3 { font-size: 15px; font-weight: 700; margin: 0 0 12px; }
.ro-rain { margin: 0 0 18px; }
.ro-kv { display: grid; grid-template-columns: 112px minmax(0, 1fr); row-gap: 12px; column-gap: 12px; font-size: 13px; }
.ro-kv .k { color: var(--muted); }
.ro-kv .v { color: var(--fg); }
.ro-top { max-height: 330px; overflow-y: auto; margin-right: -6px; padding-right: 6px;
  mask-image: linear-gradient(180deg, #000 88%, transparent); }
.ro-row { display: grid; grid-template-columns: minmax(0, 1fr) 72px 54px 62px; align-items: center; gap: 8px;
  width: 100%; border: 0; background: none; padding: 0 4px; line-height: 34px; font-size: 12.5px; text-align: left;
  cursor: pointer; border-radius: 4px; color: inherit; font-family: inherit; }
.ro-row:hover { background: rgba(255, 255, 255, 0.04); }
.ro-row.on { background: var(--blue-bg); }
.ro-row .nm { color: var(--fg-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.35; padding: 5px 0; }
.ro-row .nm small { display: block; font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.ro-row .d, .ro-row .h { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ro-row .h.und { color: var(--amber); font-size: 11.5px; }
.ro-gap { margin-top: 18px; height: 44px; border: 0; border-radius: 8px; background: #4aa3ea; color: #fff;
  font-size: 15px; font-weight: 500; cursor: pointer; }
</style>
