<script setup>
/* 任务详情：「Open ›」落到这里。挂了交付成果的任务（has_layers）先出结果地图（最大水深 /
   时间过程），下面是本次降雨的雨型图（设计暴雨才有），再列任务参数、结果指标与分析标签；排队中的任务可取消。
   地图右侧是「{防线}仿真」总览（LevelPanel）：只出溢水量 / 最大积水面积 / 最大积水深度 / 系统储水能力四项，
   切防线（L 等级）标签就出到该等级为止的对比（L1 / L1 vs L2 / L1、L2 与规划态）；地图播放或拖动时换成本帧当前值（mapNow）。
   右上角是防线切换（Line 1 / Line 2 Current / Line 2 Planned，同一场雨的三个任务，见后端
   simulation._line_runs）与「Compare」：开了对比，右侧总览换成三道并排（RunCompare）；
   对比开关记在 ?compare=1，切防线时带着走。差距分析页（Figma 04）还没做。
   多态任务（run.states，一个任务涵盖几种工况，如 Mussafah 四态 L1 / L2 / L2+A / L2+B）右上角换成态切换，
   当前态记在 ?state=（默认态不写），地图、总览、对比都按态取；总览里的防线与雨停后时长取该态自己的。
   效果分析（?view=perf，总览底部按钮进来，多态任务才有）：既有 = 第一态，方案 = ?vs=（不给取最后一态），
   时刻档 ?h=；地图画方案态水深、溢水点取第一态并按对照类别着色，右侧换 PerfPanel。
   全市任务（run.has_l2，#100 / #101 / #102）的「第二道现状」不跳到别的任务，而是本任务的第二道视图（?line=l2）：
   右侧总览的第二道溢水量、面积、水深按 L1 × (1 − 一维报告减少比例) 折算（sim_l2.estimate），储水能力取一维报告。
   多态任务（Mussafah #191）的总览四项全部取效果分析的 columns（交付实算，不折算）；切到 L2 态地图另叠已建成调蓄塘；对比按钮收起。 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { cancelSimulation, fetchSimulationPondingGe, fetchHotspotCircleSeries, fetchPondingPointSeries, fetchRdaHotspotCircleSeries, fetchRdaPointSeries, fetchSimulation, fetchSimulationEquipment, fetchSimulationL2, fetchSimulationOptions, fetchSimulationPerf } from '../api'
import AppIcon from '../components/AppIcon.vue'
import ResultMap from '../components/sim/ResultMap.vue'
import RunCompare from '../components/sim/RunCompare.vue'
import BalancePanel from '../components/sim/BalancePanel.vue'
import FacilityList from '../components/sim/FacilityList.vue'
import OutputsPanel from '../components/sim/OutputsPanel.vue'
import ParamsPanel from '../components/sim/ParamsPanel.vue'
import LevelPanel from '../components/sim/LevelPanel.vue'
import PerfPanel from '../components/sim/PerfPanel.vue'
import RdaPanels from '../components/rda/RdaPanels.vue'
import CityEquipment from '../components/sim/CityEquipment.vue'
import PondingPointsPanel from '../components/sim/PondingPointsPanel.vue'
import VolumeCurve from '../components/sim/VolumeCurve.vue'
import PointCurveModal from '../components/sim/PointCurveModal.vue'
import { usePondingPoints } from '../composables/usePondingPoints'
import RunHyetograph from '../components/sim/RunHyetograph.vue'
import RunSummary from '../components/sim/RunSummary.vue'
import SimShell from '../components/sim/SimShell.vue'
import { RDA_KEY, useRda } from '../composables/useRda'
import { locale, t } from '../i18n'
import { tr, trj } from '../i18n/rda'
import { useToastStore } from '../stores/toast'
import { PERF_COLOR, perfClass, perfPopup } from '../utils/perf'
import { analysisLabel, fmtInt, fmtLatLon, fmtWhen, stateCode, statusLabel } from '../utils/sim'

const props = defineProps({ id: { type: String, required: true } })
const route = useRoute()
const router = useRouter()
const toast = useToastStore()

const run = ref(null)
const options = ref(null)
const mapRef = ref(null)
const mapNow = ref(null)   // 地图时间过程下的本帧当前值，右侧总览跟着换
const layerMeta = ref(null)
const ponding = ref(null)
const hotspots = ref(null)   // /hotspots，ResultMap 取回后抛上来，右侧总览出报告口径对比
const pondErr = ref('')
const pondActive = ref(null)
const onPonding = (p, err) => {
  ponding.value = p
  pondErr.value = err
}

async function load() {
  try {
    ;[run.value, options.value] = await Promise.all([fetchSimulation(props.id), fetchSimulationOptions()])
    if (run.value.status === 'draft') router.replace(`/simulations/${props.id}/edit`)
  } catch (e) {
    toast.show(e.message, 'var(--red)')
  }
}
watch([() => props.id, locale], load, { immediate: true })
watch(() => props.id, () => {
  layerMeta.value = ponding.value = pondActive.value = hotspots.value = null
  pondErr.value = ''
})

const res = computed(() => run.value?.result)
const isCustom = computed(() => run.value?.rain_source === 'design_storm' && !run.value?.storm_preset_key)
const isPreset = computed(() => options.value?.storm_presets.find(p => p.key === run.value?.storm_preset_key))
const metrics = computed(() => [
  ['stillPond', res.value?.still_pond_count],
  ['drained', res.value?.drained_volume_m3],
  ...(run.value?.extent === 'RDA' ? [] : [['overflow', res.value?.overflow_node_count]]),   // RD-A 不出溢水点
  ['duration', res.value?.ponding_duration_h],
  ['validation', res.value?.validation_hit_rate_pct != null ? `${fmtInt(res.value.validation_hit_rate_pct)}%` : null],
])

const states = computed(() => run.value?.states || null)
const stateKey = computed(() => {
  if (!states.value) return null
  return states.value.some(s => s.key === route.query.state) ? route.query.state : run.value.state_default
})
const curState = computed(() => states.value?.find(s => s.key === stateKey.value) || null)

/* ---- RD-A 全套成果（extent = RDA）：这条任务多出一组明细（积水点算法 / L3 派单 / 设施 / 判据 / 措施），
   不出「溢水点」「溢水量」——RD-A 有自己的积水点算法。数据与选中项在 useRda，和 /rda 页共用。 ---- */
