/* 全市一二维任务（如 #1068 ADM 100 m）的「模拟积水点」数据层：口径同 RD-A（`useRda.js`），页面也是同一个组件
   （`PondingTab.vue`），所以这里对外的名字与 useRda 一致（ponding / pf / pp / applyThreshold / hotspotCoverage / coverageAll /
   visiblePoints / hotspotList …）。数据走 `/api/simulations/{id}/ponding-points`（后端 `city_ponding.py`）：传进来任务号
   （没有这块数据的传 null）和态 key，取回这一态按阈值现算的积水点；覆盖统计、热点清单、积水点列表的逻辑在 `hotspotCoverage.js`。
   和 RD-A 的差别：没有成果自带清单、评估剔除区、L3；热点是交付的 470 个，另有台账里不在计算域内的只列不算；100 m 网格。
   不碰地图：地图那边读这里的 ref 去画。 */
import { computed, ref, shallowRef, watch } from 'vue'

import { fetchHotspotBuffer, fetchPondingPoints, fetchPondingPointsMeta } from '../api'
import { coverOfResponse, useHotspotBuffer, useHotspotList, usePointList } from './hotspotCoverage'

export const DEPTH_CHOICES = [0.1, 0.15, 0.2, 0.3, 0.5]
const DEFAULT = { depth: 0.15, area: 100, dur: 0.5 }      // 同 RD-A / 客户文档：0.15 m、100 m²、0.5 h

export function usePondingPoints(runId, stateKey, { onError } = {}) {
  const meta = shallowRef(null)
  const ponding = shallowRef(null)
  const loading = ref(false)
  const pp = ref({ ...DEFAULT })        // 已生效的阈值
  const pf = ref({ ...DEFAULT })        // 表单里正在改的
  const formDirty = computed(() => pf.value.depth !== pp.value.depth || pf.value.area !== pp.value.area || pf.value.dur !== pp.value.dur)
  const selPoint = ref(null)
  const coverageAll = shallowRef(null)

  const enabled = computed(() => runId.value != null)

  const { pointFilter, nearOnly, showAll, visiblePoints, shownPoints } = usePointList(ponding)
  const selectedPoint = computed(() => (ponding.value?.points || []).find(p => p.id === selPoint.value) || null)
  // 地图上画全部积水点（画布圆点，几千个也流畅）；在热点 200 m 内的红色，其余橙色
  const mapPoints = computed(() => (ponding.value?.points || []).map(p => ({
    id: p.id, lat: p.lat, lon: p.lon, vol: p.vol_peak_m3, near: (p.near_hotspots || []).length > 0,
    color: (p.near_hotspots || []).length ? '#d9434b' : '#e0a44e',
    label: `${p.id} · ${p.hmax_m} m · ${Math.round(p.vol_peak_m3).toLocaleString('en-US')} m³`,
  })))

  const buf = useHotspotBuffer([runId, stateKey, meta],
    d => (enabled.value && meta.value && stateKey.value ? fetchHotspotBuffer(runId.value, stateKey.value, d) : null), { onError })
  /** 热点总清单 = 域内交付的 + 台账里不在计算域内的（没有仿真结果，恒为未覆盖），合起来是台账 ADM 506 个 */
  const allHotspots = m => [...m.hotspots, ...(m.outside_hotspots || [])]
  const hotspotCoverage = computed(() => (meta.value && ponding.value ? coverOfResponse(ponding.value, allHotspots(meta.value)) : null))
  const { hotQuery, hotFilter, hotLimit, hotspotList, hotspotCounts } = useHotspotList(hotspotCoverage, stateKey)

  const query = () => {
    const { depth, area, dur } = pp.value
    return [runId.value, stateKey.value, depth, area, dur]
  }

  async function loadPonding() {
    if (!enabled.value) return
    loading.value = true
    const key = stateKey.value
    try {
      const r = await fetchPondingPoints(...query())
      if (key !== stateKey.value) return
      ponding.value = r
      selPoint.value = null
    } catch (e) {
      onError?.(e.message)
    } finally {
      loading.value = false
    }
  }

  let seq = 0
  /** 各态并排的覆盖率（同一套阈值），后台取 */
  async function loadCoverageAll() {
    const my = ++seq
    if (!enabled.value || !meta.value) { coverageAll.value = null; return }
    const { depth, area, dur } = pp.value
    const hs = allHotspots(meta.value)
    const out = await Promise.all(meta.value.states.map(async k => {
      try {
        const c = coverOfResponse(await fetchPondingPoints(runId.value, k, depth, area, dur), hs)
        return { key: k, covered: c.covered, total: c.total, rate: c.rate, insidePct: c.insidePct, ptsPct: c.ptsPct, nPoints: c.nPoints,
          nInside: c.nInside, volTotal: c.volTotal, volInside: c.volInside, bins: c.bins }
      } catch {
        return { key: k, covered: null, total: hs.length, rate: null, insidePct: null, ptsPct: null, nPoints: null,
          nInside: null, volTotal: null, volInside: null, bins: null }
      }
    }))
    if (my === seq) coverageAll.value = out
  }

  async function load() {
    if (!enabled.value) { meta.value = ponding.value = coverageAll.value = null; return }
    try {
      meta.value = await fetchPondingPointsMeta(runId.value)
    } catch {
      meta.value = null
      return
    }
    await loadPonding()
    loadCoverageAll()
  }

  function applyThreshold() {
    const f = pf.value
    if (!(f.area >= 0) || !(f.dur >= 0)) { onError?.('连片面积和持续时间要填非负数'); return }
    pp.value = { depth: Number(f.depth), area: Number(f.area), dur: Number(f.dur) }
  }
  function resetThreshold() {
    pf.value = { ...DEFAULT }
    pp.value = { ...pf.value }
  }

  watch(runId, load, { immediate: true })
  watch(stateKey, () => { if (enabled.value && meta.value) loadPonding() })
  watch(pp, () => { if (enabled.value && meta.value) { loadPonding(); loadCoverageAll() } })

  return {
    meta, ponding, loading, stateKey, pp, pf, formDirty, selPoint, enabled,
    pointFilter, nearOnly, showAll, visiblePoints, shownPoints, selectedPoint, mapPoints,
    buf, hotspotCoverage, coverageAll, hotQuery, hotFilter, hotLimit, hotspotList, hotspotCounts,
    applyThreshold, resetThreshold,
    // 给共用的「积水点」标签（PondingTab.vue，内容与布局同 RD-A）：全市没有成果自带清单和评估剔除区，有网格说明与域外热点
    hasDelivered: false, hasExclusion: false,
    isDelivered: computed(() => false), presetDepth: () => resetThreshold(),
    outsideDomain: computed(() => meta.value?.outside_domain || []),
    gridNote: '本任务是 100 m 网格：一个单元约 2,000 m²（所以默认的 100 m² 连片下限实际等于至少 1 个单元），地形被平均到 100 m、小洼地被抹平，点数和位置与 RD-A（15 m）不可比；没有评估剔除区。',
  }
}
