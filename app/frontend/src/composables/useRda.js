/* RD-A 全套成果的数据层：给 `/rda` 页和仿真任务详情页（#RDA 那条）共用。
   传进来一个态 key（RDA 口径：L1 / L2 / L2P / OPTCS2），取回这一态的积水点（成果自带两档阈值，或按阈值现算）、
   L3 承接、判据、设施，以及（只有一份的）L2+suggested 措施台账；管选中项与过滤。
   不碰地图：地图那边（`RdaView` 自己的 Leaflet / 详情页的 `ResultMap`）读这里的 ref 去画。 */
import { computed, ref, shallowRef, watch } from 'vue'

import {
  fetchRdaCompliance, fetchRdaCriteria, fetchRdaFacilities, fetchRdaL3, fetchRdaOverview,
  fetchRdaHotspotBuffer, fetchRdaHotspots, fetchRdaPonding, fetchRdaPondingCustom, fetchRdaSuggested,
} from '../api'
import { tr } from '../i18n/rda'
import { useHotspotBuffer } from './hotspotCoverage'

/** 仿真任务里的态 key（L2+planned / L2+suggested）→ RDA 口径 */
export const RDA_KEY = { L1: 'L1', L2: 'L2', 'L2+planned': 'L2P', 'L2+suggested': 'OPTCS2' }

/** 成果自带的积水点阈值：0.15 / 0.30 m、100 m²、0.5 h；别的组合走后端重算 */
export const DELIVERED = { area: 100, dur: 0.5, depths: [0.15, 0.3] }
export const DEPTH_CHOICES = [0.1, 0.15, 0.2, 0.3, 0.5]
const RDA_STATES = ['L1', 'L2', 'L2P', 'OPTCS2']

const overview = shallowRef(null)      // 总览：设施类型名等，整份成果只有一份，取一次
const suggested = shallowRef(null)     // L2+suggested 措施台账，同上
const hotspotMeta = shallowRef({})     // 11 个客户热点的片区 / 严重度（id → properties），同上只取一次
let overviewReq = null
let suggestedReq = null
let hotspotReq = null

export function ledgerId(fid) {
  return String(fid).replace(/_g$/, '').replace(/^(SP\d+)_\d+$/, '$1')
}