const isRda = computed(() => run.value?.extent === 'RDA')
/** 全市任务（#100–102 / #561 / #1068）：总览只留总积水量 / 最大积水面积 / 最大积水深度 / 系统储水能力，
    不出溢水点（图层 / 列表 / 个数）和溢水量，与 RD-A 一样 */
const isCity = computed(() => !!run.value?.has_layers && (run.value?.extent === 'City-wide' || !!run.value?.has_ponding_points))   // engine 推演的区域任务（extent = region.toml 的 title，如 RD-A）有模拟积水点，同样按全市口径显示
const isCity0 = computed(() => run.value?.extent === 'City-wide')   // 全市任务的设备在「应对设备配置」里，不用这张清单
const hideOverflow = computed(() => isRda.value || isCity.value)
const rdaState = computed(() => (isRda.value ? RDA_KEY[stateKey.value] || 'OPTCS2' : null))
const rda = useRda(rdaState, { onError: msg => toast.show(msg, 'var(--red)') })
const rdaTab = ref('ponding')
const LEVEL_COLOR = { 关键: '#d9434b', 高: '#e0702e', 中: '#e0a44e', 无暴露: '#7a838c' }
/* ---- 全市一二维任务（如 #1068 ADM 100 m）的模拟积水点：口径同 RD-A，数据与选中项在 usePondingPoints；
   只有后端给了 points/ 的任务（run.has_ponding_points）才有，RD-A 自己走上面那套。 ---- */
const ptsRunId = computed(() => (!isRda.value && run.value?.has_ponding_points ? run.value.id : null))
const pts = usePondingPoints(ptsRunId, stateKey, { onError: msg => toast.show(msg, 'var(--red)') })
const simPoints = computed(() => (isRda.value ? (rda.ponding.value?.points || []).map(p => ({
  id: p.id, lat: p.lat, lon: p.lon, vol: p.vol_peak_m3, color: LEVEL_COLOR[p.l3?.level] || '#d9434b',
  label: `${p.id} · ${p.hmax_m} m · ${Math.round(p.vol_peak_m3).toLocaleString('en-US')} m³`,
})) : pts.enabled.value ? pts.mapPoints.value : null))
const simSelected = computed(() => (isRda.value ? rda.selPoint.value : pts.enabled.value ? pts.selPoint.value : null))
watch(isRda, on => { if (on) { rda.ensureShared(); rda.loadState() } }, { immediate: true })
/* ---- 点一个积水点弹出它的积水趋势（逐帧积水量 / 面积 / 最大水深）。两种点：
   · point：模型算出的积水点。RD-A 按清单里这个点的经纬度取，全市按编号取；阈值用当前清单生效的那一套，曲线峰值才与列表一致。
   · hotspot：用户关注的积水点（客户热点）。取它 200 m 圆内的逐帧过程，阈值用「圆内峰值积水量」那一栏正在用的，峰值与那一栏一致。
   原来的地图弹窗、热点表、积水点详情卡片都照旧，趋势是在它们之上多弹一个框。 ---- */
