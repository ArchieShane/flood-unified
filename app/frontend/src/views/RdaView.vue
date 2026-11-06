<script setup>
/* RD-A 61 mm / 1 h 全套成果页（路由 /rda）。
   一场雨四个状态（L1 / L2 / L2+planned / L2+suggested），逐状态看：
   二维最大水深与逐帧、模型自己算出来的积水点（SIM-态-序号，阈值可调重算）、L3 承接与派单、
   设施图层、四项判据 A–D；L2+suggested 另有逐项措施（设计参数、怎么排水、造价、审批、WA-726 合规）。
   数据全部来自后端 /api/rda/*，直接读交付目录，页面不算任何数。

   右栏的明细（积水点 / L3 / 设施 / 判据 / 措施）是 `RdaPanels`，和仿真任务详情页共用；本页自己管地图与概览。
   中英双语：文案与成果里的中文字段用 `tr('…')` 精确匹配（词典在 i18n/rda.js），匹配不上原样显示中文。 */
import L from 'leaflet'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch, watchEffect } from 'vue'

import {
  fetchRdaCriteria, fetchRdaFrame, fetchRdaFrameIndex, fetchRdaGrid, fetchRdaHotspots, fetchRdaHotspotSeries,
  fetchRdaMaxDepth, fetchRdaOverview, fetchRdaPondingAreas, fetchRdaRain,
} from '../api'
import MiniChart from '../components/rda/MiniChart.vue'
import RdaPanels from '../components/rda/RdaPanels.vue'
import SimShell from '../components/sim/SimShell.vue'
import { BASEMAPS } from '../constants'
import { ledgerId, useRda } from '../composables/useRda'
import { locale } from '../i18n'
import { tr } from '../i18n/rda'
import { useToastStore } from '../stores/toast'
import '../styles/rda.css'

const toast = useToastStore()
watchEffect(() => { document.title = tr('RD-A 全套成果') })

/* ---------------- 状态 ---------------- */
const overview = ref(null)
const stateKey = ref('OPTCS2')
const tab = ref('overview')
const hotspotsGeo = ref(null)
const loading = ref(false)
const panels = ref(null)

/** 积水点 / L3 / 判据 / 设施 / 措施：数据与选中项（和仿真详情页共用） */
const rda = useRda(stateKey, { onError: msg => toast.show(msg, 'var(--red)') })
const { ponding, crit, fac, suggested } = rda

const layersOn = ref({ maxDepth: true, areas: true, points: true, facilities: true, hotspots: true })

const states = computed(() => overview.value?.states || [])
const cur = computed(() => states.value.find(s => s.key === stateKey.value))
const isSugg = computed(() => stateKey.value === 'OPTCS2')

/* ---- 对比基准、逐帧、热点过程 ---- */
const baseSel = ref('prev')                 // 'prev' = 上一个态；或某个态 key
const critAll = ref({})                     // 四个态各自的判据（热点 200 m 内数字）
const rainSeries = ref([])
const hsSeries = ref(null)                  // 热点 50 m 内水深过程
const gridData = ref(null)
const frameIdx = ref({})                    // 态 → 逐帧全域指标
const playMode = ref('frame')               // frame 逐帧 | max 全程最大水深 | diff 和对比基准的差
const frameI = ref(8)
const playing = ref(false)
let playTimer = null

const stateKeys = computed(() => states.value.map(s => s.key))
const baseKey = computed(() => {
  if (baseSel.value === 'prev') {
    const i = stateKeys.value.indexOf(stateKey.value)
    return i > 0 ? stateKeys.value[i - 1] : null
  }
  return baseSel.value === stateKey.value ? null : baseSel.value
})
const stateLabel = key => tr(states.value.find(s => s.key === key)?.label || '')
const baseLabel = computed(() => (baseKey.value ? stateLabel(baseKey.value) : ''))
const shortLabel = label => label.split(' ')[0]
const fi = computed(() => frameIdx.value[stateKey.value])
const fiBase = computed(() => (baseKey.value ? frameIdx.value[baseKey.value] : null))
const frameNow = computed(() => {
  const x = fi.value
  if (!x) return null
  const i = Math.min(frameI.value, x.t_h.length - 1)
  return { t: x.t_h[i], wet: x.wet10_ha[i], dmax: x.dmax_m[i] }
})

