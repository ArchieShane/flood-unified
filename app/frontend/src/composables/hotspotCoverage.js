/* 「我们算出的积水点覆盖了多少客户热点」的共用逻辑：RD-A（`useRda.js`）与全市一二维任务（`usePondingPoints.js`）
   两个页面的「积水点」标签用同一个组件 `PondingTab.vue`，数据层都从这里取覆盖统计、热点清单、积水点列表，
   口径一致（客户文档《RD-A 模拟积水点与客户热点重合分析》）：
   · 距离 = 热点到最近积水片边界的平面距离（后端 `hotspot_dist.py` 量好，前端只用 near_hotspots / hotspot_bins）；
   · 覆盖 = 某个积水点 200 m 内有该热点；一半以上面积落在评估剔除区内的片（`mostly_excluded`）不计入；
   · 积水量覆盖率 = 热点 200 m 内的积水点的峰值积水量之和 / 全部（不含被剔除的）积水点的峰值积水量之和，每个点只算一次。 */
import { computed, ref, shallowRef, watch } from 'vue'

export const SHOW_LIMIT = 100        // 积水点列表默认只出前 N 片（按峰值积水量降序），全市一份清单上千片

/** 后端没给分档时（读不了网格）用近邻表自己数：只有 ≤200 m 的列表，0 / 50 / 100 m 按列表里的距离数 */
function binsFromNear(points, hotspots) {
  const best = {}
  for (const p of points) for (const h of p.near_hotspots || []) best[h.hotspot] = Math.min(best[h.hotspot] ?? 1e9, Number(h.dist_m))
  const n = k => hotspots.filter(h => best[h.id] != null && best[h.id] <= k).length
  return { total: hotspots.length, 0: n(0), 50: n(50), 100: n(100), 200: n(200) }
}

/** 一组积水点（已去掉被剔除的）对一批热点的覆盖统计 */
export function coverOf(points, hotspots, bins, nExcluded = 0) {
  const byHot = {}
  for (const p of points) {
    for (const h of p.near_hotspots || []) (byHot[h.hotspot] ||= []).push({ id: p.id, dist_m: Number(h.dist_m), p })
  }
  const rows = hotspots.map(h => {
    const near = (byHot[h.id] || []).sort((a, b) => a.dist_m - b.dist_m)
    return { ...h, covered: near.length > 0, points: near, vol: near.reduce((s, x) => s + (x.p.vol_peak_m3 || 0), 0) }
  })
  const vol = x => x.reduce((s, p) => s + (p.vol_peak_m3 || 0), 0)
  const inside = points.filter(p => (p.near_hotspots || []).length)
  const covered = rows.filter(r => r.covered).length
  const total = vol(points)
  return {
    rows, covered, total: rows.length, rate: rows.length ? (covered / rows.length) * 100 : null,
    nPoints: points.length, nInside: inside.length, ptsPct: points.length ? (inside.length / points.length) * 100 : null,
    volTotal: total, volInside: vol(inside), insidePct: total ? (vol(inside) / total) * 100 : null,
    nExcluded, bins: bins ? { ...bins, total: hotspots.length } : binsFromNear(points, hotspots),   // 总数以传入的热点清单为准（全市含域外热点）
  }
}

/** 取一份积水点清单（后端返回体）→ 去掉被剔除的片 → 覆盖统计 */
export function coverOfResponse(resp, hotspots) {
  const all = resp?.points || []
  const pts = all.filter(p => !p.mostly_excluded)
  return coverOf(pts, hotspots, resp?.hotspot_bins, all.length - pts.length)
}