const curve = ref(null)   // { id, kind, loading, error, series }
let curveSeq = 0
const nf = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: d }))
/** 弹框里随曲线一起给的信息 = 原来点这个点时看到的那份：积水点是下面详情卡片（PondingTab）的内容，关注积水点是地图弹窗（ResultMap.hotPopup）的内容 */
const curveFacts = computed(() => {
  const c = curve.value
  if (!c) return []
  if (c.kind === 'hotspot') {
    const h = (hotspots.value?.points || []).find(x => x.id === c.id)
    if (!h) return []
    const sts = hotspots.value?.states || []
    const st = h.stats?.[sts.includes(stateKey.value) ? stateKey.value : sts[0]] || {}
    const nr = st.near_200m || {}
    const cat = st.catchment
    const out = [[t('sim.hotspots.loc'), [h.district, h.criticality, h.location].filter(Boolean).join(' · ') || '—'],
      [t('sim.hotspots.near'), `${nf(nr.max_depth_m)} m`]]
    if (cat) {
      out.push([t('sim.hotspots.a30'), `${nf(cat.area_ge_0_30_ha)} ha`],
        [t('sim.hotspots.vol'), `${cat.max_volume_m3 == null ? '—' : fmtInt(cat.max_volume_m3)} m³（T+${nf(cat.max_volume_h)} h）`])
    } else {
      out.push([t('sim.hotspots.a30Near'), `${nf(nr.area_ge_0_30_ha)} ha`],
        [t('sim.hotspots.volNear'), `${nr.max_volume_m3 == null ? '—' : fmtInt(nr.max_volume_m3)} m³${nr.t_max_depth_h == null ? '' : `（${t('sim.hotspots.deepestAt')} T+${nf(nr.t_max_depth_h)} h）`}`])
    }
    if (h.lat != null) out.push([t('sim.hotspots.coord'), fmtLatLon(h)])
    return out
  }
  const p = ((isRda.value ? rda.ponding.value : pts.ponding.value)?.points || []).find(x => x.id === c.id)
  if (!p) return []
  const out = [
    [tr('最大水深'), `${nf(p.hmax_m)} m（${tr('地面高程')} ${nf(p.ground_z_m)} m）${p.t_hmax_h != null ? `，${tr('出现在')} ${nf(p.t_hmax_h)} h` : ''}`],
    [tr('面积'), `${fmtInt(p.area_m2)} m²（${tr('其中')} ≥0.30 m：${fmtInt(p.area_ge030_m2)} m²）`],
  ]
  if (p.area_peak_m2 != null) out.push([tr('最大积水面积'), `${fmtInt(p.area_peak_m2)} m²，${tr('出现在')} ${nf(p.t_area_peak_h)} h`])
  out.push([tr('峰值积水量'), `${fmtInt(p.vol_peak_m3)} m³，${tr('出现在')} ${nf(p.t_peak_h)} h`], [tr('持续'), `${nf(p.duration_h)} h`],
    [tr('退水'), p.receded ? `${tr('已退')} · ${tr('峰值后')} ${nf(p.recede_h)} h` : `${tr('未退')} · ${tr('到 {t} h 仍有 {v} m³', { t: p.t_end_h, v: fmtInt(p.vol_end_m3) })}`],
    [tr('最近客户热点'), `${p.nearest_client_hotspot}（${fmtInt(p.nearest_client_hotspot_dist_m)} m）`],
    [tr('200 m 内热点'), p.near_hotspots?.length ? p.near_hotspots.map(h => `${h.hotspot} ${h.dist_m} m`).join('、') : tr('无')])
  if (p.l3) out.push([tr('结论'), tr(p.l3.verdict)], [tr('定级依据'), trj(p.l3.basis)], [tr('L3 手段'), tr(p.l3.measure)])
  return out
})
async function openCurve(id, kind = 'point') {
  const my = ++curveSeq
  curve.value = { id, kind, loading: true, error: '', series: null }
  try {
    let s
    if (kind === 'hotspot') {
      s = isRda.value
        ? await fetchRdaHotspotCircleSeries(rdaState.value, id, rda.buf.bp.value)
        : await fetchHotspotCircleSeries(run.value.id, stateKey.value, id, pts.buf.bp.value)
    } else if (isRda.value) {
      const p = (rda.ponding.value?.points || []).find(x => x.id === id)
      if (!p) throw new Error(t('sim.pointCurve.empty'))
      const { depth, area, dur } = rda.pp.value
      s = await fetchRdaPointSeries(rdaState.value, p.lon, p.lat, depth, area, dur)
    } else {
      const { depth, area, dur } = pts.pp.value
      s = await fetchPondingPointSeries(run.value.id, stateKey.value, id, depth, area, dur)
    }
    if (my === curveSeq) curve.value.series = s
  } catch (e) {
    if (my === curveSeq) curve.value.error = e.message
  } finally {
    if (my === curveSeq) curve.value.loading = false
  }
}
const closeCurve = () => { curveSeq++; curve.value = null }
watch([() => props.id, stateKey], closeCurve)
/** 面板里点行：地图飞过去（热点就飞到它并开原来的弹窗）；点的是积水点 / 关注积水点就再弹趋势 */
function onLocate(e) {
  if (e.kind === 'hotspot' && e.id) mapRef.value?.focusHotspot(e.id)
  else if (e.lat != null && e.lon != null) mapRef.value?.flyToPoint(e.lat, e.lon)
  if ((e.kind === 'point' || e.kind === 'hotspot') && e.id) openCurve(e.id, e.kind)
}
function onSimPoint(id) {
  openCurve(id)
  if (pts.enabled.value) {
    pts.selPoint.value = id
    document.querySelector('.sd-pts')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  rdaTab.value = 'ponding'
  rda.selPoint.value = id
  document.querySelector('.sd-rda')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
const setState = k => {
  eqTab.value = null
  router.replace({ query: { ...route.query, state: k === run.value?.state_default ? undefined : k } })
}

/* 全市应对设备配置：右上角的 L2 sugg / L3 两个按钮，和 L1 / L2 同一排；点哪个，右栏就显示哪一档，再点 L1 / L2 回到原来的右栏。
   数据是成果目录里的两份文件，没有的任务（接口 404）不出按钮。 */
const equip = ref(null)
const eqTab = ref(null)   // null | 'fixed'（L2 sugg）| 'mobile'（L3）
watch(() => [isCity.value, run.value?.id], async ([city, rid]) => {
  equip.value = eqTab.value = null
  if (!city || rid == null) return
  try {
    const x = await fetchSimulationEquipment(rid)
    if (run.value?.id === rid) {
      equip.value = x
      if (['fixed', 'mobile'].includes(route.query.eq)) eqTab.value = route.query.eq   // 链接带 ?eq=fixed / mobile 直接打开这一档
    }
  } catch { /* 404 = 这个任务没有这份数据，不出按钮 */ }
}, { immediate: true })
/* 各态「水深 ≥ 15 cm」积水量（ponding_ge.json）：全市任务随 /equipment 下发，engine 推演的区域任务没有设备配置，单独取 */
const pge = ref(null)
watch(() => [isCity.value, run.value?.id], async ([city, rid]) => {
  pge.value = null
  if (!city || rid == null) return
  try {
    const x = await fetchSimulationPondingGe(rid)
    if (run.value?.id === rid) pge.value = x
  } catch { /* 404 = 没有 ponding_ge.json */ }
}, { immediate: true })
const pickEq = k => { eqTab.value = eqTab.value === k ? null : k }
const stateTitle = s => stateCode(s.same_as ? t('sim.detail.sameAs', { label: s.label, as: s.same_as }) : s.label)
watch(stateKey, () => {
  layerMeta.value = ponding.value = pondActive.value = hotspots.value = null
  pondErr.value = ''
})
/** 总览吃的 run：多态时防线、雨停后时长换成当前态的（L2+A / L2+B 只演算到 5 h，不能沿用 L1 的） */
const viewRun = computed(() => {
  const r = run.value
  const s = curState.value
  if (!r || !s) return r
  const simH = layerMeta.value?.domain?.simulation_duration_hours
  return {
    ...r,
    defence_line_key: s.defence_line_key,
    defence_line_label: t('sim.detail.stateTitle', { label: stateCode(s.label), line: s.defence_line_label }),
    after_rain_max_h: simH != null && r.duration_h != null ? Math.round((simH - Number(r.duration_h)) * 10) / 10 : r.after_rain_max_h,
  }
})

const compare = computed(() => route.query.compare === '1')
const setCompare = on => router.replace({ query: { ...route.query, compare: on ? '1' : undefined } })
const openLine = id => id && id !== run.value?.id && router.push({ path: `/simulations/${id}`, query: route.query })
const lineTitle = lr =>
  !lr.run_id ? t('sim.detail.lineNone') : !lr.has_layers ? t('sim.detail.lineNoLayers', { id: lr.run_id }) : `#${lr.run_id}`

/* ---------------- 第二道现状（全市任务） ---------------- */
const l2View = computed(() => !!run.value?.has_l2 && route.query.view !== 'perf' &&
  (states.value ? curState.value?.defence_line_key === 'line2_current' : route.query.line === 'l2'))
/** 规划态（L2+planned / L2+suggested / L2+B）：地图注记换成该态，对比按钮收起 */
const planView = computed(() => !!run.value?.has_l2 && route.query.view !== 'perf' && !!states.value &&
  curState.value?.defence_line_key === 'line2_planned')
const l2 = ref(null)
const l2Err = ref('')
const setL2 = on => router.replace({ query: { ...route.query, line: on ? 'l2' : undefined, compare: undefined } })
/** 全市任务（#100–102）与多态 #561 一样只出 L1 / L2 两个 tab；第二道规划没有演算，不出 */
const lineTabs = computed(() => (run.value?.has_l2
  ? run.value.line_runs.filter(lr => lr.defence_line_key !== 'line2_planned') : run.value?.line_runs || []))
/** 线路 tab：全市任务的第二道现状指向本任务的第二道视图，第一道回到本视图 */
const lineOn = lr => (run.value?.has_l2 && lr.run_id === run.value.id ? !l2View.value
  : run.value?.has_l2 && lr.defence_line_key === 'line2_current' ? l2View.value : lr.run_id === run.value?.id)
function clickLine(lr) {
  if (run.value?.has_l2 && lr.defence_line_key === 'line2_current') return setL2(true)
  if (run.value?.has_l2 && lr.run_id === run.value.id) return setL2(false)
  openLine(lr.run_id)
}
async function loadL2() {
  if (!(l2View.value || planView.value) || (l2.value && l2.value.run_id === run.value.id)) return
  l2Err.value = ''
  try {
    l2.value = { ...(await fetchSimulationL2(run.value.id)), run_id: run.value.id }
  } catch (e) {
    l2.value = null
    l2Err.value = e.message
  }
}
watch([l2View, planView], loadL2, { immediate: true })
watch(() => props.id, () => { l2.value = null })

/* ---------------- 效果分析 ---------------- */
const perf = computed(() => route.query.view === 'perf' && !!states.value)
const perfOptions = computed(() => states.value?.slice(1) || [])
const perfVs = computed(() => {
  const o = perfOptions.value
  return o.some(s => s.key === route.query.vs) ? route.query.vs : o[o.length - 1]?.key
})
const perfH = computed(() => (route.query.h != null ? Number(route.query.h) : null))
const perfData = ref(null)
const perfErr = ref('')
/** 从规划态进效果分析，方案默认就是这一态；其余态进去取默认方案（最后一态） */
const openPerf = () => router.push({ query: {
  ...route.query, view: 'perf', compare: undefined, state: undefined,
  vs: planView.value && stateKey.value !== perfOptions.value.at(-1)?.key ? stateKey.value : undefined,
} })
const closePerf = () => router.push({ query: { ...route.query, view: undefined, vs: undefined, h: undefined } })
const setVs = k => router.replace({ query: { ...route.query, vs: k === perfOptions.value.at(-1)?.key ? undefined : k } })
const setH = h => router.replace({ query: { ...route.query, h: h === 2 ? undefined : h } })

let perfSeq = 0
async function loadPerf() {
  if (!perf.value) return
  const seq = ++perfSeq
  perfErr.value = ''
  try {
    const d = await fetchSimulationPerf(props.id, perfVs.value, perfH.value)
    if (seq === perfSeq) perfData.value = d
  } catch (e) {
    if (seq === perfSeq) {
      perfData.value = null
      perfErr.value = e.message
    }
  }
}
watch([perf, perfVs, perfH, () => props.id, locale], loadPerf)
// 方案或时刻一换，旧数字作废（不拿别的方案 / 时刻的分类顶着）
watch([perfVs, perfH, () => props.id], () => { perfData.value = null })

const perfFit = computed(() => (perfData.value?.vs.key === perfVs.value ? perfData.value : null))
const pondClass = computed(() => (perf.value && perfFit.value ? perfClass(perfFit.value) : null))
const pondPopup = computed(() => (perf.value && perfFit.value ? perfPopup(perfFit.value) : null))
const pondLegend = computed(() => {
  const d = perfFit.value
  if (!perf.value || !d) return null
  return {
    title: t('sim.perf.legend', { h: d.h }),
    rows: Object.keys(PERF_COLOR).map(k => ({ color: PERF_COLOR[k], label: t(`sim.perf.cat.${k}`), n: d.category_counts[k] })),
    note: t('sim.perf.catRule', { pct: d.reduced_pct, d: d.active_depth_m }),
  }
})

async function cancel() {
  try {
    run.value = await cancelSimulation(run.value.id)
    toast.show(t('sim.detail.cancelled'))
  } catch (e) {
    toast.show(e.message, 'var(--red)')
  }
}
</script>

<template>
  <SimShell :users="options?.users || []">
    <div class="wiz-head">
      <button class="wiz-back" :title="t('sim.title')" @click="router.push('/simulations')"><AppIcon name="back" :size="16" /></button>
      <h1 v-if="perf" class="sim-h1 sd-crumb">
        <button @click="closePerf">{{ t('sim.detail.title', { id }) }}</button><span>/</span>{{ t('sim.perf.crumb') }}
      </h1>
      <h1 v-else class="sim-h1">{{ t('sim.detail.title', { id }) }}</h1>
      <span v-if="run?.job_id" class="sim-tag sd-job" title="job_id">{{ run.job_id }}</span>
      <span v-if="perf && perfFit" class="sim-tag sd-variant">{{ stateCode(perfFit.base.label) }} vs {{ stateCode(perfFit.vs.label) }}</span>
      <span v-else-if="l2View && !planView && !(curState && !curState.same_as)" class="sim-tag sd-variant">{{ t('sim.l2.tag') }}</span>
      <span v-else-if="curState || run?.variant_label" class="sim-tag sd-variant">{{ curState ? stateTitle(curState) : stateCode(run.variant_label) }}</span>
      <RouterLink v-if="run?.extent === 'RDA'" to="/rda" class="sim-tag sd-rda">{{ tr('RD-A 完整页（逐帧播放 · 概览）') }} ›</RouterLink>
      <span v-if="run" :class="'st-' + run.status" style="width: auto">
        <span class="sim-dot" />{{ statusLabel(run) }}
      </span>
      <div v-if="perf" class="sd-lines">
        <span class="sd-vs">{{ stateCode(states[0].key) }} vs</span>
        <div class="sd-seg" role="tablist">
          <button v-for="s in perfOptions" :key="s.key" role="tab" :class="{ on: s.key === perfVs }"
                  :aria-selected="s.key === perfVs" :title="stateTitle(s)" @click="setVs(s.key)">
            {{ stateCode(s.key) }}
          </button>
        </div>
      </div>
      <div v-else-if="run?.line_runs" class="sd-lines">
        <div v-if="states" class="sd-seg" role="tablist">
          <button v-for="s in states" :key="s.key" role="tab" :class="{ on: s.key === stateKey && !eqTab }"
                  :aria-selected="s.key === stateKey && !eqTab" :title="`${stateTitle(s)} · ${s.defence_line_label}`"
                  @click="setState(s.key)">
            {{ stateCode(s.key) }}
          </button>
          <template v-if="equip">
            <button role="tab" :class="{ on: eqTab === 'fixed' }" :aria-selected="eqTab === 'fixed'" :title="t('sim.equip.tabFixed', { p: `${(equip.fixed.inputs.volume_fixed_m3 / equip.fixed.inputs.remaining_ponding_after_L2_m3 * 100).toFixed(1)}%` })" @click="pickEq('fixed')">
              {{ t('sim.equip.btnFixed') }}
            </button>
            <button role="tab" :class="{ on: eqTab === 'mobile' }" :aria-selected="eqTab === 'mobile'" :title="t('sim.equip.tabMobile', { p: `${(equip.fixed.inputs.volume_mobile_m3 / equip.fixed.inputs.remaining_ponding_after_L2_m3 * 100).toFixed(1)}%` })" @click="pickEq('mobile')">
              {{ t('sim.equip.btnMobile') }}
            </button>
          </template>
        </div>
        <div v-else class="sd-seg" role="tablist">
          <button v-for="lr in lineTabs" :key="lr.defence_line_key" role="tab"
                  :class="{ on: lineOn(lr), empty: lr.run_id && !lr.has_layers && !(run.has_l2 && lr.defence_line_key === 'line2_current') }"
                  :aria-selected="lineOn(lr)"
                  :disabled="!lr.run_id && !(run.has_l2 && lr.defence_line_key === 'line2_current')"
                  :title="run.has_l2 && lr.defence_line_key === 'line2_current' ? t('sim.l2.tabHint') : lineTitle(lr)"
                  @click="clickLine(lr)">
            {{ t(`sim.detail.lineTab.${lr.defence_line_key}`) }}
          </button>
        </div>
        <button v-if="!l2View && !planView" class="sd-cmp" :class="{ on: compare }" :aria-pressed="compare" @click="setCompare(!compare)">
          <AppIcon name="compare" :size="14" />{{ t('sim.compare.btn') }}
        </button>
      </div>
      <button v-if="run && ['queued'].includes(run.status)" class="sim-btn" @click="cancel">
        {{ t('sim.detail.cancel') }}
      </button>
    </div>

    <div v-if="run?.has_layers && perf" class="sd-grid">
      <ResultMap :key="`${run.id}|perf|${perfVs}`" :run-id="run.id" :state="perfVs" :ponding-state="states[0].key" :no-ledger="hideOverflow"
                 :pond-class="pondClass" :pond-popup="pondPopup" :pond-legend="pondLegend" :show-residual="false" fit-ponds
                 @hotspots="hotspots = $event" />
      <PerfPanel :data="perfFit" :error="perfErr" :rain="run.rain_label" :hotspots="hotspots" @h="setH" />
    </div>
    <div v-else-if="run?.has_layers" class="sd-grid">
      <ResultMap ref="mapRef" :key="`${run.id}|${stateKey || ''}`" :run-id="run.id" :state="stateKey"
                 :l2-ponds="l2View && !states && l2?.ponds?.length ? l2.ponds : null"
                 :l2-note="isRda ? '' : planView ? t('sim.l2.mapNotePlan', { s: stateCode(stateKey) }) : l2View ? t(!states ? 'sim.l2.mapNote' : curState?.same_as ? 'sim.l2.mapNoteState' : 'sim.l2.mapNoteReal', { s: stateCode(stateKey) }) : ''"
                 :no-overflow="hideOverflow" :no-ledger="hideOverflow" :no-pond-points="pts.enabled.value" :sim-points="simPoints" :sim-selected="simSelected" @simpoint="onSimPoint" @hotspotpick="openCurve($event, 'hotspot')"
                 @meta="layerMeta = $event" @ponding="onPonding" @active="pondActive = $event" @now="mapNow = $event" @hotspots="hotspots = $event" />
      <!-- 全市：右上角点 L2 sugg（固定设施）/ L3（移动泵与罐车）时，右栏换成应对设备配置；点 L1 / L2 回到原来的右栏 -->
      <CityEquipment v-if="isCity && equip && eqTab" :data="equip" :tab="eqTab" :rain-label="run.rain_label || ''" />
      <RunCompare v-else-if="compare && !l2View && !planView" :run="run" :state="stateKey" :meta="layerMeta" :ponding="ponding"
                  @open="openLine" @state="setState" @locate="mapRef?.flyToPoint($event.lat, $event.lon)" />
      <LevelPanel v-else :run="viewRun" :states="states" :state-key="stateKey" :l2-view="l2View" :meta="layerMeta" :no-overflow="hideOverflow" :no-pond-points="pts.enabled.value"
                  :ponding="ponding" :active="pondActive" :now="mapNow" :hotspots="hotspots" :vol-ge="equip?.ponding_ge || pge" :city-ge="isCity0" @focus="mapRef?.focusPond($event)" @perf="openPerf" />
    </div>
    <RunCompare v-else-if="run && compare" class="sd-cmp-solo" :run="run" @open="openLine" />

    <!-- 水量账 / 推演参数 / 成果数据位置：整行放在地图和总览下面，不进网格（总览吸顶，放进去滚动时会叠在它们上面） -->
    <div v-if="run && !perf" class="sd-extra">
      <FacilityList v-if="states && states.length > 1 && !isCity0" :run-id="run.id" :states="states" />
      <BalancePanel v-if="states" :run-id="run.id" :states="states" />
      <ParamsPanel :run-id="run.id" />
      <OutputsPanel :run-id="run.id" />
    </div>

    <!-- RD-A / 全市：总积水量随时间的折线，随地图上选的态走 -->
    <VolumeCurve v-if="(isRda || isCity) && run?.has_layers && !perf" :key="`${run.id}|${stateKey || ''}`"
                 :run-id="run.id" :state="stateKey" :rain-min="layerMeta?.model_configuration?.rainfall_duration_minutes ?? null" />

    <!-- RD-A：积水点算法 / L3 派单 / 设施 / 判据 / 措施，随地图上选的态走；点行在地图上定位 -->
    <section v-if="isRda && run?.has_layers && !perf" class="sim-card sd-rda">
      <RdaPanels v-model:tab="rdaTab" :rda="rda" :label="curState?.label || ''" @locate="onLocate" />
    </section>

    <!-- 全市一二维任务的模拟积水点：口径同 RD-A（阈值可调、热点覆盖率、最大值时刻），随地图上选的态走 -->
    <section v-if="pts.enabled.value && run?.has_layers && !perf" class="sim-card sd-rda sd-pts">
      <PondingPointsPanel :pts="pts" @locate="onLocate" />
    </section>

    <PointCurveModal v-if="curve" :id="curve.id" :kind="curve.kind" :facts="curveFacts" :series="curve.series" :loading="curve.loading" :error="curve.error"
                     :rain-min="layerMeta?.model_configuration?.rainfall_duration_minutes ?? null" @close="closeCurve" />

    <div v-if="run && options" class="wiz-body" style="margin-top: 24px">
      <p v-if="!run.has_layers" class="sim-empty" style="padding-top: 0">{{ t('sim.detail.resultsPending') }}</p>
      <!-- 设计暴雨一律按同一套雨量 / 历时画派生雨型，同雨深历时的任务（如全市 61 mm/2h 与 Mussafah_00）图必须一致 -->
      <RunHyetograph v-if="run.rain_source === 'design_storm' && (!run.has_layers || layerMeta)"
                     :run="run" :is-custom="isCustom" />
      <section class="sim-card wiz-confirm">
        <div class="wiz-kv">
          <span class="k">{{ t('sim.history.cols.basis') }}</span><span class="v">{{ run.defence_basis }}<template v-if="!states && run.variant_label"><span class="sep">｜</span>{{ run.variant_label }}</template></span>
          <span class="k">{{ t('sim.history.cols.start') }}</span><span class="v">{{ fmtWhen(run.started_at) }}</span>
          <span class="k">{{ t('sim.history.cols.end') }}</span>
          <span class="v">{{ fmtWhen(run.ended_at) }}<template v-if="run.ended_at && run.ended_is_estimate">{{ t('sim.history.est') }}</template></span>
          <span class="k">{{ t('sim.history.cols.analyses') }}</span>
          <span class="v">
            <template v-if="run.analyses.length">
              <span v-for="a in run.analyses" :key="a.id" class="sim-tag" :class="a.kind" style="margin-right: 6px">{{ analysisLabel(a) }}</span>
            </template>
            <template v-else>—</template>
          </span>
        </div>

        <div class="wiz-block">
          <div class="bh">{{ t('sim.detail.result') }}</div>
          <div v-if="res" class="wiz-kv">
            <template v-for="[k, v] in metrics" :key="k">
              <span class="k">{{ t(`sim.detail.${k}`) }}</span>
              <span class="v" :class="{ none: v == null }">{{ v == null ? '—' : typeof v === 'string' ? v : fmtInt(v) }}</span>
            </template>
          </div>
          <p v-else class="sim-empty" style="padding: 0">{{ t('sim.detail.noResult') }}</p>
        </div>

        <RunSummary
          :run="run"
          :lines="options.defence_lines"
          :start-utc="run.model_start_utc"
          :is-design-basis="!!isPreset?.is_design_basis"
          :is-custom="isCustom"
        />
      </section>
    </div>
  </SimShell>
</template>

<style scoped>
/* 地图在左、总览在右；窄屏总览落到地图下面 */
.sd-grid { display: grid; grid-template-columns: minmax(0, 1fr) 420px; gap: 20px; align-items: start; }
.sd-grid > .ro { margin-top: 20px; position: sticky; top: 20px; max-height: calc(100vh - 40px); overflow-y: auto; }   /* 吸顶但不超过一屏，超出的在面板里滚 */
.sd-extra { margin-top: 20px; display: flex; flex-direction: column; gap: 20px; }
.sd-variant { background: var(--blue-bg); color: var(--blue-lt); }
.sd-job { font-family: ui-monospace, monospace; opacity: 0.85; }
.sd-rda { margin-top: 20px; padding: 4px 6px 10px; }
.sd-lines { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.sd-seg { display: flex; gap: 2px; padding: 3px; border-radius: 999px; background: rgba(255, 255, 255, 0.04); }
.sd-seg button { border: 0; background: none; color: var(--fg-2); font: inherit; font-size: 14px; padding: 4px 10px;
  border-radius: 999px; cursor: pointer; white-space: nowrap; }
.sd-seg button:hover:not(:disabled):not(.on) { color: var(--fg); }
.sd-seg button.on { background: var(--blue); color: #fff; font-weight: 600; cursor: default; }
.sd-seg button.empty:not(.on) { color: var(--muted); }
.sd-seg button:disabled { color: var(--faint); cursor: not-allowed; }
.sd-cmp { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px; border-radius: 6px;
  border: 1px solid var(--line); background: none; color: var(--fg); font: inherit; font-size: 14px; font-weight: 600;
  cursor: pointer; }
.sd-cmp:hover { background: rgba(255, 255, 255, 0.05); }
.sd-cmp.on { border-color: var(--blue); background: var(--blue-bg); color: var(--blue-lt); }
.sd-crumb { display: flex; align-items: baseline; gap: 10px; }
.sd-crumb button { border: 0; background: none; padding: 0; font: inherit; font-weight: 400; color: var(--fg-2); cursor: pointer; }
.sd-crumb button:hover { color: var(--fg); }
.sd-crumb span { color: var(--faint); font-weight: 400; }
.sd-vs { color: var(--muted); font-size: 13px; }
.sd-cmp-solo { max-width: 560px; margin-top: 20px; }
@media (max-width: 1280px) {
  .sd-grid { grid-template-columns: minmax(0, 1fr); }
  .sd-grid > .ro { position: static; max-height: none; overflow-y: visible; }
}
</style>