/** 本态 vs 基准 的四个对比数字（全部取自成果的判据评估，不另算） */
function domainStats(key) {
  const c = critAll.value[key]
  if (!c) return null
  return {
    ge030: c.domain.ge030_ha,
    hotGe030: c.hotspots.reduce((a, h) => a + (h.ge030_ha || 0), 0),
    peakVol: c.domain.peak_vol_m3,
    hotMax: Math.max(...c.hotspots.map(h => h.max_m || 0)),
  }
}
const kpiCompare = computed(() => {
  const a = domainStats(stateKey.value)
  const b = baseKey.value ? domainStats(baseKey.value) : null
  if (!a) return []
  const n = cur.value?.hotspots_total ?? 11
  const defs = [
    [tr('全域 · ≥0.30 m 积水面积'), 'ge030', 'ha', 2],
    [tr('{n} 个热点 200 m 内 · ≥0.30 m', { n }), 'hotGe030', 'ha', 2],
    [tr('全域 · 最大积水量'), 'peakVol', 'm³', 0],
    [tr('{n} 个热点 200 m 内最大水深', { n }), 'hotMax', 'm', 2],
  ]
  return defs.map(([label, k, unit, dg]) => ({ label, unit, value: a[k].toFixed(dg), delta: b ? pct(a[k], b[k]) : null }))
})
function pct(v, base) {
  if (!base) return null
  const p = ((v - base) / base) * 100
  return { txt: `${p > 0 ? '+' : ''}${p.toFixed(1)}%`, good: p < 0 }
}
/** 差值：负 = 改善（绿） */
function diffCell(v, b, dg = 2) {
  if (b == null || v == null) return null
  const d = v - b
  if (Math.abs(d) < 0.5 * 10 ** -dg) return null
  return { txt: (d > 0 ? '+' : '') + d.toFixed(dg), good: d < 0 }
}
const hotRows = computed(() => {
  const a = critAll.value[stateKey.value]
  if (!a) return []
  const b = baseKey.value ? critAll.value[baseKey.value] : null
  const sa = hsSeries.value?.states[stateKey.value]
  const sb = baseKey.value ? hsSeries.value?.states[baseKey.value] : null
  return a.hotspots.map(h => {
    const hb = b?.hotspots.find(x => x.id === h.id)
    return {
      id: h.id, all_pass: h.all_pass, pass: h.pass,
      ge030: h.ge030_ha, ge030D: diffCell(h.ge030_ha, hb?.ge030_ha),
      max: h.max_m, maxD: diffCell(h.max_m, hb?.max_m),
      dur: sa?.[h.id]?.ge030_h, durD: diffCell(sa?.[h.id]?.ge030_h, sb?.[h.id]?.ge030_h),
      curve: sa?.[h.id], curveBase: sb?.[h.id],
    }
  })
})
const hotInfo = computed(() => Object.fromEntries((hotspotsGeo.value?.features || []).map(f => [f.properties.hotspot_id, f.properties])))
const rainChart = computed(() => {
  const r = rainSeries.value
  if (!r.length) return []
  return [{ x: r.map(v => v.t_h), y: r.map(v => v.intensity_mm_h), color: '#4786c9', bars: true }]
})
const areaChart = computed(() => {
  const out = []
  if (fiBase.value) out.push({ x: fiBase.value.t_h, y: fiBase.value.wet10_ha, color: 'rgba(255,255,255,0.35)', width: 1.2 })
  if (fi.value) out.push({ x: fi.value.t_h, y: fi.value.wet10_ha, color: '#4786c9', width: 2 })
  return out
})
function curveSeries(h) {
  const t = hsSeries.value?.t_h || []
  const out = []
  if (h.curveBase?.depth_m) out.push({ x: t, y: h.curveBase.depth_m, color: 'rgba(255,255,255,0.35)', width: 1 })
  if (h.curve?.depth_m) out.push({ x: t, y: h.curve.depth_m, color: '#4786c9', width: 1.8 })
  return out
}
const passLine = computed(() => states.value.map(s => `${shortLabel(s.label)} ${s.hotspots_passed}/${s.hotspots_total}`).join(' · '))