/** 全部热点清单：覆盖情况 + 200 m 内最大水深；搜索 / 筛选 / 分页。`stats` 取当前态的 `hotspot.stats[state]`（没有就空） */
export function useHotspotList(coverage, stateKey) {
  const hotQuery = ref('')
  const hotFilter = ref('all')         // all | covered | uncovered | flooded（200 m 内最大水深 ≥0.30 m）
  const hotLimit = ref(50)
  watch([hotQuery, hotFilter], () => (hotLimit.value = 50))
  const hotspotAll = computed(() => {
    const c = coverage.value
    if (!c) return []
    return c.rows.map(r => ({ ...r, st: r.stats?.[stateKey.value] || {} }))
  })
  const hotspotList = computed(() => {
    const q = hotQuery.value.trim().toLowerCase()
    let rows = hotspotAll.value
    if (hotFilter.value === 'covered') rows = rows.filter(r => r.covered)
    if (hotFilter.value === 'uncovered') rows = rows.filter(r => !r.covered)
    if (hotFilter.value === 'flooded') rows = rows.filter(r => r.st.class === 'flooded')
    if (q) rows = rows.filter(r => `${r.id} ${r.area || ''} ${r.location || ''}`.toLowerCase().includes(q))
    return [...rows].sort((a, b) => (b.st.max_depth_m ?? -1) - (a.st.max_depth_m ?? -1))
  })
  const hotspotCounts = computed(() => ({
    all: hotspotAll.value.length,
    covered: hotspotAll.value.filter(r => r.covered).length,
    uncovered: hotspotAll.value.filter(r => !r.covered).length,
    flooded: hotspotAll.value.filter(r => r.st.class === 'flooded').length,
  }))
  return { hotQuery, hotFilter, hotLimit, hotspotList, hotspotCounts }
}

export const BUF_DEFAULT = 0.15      // 圆内积水量的水深阈值默认值（同积水点阈值）

/** 热点 200 m 圆内的地表峰值积水量 + 圆重叠（后端 `hotspot_buffer.py`）。阈值单独一栏（bf 表单 / bp 已生效），
    改了不影响积水点清单与覆盖率。`deps` 是会让结果作废的 ref（态、任务号…），`load(depth)` 返回 Promise，不该取时返回 null。 */
export function useHotspotBuffer(deps, load, { onError } = {}) {
  const bp = ref(BUF_DEFAULT)
  const bf = ref(BUF_DEFAULT)
  const data = shallowRef(null)
  const loading = ref(false)
  let seq = 0
  async function run() {
    const my = ++seq
    const p = load(bp.value)
    if (!p) { data.value = null; loading.value = false; return }
    loading.value = true
    try {
      const r = await p
      if (my === seq) data.value = r
    } catch (e) {
      if (my === seq) { data.value = null; onError?.(e.message) }
    } finally {
      if (my === seq) loading.value = false
    }
  }
  watch([bp, ...deps], run, { immediate: true })
  const dirty = computed(() => Number(bf.value) !== bp.value)
  function apply() {
    const v = Number(bf.value)
    if (!(v >= 0.05 && v <= 3)) { onError?.('水深阈值要在 0.05–3 m 之间'); return }
    bp.value = v
  }
  function reset() { bf.value = BUF_DEFAULT; bp.value = BUF_DEFAULT }
  const byId = computed(() => Object.fromEntries((data.value?.hotspots || []).map(h => [h.id, h])))
  return { bf, bp, dirty, data, loading, apply, reset, byId }
}

/** 积水点列表：筛选（未退 / 要 L3 / 剔除区 / 只看热点 200 m 内）+ 默认只出前 SHOW_LIMIT 片 */
export function usePointList(ponding) {
  const pointFilter = ref('all')         // all | notRecede | l3 | excluded
  const nearOnly = ref(false)
  const showAll = ref(false)
  const visiblePoints = computed(() => {
    let pts = ponding.value?.points || []
    if (nearOnly.value) pts = pts.filter(p => (p.near_hotspots || []).length)
    if (pointFilter.value === 'notRecede') return pts.filter(p => p.receded === false)
    if (pointFilter.value === 'l3') return pts.filter(p => p.l3 && p.l3.measure && p.l3.measure !== '无需')
    if (pointFilter.value === 'excluded') return pts.filter(p => p.excluded || p.mostly_excluded)
    return pts
  })
  const shownPoints = computed(() => (showAll.value ? visiblePoints.value : visiblePoints.value.slice(0, SHOW_LIMIT)))
  return { pointFilter, nearOnly, showAll, visiblePoints, shownPoints, SHOW_LIMIT }
}