export function useRda(stateKey, { onError } = {}) {
  const ponding = shallowRef(null)
  const l3 = shallowRef(null)
  const crit = shallowRef(null)
  const fac = shallowRef(null)
  const compliance = shallowRef(null)
  const loading = ref(false)
  const error = ref('')

  const pp = ref({ depth: 0.15, area: DELIVERED.area, dur: DELIVERED.dur })    // 已生效的阈值
  const pf = ref({ depth: 0.15, area: DELIVERED.area, dur: DELIVERED.dur })    // 表单里正在改的
  const isDelivered = computed(() => pp.value.area === DELIVERED.area && pp.value.dur === DELIVERED.dur && DELIVERED.depths.includes(pp.value.depth))
  const formDirty = computed(() => pf.value.depth !== pp.value.depth || pf.value.area !== pp.value.area || pf.value.dur !== pp.value.dur)

  const selPoint = ref(null)
  const selMeasure = ref(null)
  const selFac = ref(null)
  const pointFilter = ref('all')         // all | notRecede | l3 | excluded
  const nearOnly = ref(false)
  const showAllChecks = ref(false)

  const isSugg = computed(() => stateKey.value === 'OPTCS2')
  const kindName = k => tr(overview.value?.facility_kinds?.[k] || k)

  const visiblePoints = computed(() => {
    let pts = ponding.value?.points || []
    if (nearOnly.value) pts = pts.filter(p => (p.n_client_hotspots_200m || 0) > 0)
    if (pointFilter.value === 'notRecede') return pts.filter(p => p.receded === false)
    if (pointFilter.value === 'l3') return pts.filter(p => p.l3 && p.l3.measure && p.l3.measure !== '无需')
    if (pointFilter.value === 'excluded') return pts.filter(p => p.excluded || p.mostly_excluded)
    return pts
  })
  /** 反查：这一态、当前阈值下算出的积水点，覆盖了哪些客户热点（点的 200 m 内热点清单反过来按热点聚合）。
      只读 `ponding.points[].near_hotspots`，不改任何原始数据；热点清单取判据里的 11 个。 */
  const hotspotCoverage = computed(() => {
    const all = ponding.value?.points
    const hs = crit.value?.hotspots
    if (!all || !hs) return null
    // 一半以上面积落在评估剔除区内的片不计入（客户文档同口径：ADM-80 旁几片几乎全在 DTM 缺测区里）
    const pts = all.filter(p => !p.mostly_excluded)
    const byHot = {}
    for (const p of pts) {
      for (const h of p.near_hotspots || []) (byHot[h.hotspot] ||= []).push({ id: p.id, dist_m: Number(h.dist_m), p })
    }
    const rows = hs.map(h => {
      const near = (byHot[h.id] || []).sort((a, b) => a.dist_m - b.dist_m)
      const m = hotspotMeta.value[h.id] || {}
      return {
        id: h.id, area: m.area, criticality: m.criticality_zh, location: m.location, max_m: h.max_m,
        covered: near.length > 0, points: near,
        peak_vol_m3: near.reduce((s, x) => s + (x.p.vol_peak_m3 || 0), 0),
      }
    })
    // 积水量按热点 200 m 内 / 范围之外拆（每片只算一次；不含被剔除的片）
    const vol = x => x.reduce((s, p) => s + (p.vol_peak_m3 || 0), 0)
    const total = vol(pts)
    const inside = vol(pts.filter(p => (p.near_hotspots || []).length))
    const covered = rows.filter(r => r.covered).length
    return {
      rows, covered, total: rows.length, rate: rows.length ? (covered / rows.length) * 100 : null,   // 热点覆盖率 = 被覆盖热点数 / 热点总数
      nPoints: pts.length, nExcluded: all.length - pts.length, nInside: pts.filter(p => (p.near_hotspots || []).length).length,
      volTotal: total, volInside: inside, insidePct: total ? (inside / total) * 100 : null,
    }
  })
  /** 四个状态并排的覆盖率（同一套阈值）：热点覆盖率 = 被覆盖热点数 / 热点总数；积水量覆盖率 = 热点 200 m 内积水量占比。
      口径与上面 `hotspotCoverage` 一致（剔除多半在剔除区的片）。切态、改阈值后后台重取，取不到的态给 null。 */
  const coverageAll = shallowRef(null)
  let covSeq = 0
  async function loadCoverageAll() {
    const my = ++covSeq
    const ids = Object.keys(hotspotMeta.value).length ? Object.keys(hotspotMeta.value) : (crit.value?.hotspots || []).map(h => h.id)
    if (!ids.length || !stateKey.value) { coverageAll.value = null; return }
    const { depth, area, dur } = pp.value
    const fetchOne = k => (isDelivered.value ? fetchRdaPonding(k, depth.toFixed(2)) : fetchRdaPondingCustom(k, depth, area, dur))
    const out = await Promise.all(RDA_STATES.map(async k => {
      try {
        const pts = (await fetchOne(k)).points.filter(p => !p.mostly_excluded)
        const near = new Set(pts.flatMap(p => (p.near_hotspots || []).map(h => h.hotspot)))
        const covered = ids.filter(i => near.has(i)).length
        const vol = x => x.reduce((s, p) => s + (p.vol_peak_m3 || 0), 0)
        const total = vol(pts)
        const inside = vol(pts.filter(p => (p.near_hotspots || []).length))
        return { key: k, covered, total: ids.length, rate: (covered / ids.length) * 100, insidePct: total ? (inside / total) * 100 : null, nPoints: pts.length }
      } catch {
        return { key: k, covered: null, total: ids.length, rate: null, insidePct: null, nPoints: null }
      }
    }))
    if (my === covSeq) coverageAll.value = out
  }
  const selectedPoint = computed(() => (ponding.value?.points || []).find(p => p.id === selPoint.value) || null)
  const measureById = computed(() => Object.fromEntries((suggested.value?.measures || []).map(m => [m.id, m])))
  const selectedMeasure = computed(() => (selMeasure.value ? measureById.value[selMeasure.value] : null))
  const measureSim = computed(() => {
    if (!selMeasure.value) return []
    return (fac.value?.geojson.features || []).map(f => f.properties)
      .filter(p => ledgerId(p['编号']) === selMeasure.value && (p['本场模拟_max_m3'] != null || p['本场模拟_vol_m3'] != null))
  })
  const shownChecks = computed(() => {
    const c = compliance.value?.checks || []
    return showAllChecks.value ? c : c.filter(x => x.result !== 'pass')
  })
  const measureTypes = computed(() => suggested.value?.by_type?.rows || [])
  const costTotal = computed(() => suggested.value?.by_type?.total)

  function fetchPonding() {
    const { depth, area, dur } = pp.value
    return isDelivered.value
      ? fetchRdaPonding(stateKey.value, depth.toFixed(2))
      : fetchRdaPondingCustom(stateKey.value, depth, area, dur)
  }

  /** 换态：积水点 / 判据 / 设施 / L3 一起重取；选中项清掉 */
  async function loadState() {
    if (!stateKey.value) return          // 不是 RD-A 任务（详情页传 null）：什么都不取
    loading.value = true
    error.value = ''
    selPoint.value = null
    selMeasure.value = null
    selFac.value = null
    const key = stateKey.value
    try {
      const [p, c, fc] = await Promise.all([fetchPonding(), fetchRdaCriteria(key), fetchRdaFacilities(key)])
      if (key !== stateKey.value) return
      ponding.value = p
      crit.value = c
      fac.value = fc
      l3.value = await fetchRdaL3(key).catch(() => null)
    } catch (e) {
      error.value = e.message
      onError?.(tr('RDA 成果读取失败：') + e.message)
    } finally {
      loading.value = false
    }
  }

  /** 只换阈值：积水点重取（判据 / 设施不变） */
  async function loadPonding() {
    if (!stateKey.value) return
    loading.value = true
    try {
      ponding.value = await fetchPonding()
      selPoint.value = null
      if (pointFilter.value === 'l3' && ponding.value.custom) pointFilter.value = 'all'
    } catch (e) {
      onError?.(tr('积水点重算失败：') + e.message)
    } finally {
      loading.value = false
    }
  }

  function applyThreshold() {
    const f = pf.value
    if (!(f.area >= 0) || !(f.dur >= 0)) { onError?.(tr('连片面积和持续时间要填非负数')); return }
    pp.value = { depth: Number(f.depth), area: Number(f.area), dur: Number(f.dur) }
  }
  function presetDepth(d) {
    pf.value = { depth: d, area: DELIVERED.area, dur: DELIVERED.dur }
    pp.value = { ...pf.value }
  }

  async function pickMeasure(id) {
    selMeasure.value = id
    compliance.value = null
    showAllChecks.value = false
    try {
      compliance.value = await fetchRdaCompliance(id)
    } catch {
      compliance.value = null
    }
  }

  /** 一次性的总览 / 措施台账（模块级缓存，页面切换不重取） */
  function ensureShared() {
    overviewReq ||= fetchRdaOverview().then(o => (overview.value = o)).catch(() => { overviewReq = null })
    suggestedReq ||= fetchRdaSuggested().then(s => (suggested.value = s)).catch(() => { suggestedReq = null })
    hotspotReq ||= fetchRdaHotspots()
      .then(g => (hotspotMeta.value = Object.fromEntries((g.features || []).map(f => [f.properties.hotspot_id, f.properties]))))
      .catch(() => { hotspotReq = null })
    return Promise.all([overviewReq, suggestedReq, hotspotReq])
  }

  /** 热点 200 m 圆内的地表峰值积水量 + 圆重叠；阈值单独一栏，不影响上面的积水点 */
  const buf = useHotspotBuffer([stateKey], d => (stateKey.value ? fetchRdaHotspotBuffer(stateKey.value, d) : null),
    { onError: m => onError?.(tr('圆内积水量读取失败：') + m) })

  watch(stateKey, loadState)
  watch(pp, loadPonding)
  watch([() => !!stateKey.value, pp, hotspotMeta], loadCoverageAll, { immediate: true })   // 换阈值 / 热点清单到齐后重算四态覆盖率

  return {
    stateKey, overview, suggested, ponding, l3, crit, fac, compliance, loading, error,
    pp, pf, isDelivered, formDirty, selPoint, selMeasure, selFac, pointFilter, nearOnly, showAllChecks, isSugg, kindName,
    buf, visiblePoints, hotspotCoverage, coverageAll, selectedPoint, measureById, selectedMeasure, measureSim, shownChecks, measureTypes, costTotal,
    loadState, loadPonding, applyThreshold, presetDepth, pickMeasure, ensureShared,
  }
}