/* ---------------- 展示用的小函数 ---------------- */
const f1 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 }))
const f2 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }))
const int = v => (v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'))

const KIND_COLOR = {
  ps_l1: '#9aa3ad', mobile: '#e0a44e', ps_planned: '#a07be0',
  tank: '#4786c9', gpipe: '#3fb6a8', conv: '#d9434b', sump: '#4dc3ee',
}
const LEVEL_COLOR = { 关键: '#d9434b', 高: '#e0702e', 中: '#e0a44e', 无暴露: '#7a838c' }

function depthColor(d) {
  return d >= 1 ? '#08306b' : d >= 0.5 ? '#08519c' : d >= 0.3 ? '#2171b5' : d >= 0.15 ? '#4292c6' : '#9ecae1'
}

/* ---------------- 取数 ---------------- */
async function loadState() {
  loading.value = true
  grp.sel.clearLayers()
  try {
    await rda.loadState()
    if (!isSugg.value && tab.value === 'suggested') tab.value = 'overview'
    await nextTick()
    await redrawAll()
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    overview.value = await fetchRdaOverview()
  } catch (e) {
    toast.show(tr('没有找到 RDA 成果：') + e.message, 'var(--red)')
    return
  }
  initMap()
  rda.ensureShared()
  fetchRdaHotspots().then(h => { hotspotsGeo.value = h; drawHotspots() }).catch(() => {})
  fetchRdaRain().then(r => (rainSeries.value = r)).catch(() => {})
  fetchRdaHotspotSeries().then(h => (hsSeries.value = h)).catch(() => {})
  fetchRdaGrid().then(g => { gridData.value = g; drawMaxDepth() }).catch(() => {})
  for (const k of stateKeys.value) {
    fetchRdaCriteria(k).then(c => (critAll.value = { ...critAll.value, [k]: c })).catch(() => {})
    fetchRdaFrameIndex(k).then(f => (frameIdx.value = { ...frameIdx.value, [k]: f })).catch(() => {})
  }
  await loadState()
  map.invalidateSize()
  fitAll()
})
// 换态：useRda 自己重取数据（它先注册的 watch），这里清地图选中并重画
watch(stateKey, () => { grp.sel.clearLayers(); if (tab.value === 'suggested' && !isSugg.value) tab.value = 'overview'; nextTick(redrawAll) })
watch(ponding, () => { grp.sel.clearLayers(); drawAreas(); drawPoints() })
watch(fac, drawFacilities)
watch(layersOn, () => syncLayers(), { deep: true })
watch([frameI, playMode, baseKey], () => drawMaxDepth())
// 切语言：地图上的 tooltip 是命令式画的，要重画；其余文案是响应式的
watch(locale, () => { drawPoints(); drawFacilities(); drawHotspots() })
watch(playing, on => {
  clearInterval(playTimer)
  if (!on) return
  playTimer = setInterval(() => {
    const n = fi.value?.t_h.length || 0
    if (frameI.value >= n - 1) { playing.value = false; return }
    frameI.value += 1
  }, 450)
})
onBeforeUnmount(() => clearInterval(playTimer))

/* ---------------- 地图 ---------------- */
const el = ref(null)
let map = null
const grp = {
  depth: L.layerGroup(), areas: L.layerGroup(), points: L.layerGroup(),
  fac: L.layerGroup(), hot: L.layerGroup(), sel: L.layerGroup(),
}
const facLayers = new Map()
const depthCache = shallowRef({})

function initMap() {
  map = L.map(el.value, { preferCanvas: true, zoomControl: true })
  const b = BASEMAPS.light
  L.tileLayer(b.url, { maxNativeZoom: b.maxNativeZoom, maxZoom: 19, attribution: b.attribution }).addTo(map)
  map.createPane('labels')
  map.getPane('labels').style.zIndex = 450
  map.getPane('labels').style.pointerEvents = 'none'
  L.tileLayer(b.labels, { maxNativeZoom: b.maxNativeZoom, maxZoom: 19, pane: 'labels' }).addTo(map)
  map.setView([24.46, 54.34], 14)
  for (const g of Object.values(grp)) g.addTo(map)
}
onBeforeUnmount(() => map && map.remove())

function syncLayers() {
  const on = layersOn.value
  const pairs = [['depth', on.maxDepth], ['areas', on.areas], ['points', on.points], ['fac', on.facilities], ['hot', on.hotspots]]
  for (const [k, v] of pairs) {
    if (v && !map.hasLayer(grp[k])) grp[k].addTo(map)
    if (!v && map.hasLayer(grp[k])) map.removeLayer(grp[k])
  }
  if (on.maxDepth && !grp.depth.getLayers().length) drawMaxDepth()
}

async function redrawAll() {
  grp.sel.clearLayers()
  drawMaxDepth()
  drawAreas()
  drawPoints()
  drawFacilities()
  drawHotspots()
}

function fitAll() {
  const pts = ponding.value?.points || []
  const ll = pts.map(p => [p.lat, p.lon])
  for (const m of suggested.value?.measures || []) if (isSugg.value) ll.push([m.lat, m.lon])
  if (ll.length) map.fitBounds(L.latLngBounds(ll).pad(0.15))
}

const frameCache = new Map()
let drawToken = 0

function addCells(group, lons, lats, vals, colorFn, edge, opts = {}) {
  const half = edge / 2
  const dLat = half / 111320
  const dLon = half / (111320 * Math.cos((24.46 * Math.PI) / 180))
  for (let i = 0; i < lons.length; i++) {
    L.rectangle([[lats[i] - dLat, lons[i] - dLon], [lats[i] + dLat, lons[i] + dLon]], {
      stroke: false, fillColor: colorFn(vals[i]), fillOpacity: 0.72, interactive: false, ...opts,
    }).addTo(group)
  }
}

/** 差值色：负 = 变浅（绿），正 = 变深（红） */
function diffColor(v) {
  const a = Math.abs(v)
  if (v < 0) return a >= 0.5 ? '#1a7f37' : a >= 0.2 ? '#4cae5b' : '#a6dba0'
  return a >= 0.5 ? '#b2182b' : a >= 0.2 ? '#e05a4f' : '#f4a582'
}

/** 水深层：逐帧 / 全程最大水深 / 和对比基准的差。三种都只是读成果，不在页面里算水深。 */
async function drawMaxDepth() {
  if (!map || !layersOn.value.maxDepth) { grp.depth.clearLayers(); return }
  const key = stateKey.value
  const mode = playMode.value === 'diff' && !baseKey.value ? 'frame' : playMode.value
  const token = ++drawToken
  if (mode === 'max') {
    let d = depthCache.value[key]
    if (!d) {
      d = await fetchRdaMaxDepth(key, 0.15).catch(() => null)
      if (!d) return
      depthCache.value = { ...depthCache.value, [key]: d }
    }
    if (token !== drawToken) return
    grp.depth.clearLayers()
    addCells(grp.depth, d.lon, d.lat, d.depth_m, depthColor, d.cell_edge_m)
    return
  }
  if (!gridData.value) return
  const ck = `${key}|${frameI.value}|${mode === 'diff' ? baseKey.value : ''}`
  let f = frameCache.get(ck)
  if (!f) {
    f = await fetchRdaFrame(key, frameI.value, mode === 'diff' ? baseKey.value : null).catch(() => null)
    if (!f) return
    if (frameCache.size > 160) frameCache.clear()
    frameCache.set(ck, f)
  }
  if (token !== drawToken) return
  grp.depth.clearLayers()
  const g = gridData.value
  addCells(grp.depth, f.cells.map(c => g.lon[c]), f.cells.map(c => g.lat[c]), f.v, mode === 'diff' ? diffColor : depthColor, g.cell_edge_m)
}

/** 选中一个积水点 / 措施 / 设施：右栏点行 → `locate`，地图画高亮圈并飞过去 */
function onLocate({ lat, lon, kind, id }) {
  if (lat == null || lon == null) return    // 热点行没有坐标（详情页靠 id 弹圆内积水趋势）：这里没东西可画
  grp.sel.clearLayers()
  const color = kind === 'measure' ? '#e0a44e' : '#4786c9'
  L.circleMarker([lat, lon], { radius: 20, color, weight: 3, fillOpacity: 0 }).addTo(grp.sel)
  if (kind === 'measure') {
    for (const [fid, layer] of facLayers) if (ledgerId(fid) === id && layer.bringToFront) layer.bringToFront()
    const m = rda.measureById.value[id]
    if (m?.outfall_lat && m.outfall_lon) {
      L.polyline([[m.lat, m.lon], [m.outfall_lat, m.outfall_lon]], { color, weight: 3, interactive: false }).addTo(grp.sel)
    }
  }
  map.flyTo([lat, lon], Math.max(map.getZoom(), 16), { duration: 0.6 })
}

/** 积水范围：成果自带阈值画交付的面；重算的阈值按单元号落格（范围 = 片内全部单元） */
async function drawAreas() {
  grp.areas.clearLayers()
  const key = stateKey.value
  const p = ponding.value
  if (!p) return
  if (p.custom) {
    const g = gridData.value
    if (!g) return
    for (const pt of p.points) {
      const cells = pt.cells
      addCells(grp.areas, cells.map(c => g.lon[c]), cells.map(c => g.lat[c]), cells.map(() => 0), () => '#d9434b', g.cell_edge_m,
        { fillOpacity: 0.28, interactive: true })
    }
    return
  }
  const geo = await fetchRdaPondingAreas(key, rda.pp.value.depth.toFixed(2)).catch(() => null)
  if (!geo || key !== stateKey.value) return
  L.geoJSON(geo, {
    style: { color: '#d9434b', weight: 1.2, fillOpacity: 0.05, dashArray: '3 3' },
    onEachFeature: (f, layer) => layer.on('click', () => pickPointOnMap(f.properties.id)),
  }).addTo(grp.areas)
}
watch(gridData, () => { if (ponding.value?.custom) drawAreas() })

function pointStyle(p) {
  const lv = p.l3?.level
  return { color: '#fff', weight: 1.5, fillColor: LEVEL_COLOR[lv] || '#d9434b', fillOpacity: 0.95, opacity: 1 }
}

function drawPoints() {
  grp.points.clearLayers()
  for (const p of ponding.value?.points || []) {
    const r = Math.max(5, Math.min(14, 4 + Math.sqrt((p.vol_peak_m3 || 0) / 60)))
    const m = L.circleMarker([p.lat, p.lon], { radius: r, ...pointStyle(p) }).addTo(grp.points)
    m.bindTooltip(tr('{id} · 最深 {d} m · 峰值 {v} m³', { id: p.id, d: f2(p.hmax_m), v: int(p.vol_peak_m3) }), { className: 'rm-tip' })
    m.on('click', () => pickPointOnMap(p.id))
  }
}

function pickPointOnMap(id) {
  rda.selPoint.value = id
  tab.value = 'ponding'
  const p = (ponding.value?.points || []).find(x => x.id === id)
  if (p) onLocate({ lat: p.lat, lon: p.lon, kind: 'point', id })
}

function facStyle(kind) {
  const c = KIND_COLOR[kind] || '#888'
  if (kind === 'gpipe') return { color: c, weight: 3, opacity: 0.95 }
  return { color: c, weight: 1.5, fillColor: c, fillOpacity: 0.45 }
}

function drawFacilities() {
  grp.fac.clearLayers()
  facLayers.clear()
  const g = fac.value?.geojson
  if (!g || !map) return
  L.geoJSON(g, {
    pointToLayer: (f, ll) => {
      const k = f.properties.kind
      const c = KIND_COLOR[k] || '#888'
      return L.circleMarker(ll, { radius: k === 'conv' ? 7 : 6, color: '#fff', weight: 1.5, fillColor: c, fillOpacity: 1 })
    },
    style: f => facStyle(f.properties.kind),
    onEachFeature: (f, layer) => {
      const pr = f.properties
      facLayers.set(pr['编号'], layer)
      layer.bindTooltip(`${tr(pr['类型'])} ${pr['编号']}`, { className: 'rm-tip', sticky: true })
      layer.on('click', () => panels.value?.pickFacilityById(pr['编号']))
    },
  }).addTo(grp.fac)
  // 泵 → 排口：压力输水 / 泵站 / 取水口都带「排到」坐标，画一条虚线，看水往哪排
  for (const f of g.features) {
    const p = f.properties
    if (p['排到 经度'] && p['排到 纬度'] && f.geometry.type === 'Point') {
      const [lo, la] = f.geometry.coordinates
      L.polyline([[la, lo], [p['排到 纬度'], p['排到 经度']]], {
        color: KIND_COLOR[p.kind] || '#888', weight: 2, dashArray: '6 5', opacity: 0.85, interactive: false,
      }).addTo(grp.fac)
      if (p.kind === 'conv') {
        L.circleMarker([p['排到 纬度'], p['排到 经度']], { radius: 4, color: '#fff', weight: 1, fillColor: '#222', fillOpacity: 1 })
          .bindTooltip(tr('排口'), { className: 'rm-tip' }).addTo(grp.fac)
      }
    }
  }
}

function drawHotspots() {
  grp.hot.clearLayers()
  if (!map) return
  for (const f of hotspotsGeo.value?.features || []) {
    const [lo, la] = f.geometry.coordinates
    const pr = f.properties
    const pass = crit.value?.hotspots.find(h => h.id === pr.hotspot_id)?.all_pass
    L.marker([la, lo], {
      icon: L.divIcon({
        className: '', iconSize: [0, 0],
        html: `<div class="rda-hot ${pass ? 'ok' : 'bad'}">${pr.hotspot_id.replace('ADM-', '')}</div>`,
      }),
    }).bindTooltip(tr('客户热点 {id} · {c} · {r}', { id: pr.hotspot_id, c: pr.criticality || '', r: tr(pr.root_cause_zh || '') }), { className: 'rm-tip' })
      .addTo(grp.hot)
  }
}
watch(crit, drawHotspots)
</script>

<template>
  <SimShell>
    <div class="rda">
      <header class="rda-head">
        <div>
          <h1>{{ tr(overview?.title || 'RD-A 全套成果') }}</h1>
          <p v-if="overview" class="sub">
            {{ tr(overview.model) }}{{ tr('。降雨') }} {{ overview.rain.depth_mm }} mm / {{ overview.rain.duration_h }} h{{ tr('，5 分钟峰值') }}
            {{ overview.rain.peak_mm_h }} mm/h。
          </p>
        </div>
        <div v-if="overview" class="storm rda-ui">
          <label>{{ tr('暴雨') }}
            <select disabled :title="tr('本次交付只有这一场雨')">
              <option>{{ overview.rain.depth_mm }} mm / {{ overview.rain.duration_h }} h（S61D1）</option>
            </select>
          </label>
          <span class="muted small">{{ tr('热点四项全过（{n} 个）：', { n: cur?.hotspots_total }) }}{{ passLine }}</span>
        </div>
        <div class="states">
          <button v-for="s in states" :key="s.key" :class="{ on: stateKey === s.key }" @click="stateKey = s.key">
            {{ tr(s.label) }}
            <small>{{ tr('热点达标') }} {{ s.hotspots_passed }}/{{ s.hotspots_total }}</small>
          </button>
        </div>
      </header>

      <div class="rda-body">
        <section class="map-col">
          <div ref="el" class="map" />
          <div class="map-tools rda-ui">
            <label><input v-model="layersOn.maxDepth" type="checkbox" /> {{ tr('最大水深') }}</label>
            <label><input v-model="layersOn.areas" type="checkbox" /> {{ tr('积水范围') }}</label>
            <label><input v-model="layersOn.points" type="checkbox" /> {{ tr('积水点') }}</label>
            <label><input v-model="layersOn.facilities" type="checkbox" /> {{ tr('设施') }}</label>
            <label><input v-model="layersOn.hotspots" type="checkbox" /> {{ tr('客户热点') }}</label>
            <span class="seg">
              <button :class="{ on: rda.isDelivered.value && rda.pp.value.depth === 0.15 }" @click="rda.presetDepth(0.15)">≥0.15 m</button>
              <button :class="{ on: rda.isDelivered.value && rda.pp.value.depth === 0.3 }" @click="rda.presetDepth(0.3)">≥0.30 m</button>
              <button v-if="!rda.isDelivered.value" class="on">{{ tr('自定义') }}</button>
            </span>
          </div>
          <div class="legend">
            <b>{{ tr('最大水深') }}</b>
            <i v-for="(c, n) in { '0.15': '#4292c6', '0.30': '#2171b5', '0.50': '#08519c', '1.0': '#08306b' }" :key="n">
              <u :style="{ background: c }" />≥{{ n }} m
            </i>
            <b>{{ tr('积水点（后果等级）') }}</b>
            <i v-for="(c, n) in LEVEL_COLOR" :key="n"><u class="dot" :style="{ background: c }" />{{ tr(n) }}</i>
            <template v-if="layersOn.facilities">
              <b>{{ tr('设施') }}</b>
              <i v-for="(c, k) in KIND_COLOR" :key="k"><u :style="{ background: c }" />{{ rda.kindName(k) }}</i>
            </template>
          </div>
          <div class="player rda-ui">
            <button class="play" :title="playing ? tr('暂停') : tr('播放')" @click="playing = !playing">{{ playing ? '❚❚' : '▶' }}</button>
            <input v-model.number="frameI" type="range" min="0" :max="(fi?.t_h.length || 1) - 1" />
            <span v-if="frameNow" class="readout">
              t = {{ f2(frameNow.t) }} h　{{ tr('此刻 ≥0.10 m') }} {{ f1(frameNow.wet) }} ha　{{ tr('最深') }} {{ f2(frameNow.dmax) }} m
            </span>
            <span class="seg">
              <button :class="{ on: playMode === 'frame' }" @click="playMode = 'frame'">{{ tr('逐帧') }}</button>
              <button :class="{ on: playMode === 'max' }" @click="playMode = 'max'">{{ tr('全程最大水深') }}</button>
              <button :class="{ on: playMode === 'diff' }" :disabled="!baseKey" @click="playMode = 'diff'">{{ tr('和对比基准的差') }}</button>
            </span>
          </div>
          <div v-if="loading || rda.loading.value" class="loading">{{ tr('读取中…') }}</div>
        </section>

        <aside class="side">
          <RdaPanels ref="panels" v-model:tab="tab" :rda="rda" :label="cur?.label || ''" @locate="onLocate">
            <template #overview>
              <template v-if="cur">
                <h3>{{ tr(cur.label) }}</h3>
                <p class="muted">{{ tr(cur.line) }}</p>
                <label class="small basepick">{{ tr('对比基准') }}
                  <select v-model="baseSel">
                    <option value="prev">{{ tr('上一个态') }}</option>
                    <option v-for="s in states" :key="s.key" :value="s.key" :disabled="s.key === stateKey">{{ tr(s.label) }}</option>
                  </select>
                  <span v-if="baseKey" class="muted">　{{ tr('当前对比：') }}{{ baseLabel }}</span>
                  <span v-else class="muted">　{{ tr('（这个态没有可比的基准）') }}</span>
                </label>
                <div class="kpis">
                  <div v-for="k in kpiCompare" :key="k.label">
                    <span>{{ k.label }}</span>
                    <b>{{ k.value }}<small> {{ k.unit }}</small></b>
                    <em v-if="k.delta" :class="k.delta.good ? 'ok' : 'bad'">{{ k.delta.txt }} {{ tr('对比') }} {{ shortLabel(baseLabel) }}</em>
                  </div>
                </div>
                <div class="kpis">
                  <div><b>{{ cur.hotspots_passed }}/{{ cur.hotspots_total }}</b><span>{{ tr('热点达标（A–C 全过）') }}</span></div>
                  <div><b :class="cur.pass_d ? 'ok' : 'bad'">{{ cur.pass_d ? tr('过') : tr('不过') }}</b><span>{{ tr('D：不比 planned 加深') }}</span></div>
                  <div><b>{{ cur.ponding_count['0.15'] }} / {{ cur.ponding_count['0.30'] }}</b><span>{{ tr('积水点 ≥0.15 / ≥0.30 m') }}</span></div>
                  <div><b>{{ int(cur.end_vol_m3) }}</b><span>{{ tr('模拟结束仍积水 m³') }}</span></div>
                </div>

                <h4>{{ tr('水深逐帧 · 全域积水 ≥0.10 m 面积（ha）') }}</h4>
                <MiniChart :series="areaChart" :x-max="11" :marker="frameNow?.t" :height="96" />
                <p class="muted small">
                  <u class="sw" style="background: #4786c9" />{{ tr('本态') }}　<u class="sw" style="background: rgba(255, 255, 255, 0.4)" />{{ baseLabel || tr('无基准') }}。{{ tr('拖地图下方的滑条或点 ▶ 看逐帧。面积含塘占地。') }}
                </p>
                <h4>{{ tr('降雨（雨型）') }}</h4>
                <MiniChart :series="rainChart" :x-max="11" :marker="frameNow?.t" :height="70" y-label="mm/h" />
                <p v-if="overview" class="muted small">
                  {{ overview.rain.depth_mm }} mm / {{ overview.rain.duration_h }} h · {{ tr('最大 5 分钟雨强') }} {{ overview.rain.peak_mm_h }} mm/h
                  （{{ overview.rain.peak_t_h }} h {{ tr('前后') }}）· {{ tr('净雨') }} {{ overview.rain.net_total_mm }} mm
                </p>

                <template v-if="isSugg && suggested?.by_type?.total">
                  <h4>{{ tr('造价（占位，当前方案）') }}</h4>
                  <div class="kpis three">
                    <div><b>{{ f2(suggested.by_type.total['占位造价 百万美元']) }}<small> M USD</small></b><span>{{ tr('合计造价') }}</span></div>
                    <div><b>{{ suggested.by_type.total['数量'] }}<small> {{ tr('项') }}</small></b><span>{{ tr('措施数') }}</span></div>
                    <div><b>{{ overview.new_outfalls }}<small> {{ tr('项') }}</small></b><span>{{ tr('新建排海口') }}</span></div>
                  </div>
                  <table class="tbl small-t">
                    <thead><tr><th>{{ tr('设施类型') }}</th><th>{{ tr('数量') }}</th><th>M USD</th><th>%</th></tr></thead>
                    <tbody>
                      <tr v-for="r in rda.measureTypes.value" :key="r['设施类型']">
                        <td>{{ tr(r['设施类型']) }}<div class="muted small">{{ tr(r['造价依据']) }}</div></td>
                        <td>{{ r['数量'] }}</td><td>{{ f2(r['占位造价 百万美元']) }}</td><td>{{ int(r['占比 %']) }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <p class="muted small warn">{{ tr(overview.disclaimer) }}</p>
                </template>

                <h4>{{ tr('各热点') }}</h4>
                <table class="tbl small-t">
                  <thead>
                    <tr><th>{{ tr('热点') }}</th><th>{{ tr('200 m 内 ≥0.30 m（ha）') }}</th><th>{{ tr('200 m 内最深（m）') }}</th><th>{{ tr('50 m 内 ≥0.30 m 时长（h）') }}</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="h in hotRows" :key="h.id">
                      <td><b :class="h.all_pass ? 'ok' : ''">{{ h.id }}</b><div class="muted small">{{ hotInfo[h.id]?.criticality }}</div></td>
                      <td>{{ f2(h.ge030) }} <em v-if="h.ge030D" :class="h.ge030D.good ? 'ok' : 'bad'">{{ h.ge030D.txt }}</em></td>
                      <td>{{ f2(h.max) }} <em v-if="h.maxD" :class="h.maxD.good ? 'ok' : 'bad'">{{ h.maxD.txt }}</em></td>
                      <td>{{ h.dur == null ? '—' : f2(h.dur) }} <em v-if="h.durD" :class="h.durD.good ? 'ok' : 'bad'">{{ h.durD.txt }}</em></td>
                    </tr>
                  </tbody>
                </table>
                <p class="muted small">{{ tr('彩色数是和对比基准之差（绿 = 改善）。积水面积不含塘占地。ADM-80 在汇水区南边界外，客户 PDF 也写了「无管网」。') }}</p>

                <h4>{{ tr('{n} 个热点：水深过程', { n: hotRows.length }) }}</h4>
                <div class="curves">
                  <div v-for="h in hotRows" :key="h.id" class="curve">
                    <b>{{ h.id }}</b>
                    <MiniChart :series="curveSeries(h)" :x-max="11" :y-max="0.6" :hline="0.3" :marker="frameNow?.t" :height="64" :x-ticks="3" />
                    <span class="muted small">{{ tr('最深') }} {{ f2(h.curve?.max_m) }} m · ≥0.30 m {{ f2(h.curve?.ge030_h) }} h · {{ tr('模拟末') }} {{ f2(h.curve?.end_m) }} m</span>
                  </div>
                </div>
                <p class="muted small">{{ tr('曲线 = 热点 50 m 内最深的水深，每 5 分钟一个值；蓝线是当前态，灰线是对比基准，红虚线 0.30 m。') }}</p>

                <h4>{{ tr('这个态包含的设施') }}</h4>
                <table class="tbl small-t">
                  <tbody>
                    <tr v-for="t in cur.facility_totals" :key="t.level + t.kind">
                      <td><span class="muted">{{ tr(t.level).split(' ')[0] }}</span> <u class="sw" :style="{ background: KIND_COLOR[t.kind] }" />{{ rda.kindName(t.kind) }}</td>
                      <td>{{ t.count }}</td>
                      <td>{{ t.vol_m3 ? int(t.vol_m3) + ' m³' : t.q_ls ? int(t.q_ls) + ' L/s' : '' }}</td>
                    </tr>
                  </tbody>
                </table>
                <p class="muted small">{{ tr('形状、颜色分设施类型；虚线 = 排水去向，黑点 = 排到的位置；点设施看设计参数和当前态的实际表现。') }}</p>

                <h4>{{ tr('四个状态对比') }}</h4>
                <table class="tbl">
                  <thead><tr><th>{{ tr('状态') }}</th><th>{{ tr('热点达标') }}</th><th>≥0.30 m ha</th><th>{{ tr('积水点 0.15/0.30') }}</th><th>{{ tr('设施') }}</th></tr></thead>
                  <tbody>
                    <tr v-for="s in states" :key="s.key" :class="{ cur: s.key === stateKey }" @click="stateKey = s.key">
                      <td>{{ tr(s.label) }}</td><td>{{ s.hotspots_passed }}/{{ s.hotspots_total }}</td><td>{{ f2(s.ge030_ha) }}</td>
                      <td>{{ s.ponding_count['0.15'] }} / {{ s.ponding_count['0.30'] }}</td><td>{{ s.facility_features }}</td>
                    </tr>
                  </tbody>
                </table>
                <h4>{{ tr('积水点是怎么定的') }}</h4>
                <p v-if="overview" class="muted">{{ tr(overview.ponding_rule.text) }}</p>
              </template>
            </template>
          </RdaPanels>
        </aside>
      </div>
    </div>
  </SimShell>
</template>

<style scoped>
.rda { display: flex; flex-direction: column; height: 100vh; padding: 14px 18px 12px; gap: 10px; }
.rda-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; }
.rda-head h1 { margin: 0; font-size: 20px; }
.sub { margin: 2px 0 0; color: var(--muted); font-size: 12px; max-width: 760px; }
.rda-body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 470px; gap: 12px; }
.map-col { position: relative; min-height: 0; border-radius: var(--radius); overflow: hidden; background: var(--card); }
.map { position: absolute; inset: 0; }
.map-tools { position: absolute; top: 10px; left: 56px; right: 10px; z-index: 500; display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center;
  background: rgba(28, 30, 33, 0.92); border: 1px solid var(--line-2); border-radius: 8px; padding: 6px 10px; font-size: 12px; }
.map-tools label { display: flex; gap: 4px; align-items: center; cursor: pointer; white-space: nowrap; }
.legend { position: absolute; left: 10px; bottom: 52px; z-index: 500; max-width: 70%; display: flex; flex-wrap: wrap; gap: 4px 10px;
  align-items: center; background: rgba(28, 30, 33, 0.92); border: 1px solid var(--line-2); border-radius: 8px; padding: 6px 10px; font-size: 11px; }
.legend i { font-style: normal; display: inline-flex; gap: 4px; align-items: center; color: var(--fg-2); }
.legend u, .sw { display: inline-block; width: 12px; height: 8px; border-radius: 2px; text-decoration: none; margin-right: 4px; }
.legend u.dot { width: 9px; height: 9px; border-radius: 50%; }
.loading { position: absolute; right: 12px; top: 12px; z-index: 600; background: var(--blue); padding: 3px 10px; border-radius: 6px; font-size: 12px; }
.side { min-height: 0; overflow: auto; background: var(--card); border-radius: var(--radius); }
.states { display: flex; gap: 6px; flex-wrap: wrap; }
.states button { border: 1px solid var(--line-2); border-radius: 8px; background: transparent; color: var(--muted); cursor: pointer; display: flex;
  flex-direction: column; align-items: flex-start; padding: 6px 12px; font-size: 13px; }
.states button small { color: var(--muted); font-size: 11px; }
.states button.on { background: var(--blue-bg); border-color: var(--blue); color: var(--fg); }
.storm { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.storm select { background: var(--field); color: var(--fg); border: 1px solid var(--line-2); border-radius: 6px; padding: 3px 6px; }
.player { position: absolute; left: 10px; right: 10px; bottom: 10px; z-index: 500; display: flex; align-items: center; gap: 10px;
  background: rgba(28, 30, 33, 0.94); border: 1px solid var(--line-2); border-radius: 8px; padding: 6px 10px; font-size: 12px; }
.player input[type='range'] { flex: 1; min-width: 80px; }
.player .play { background: var(--blue); border: 0; border-radius: 50%; width: 26px; height: 26px; cursor: pointer; color: #fff; }
.player .readout { color: var(--fg-2); white-space: nowrap; }
.player .seg button:disabled { opacity: 0.4; cursor: default; }
</style>

<style>
.rda-hot { transform: translate(-50%, -50%); min-width: 24px; text-align: center; font: 600 10px/16px Inter, sans-serif; color: #fff;
  border-radius: 3px; border: 1px solid #fff; padding: 0 3px; }
.rda-hot.ok { background: #2e9c4a; }
.rda-hot.bad { background: #8a2b32; }
</style>
