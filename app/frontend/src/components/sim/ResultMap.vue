<script setup>
/* 任务详情的结果地图：耦合演算交付的 250 m 方格水深，落在底图上。
   两种看法：
   - 最大水深：每格全程最大值（/layers/max-depth，几何只在这里取一次）；
   - 时间过程：每 5 min 一帧（/layers/frames/{i} 只给「格子号 → 水深」），拖动或播放。
   帧按需取、取过的留在内存里，播放时顺手预取后几帧；这一帧没水的格子隐藏，不画 0。
   叠加层「溢水点」：/layers/ponding 按 docs/analysis_ponding 算法重算的片区。片区只给格子号，
   范围直接复用最大水深图层里同号方格的几何描边；代表点（最深格）画成带名次的圆点。
   时间过程下溢水点跟着帧走：片区与名次仍是全程结果，只按后端给的成立时刻 onset_min 逐个点亮，
   已成立的再看本帧片区内有没有水（退干的虚线描边、圆点变灰）；末帧即全程结果。溢水点与水深
   不是同一态时（效果分析页）不联动，保持静态。
   时间过程下地图上方那排数换成本帧的当前值（/layers/timeline：已出现溢水点、累计溢水量、同刻积水面积、本帧最深），
   往外抛 `now` 给右侧总览（连同各溢水点本帧片区内的最深，右侧前 10 按它逐帧重排）；timeline 里拿不出的面积 / 最深（非 sww 交付）按本帧 ≥ 1 cm 的格子算，口径照实标。
   叠加层「残留积水」：/layers/residual 把演算末路面上的水按洼地分块、逐块归因（只有四态交付有，
   别的交付 422 就不挂开关和面板）；按原因三色填格，前 20 块画带名次的方块。
   效果分析页（SimulationDetail ?view=perf）：水深画方案态，溢水点取基线态（pondingState），
   片区按 pondClass 分类填色、点开弹 pondPopup 点卡，图例由 pondLegend 给；残留积水默认关。
   叠加层「已建成调蓄塘」（l2Ponds，全市任务的第二道现状视图才给）：台账塘位画成青色标记，点开弹点卡；
   只是位置与台账尺寸，水深仍是第一道的二维成果（l2Note 在图上方照实写）。实算 L2 态（全市 #561）另画
   多态任务的实算态另按态取 /layers/storage：模型里的蓄水单元（全市 #561 是台账塘）+ 接它们的管 / 泵与方案新增连接
   粗线（按管径加粗，点开看报告峰值流量），周边 300 m 管网细灰线作背景。规划态后端只给相对父态新增的
   设施与连接（原有泵站、周边既有管网不画）。
   叠加层「雨水管网」：/api/network（与指挥中心同一份落图线），默认关；打开才取干管，放大到街区尺度再取支管，
   取过的在模块级缓存里，换态 / 换任务不重取。蓝阶水深上看不清蓝线，这里改用橙棕四级。 */
import L from 'leaflet'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import { fetchNetwork, fetchSimulationPipes, fetchSimulationFacilities, fetchSimulationFrame, fetchSimulationHotspots, fetchSimulationStorage, fetchSimulationLayers, fetchSimulationMaxDepth, fetchSimulationPonding, fetchSimulationResidual, fetchSimulationTimeline } from '../../api'
import { BASEMAPS, MECHANISM_COLOR, NETWORK_ORDER, RESIDUAL_COLOR } from '../../constants'
import { locale, t } from '../../i18n'
import { esc } from '../../utils/format'
import { fmtInt, fmtKm2 as fmtKm2Num, fmtLatLon } from '../../utils/sim'
import HotspotPanel from './HotspotPanel.vue'
import PondingPanel from './PondingPanel.vue'
import ResidualPanel from './ResidualPanel.vue'

/** 管网落图线的模块级缓存：layer → Promise<FeatureCollection>（几 MB，多个结果地图共用一份） */
const NET_CACHE = new Map()
const loadNet = layer => {
  if (!NET_CACHE.has(layer)) NET_CACHE.set(layer, fetchNetwork(layer).catch(e => { NET_CACHE.delete(layer); throw e }))
  return NET_CACHE.get(layer)
}

const props = defineProps({
  runId: { type: [String, Number], required: true },
  state: { type: String, default: null },     // 多态任务的态（run.states），单一交付为 null
  pondingState: { type: String, default: null }, // 溢水点取哪一态（效果分析取基线）；不给同 state
  pondClass: { type: Function, default: null },   // 片区 → { color, key } | null：给了就按类别填色
  pondPopup: { type: Function, default: null },   // 片区 → 点卡 HTML：给了点圆点弹卡
  pondLegend: { type: Object, default: null },    // { title, rows: [{ color, label, n }], note }
  showResidual: { type: Boolean, default: true },
  fitPonds: { type: Boolean, default: false },     // 溢水点载入后飞到它们一带（效果分析）
  l2Ponds: { type: Array, default: null },         // 已建成调蓄塘（sim_l2.ponds），给了就画
  l2Note: { type: String, default: '' },           // 第二道视图的底图说明
  // RD-A 全套成果：有自己的积水点算法（SIM 编号，见 RdaPanels），不再出「溢水点」及其溢水量；
  // simPoints = 算法给的积水点 [{ id, lat, lon, vol, color }]，画在这里，点圆点往外抛 simpoint
  noOverflow: { type: Boolean, default: false },
  // 全市一二维任务（#1068）：模拟积水点已替代「溢水点」，只藏溢水点（图层 / 列表 / 个数），溢水量照出
  noPondPoints: { type: Boolean, default: false },
  noLedger: { type: Boolean, default: false },      // RD-A / 全市：不出「台账全部积水点」开关和图层
  simPoints: { type: Array, default: null },
  simSelected: { type: String, default: null },
})
/** 取回的图层元数据与积水片区往外抛一份，详情页右侧总览（RunOverview）直接用，不再重复请求 */
const emit = defineEmits(['meta', 'ponding', 'active', 'l2active', 'now', 'hotspots', 'simpoint', 'hotspotpick'])

/** 分档着色：**水越深颜色越深**（单色蓝阶，浅蓝 → 藏青）。
 *  原先是暗底图上「越深越亮」，用户会把深色格误读成积水点；深色要看得出，底图因此换成浅灰。 */
const BINS = [
  { min: 0.01, color: '#c6dbef' },
  { min: 0.02, color: '#9ecae1' },
  { min: 0.05, color: '#6baed6' },
  { min: 0.1, color: '#3182bd' },
  { min: 0.2, color: '#08519c' },
  { min: 0.3, color: '#08306b' },
]
const colorFor = d => {
  let c = BINS[0].color
  for (const b of BINS) if (d >= b.min) c = b.color
  return c
}
const HIDDEN = { stroke: false, fillOpacity: 0 }
const styleFor = d => (d == null ? HIDDEN : { stroke: false, fillColor: colorFor(d), fillOpacity: 0.78 })

const el = ref(null)
const meta = shallowRef(null)
const error = ref('')
const mode = ref('max')
const frameIdx = ref(0)
const frame = shallowRef(null)
const playing = ref(false)
const speed = ref(3)          // 每一跳前进几帧（1 帧 = 5 min）
const SPEEDS = [1, 3, 12]

let map = null
let cellLayer = null
let pondLayer = null
const pondMarkers = new Map() // 名次 → 代表点 marker
let pondItems = []            // [{ c, color, geo, marker, st, now }]，逐帧联动用
let timer = null
const cells = new Map()       // cell_id → { layer, max }
const frames = new Map()      // 帧号 → 帧数据
const pending = new Map()     // 帧号 → 在途请求

const ponding = shallowRef(null)
const pondErr = ref('')
const pondOn = ref(true)
const pondActive = ref(null)
/** 溢水点随帧联动：只在溢水点与水深同一态时（效果分析页取基线态，保持静态） */
const pondLive = computed(() => !props.pondingState || props.pondingState === props.state)
const pondLit = ref(null)     // 时间过程下本帧已成立的溢水点数
const pondNow = shallowRef(null)  // 时间过程下本帧各溢水点的状态与片区内最深：[{ rank, st, depth_m }]；不联动时 null

const residual = shallowRef(null)
const resOn = ref(props.showResidual)
const resActive = ref(null)
let resLayer = null
const resMarkers = new Map()  // 名次 → 代表点 marker
const RES_MARKED = 20


const frameList = computed(() => meta.value?.frames || [])
const rainMin = computed(() => meta.value?.model_configuration?.rainfall_duration_minutes ?? null)
const curMin = computed(() => frameList.value[frameIdx.value]?.time_minutes ?? 0)
const cellKm2 = computed(() => ((meta.value?.domain?.cell_size_m || 250) ** 2) / 1e6)

const fmtClock = min => {
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return `T+${h}:${String(m).padStart(2, '0')}`
}
const fmtKm2 = m2 => (m2 == null ? '—' : fmtKm2Num(m2 / 1e6))
const fmtM = x => (x == null ? '—' : Number(x).toFixed(2))

// 最大水深模式只展示最大水深（积水面积、模拟结束仍积水、管网溢出进地表等指标不再展示）
const stats = computed(() => {
  const r = meta.value?.results || {}
  return [['maxDepth', `${fmtM(r.maximum_depth_m)} m`]]
})

/** 本帧当前值（只在时间过程下）：timeline 有的取 timeline（sww 交付是三角形 ≥ 2 cm，与「最大积水面积」同口径），
 *  没有的按本帧 ≥ 1 cm 的格子算。溢水点数取 timeline 的 onset 计数，与地图逐帧点亮同一判据。 */
const timeline = shallowRef(null)
const now = computed(() => {
  const f = frame.value
  if (mode.value !== 'time' || !f) return null
  const tl = timeline.value
  const i = frameIdx.value
  const tri = tl?.area_basis === 'triangle'
  const n = f.cells.length
  return {
    index: i,
    time_minutes: f.time_minutes,
    area_basis: tri ? 'triangle' : 'cell',
    area_m2: tri ? tl.wet_area_m2[i] : n * cellKm2.value * 1e6,
    depth_m: tri ? tl.max_depth_m[i] : n ? Math.max(...f.depth_m) : 0,
    volume_m3: tl?.volume_m3?.[i] ?? null,
    overflow_m3: tl?.overflow_cum_m3?.[i] ?? null,
    overflow_basis: tl?.overflow_basis ?? null,
    ponding: tl?.ponding_lit?.[i] ?? pondLit.value,
    ponding_total: tl?.ponding_total ?? ponding.value?.summary.cluster_count ?? null,
    ponds: pondNow.value,
  }
})
watch(now, v => emit('now', v), { immediate: true })

const nowStats = computed(() => {
  const x = now.value
  if (!x) return null
  return [
    ['now', fmtClock(x.time_minutes)],
    ...(props.noOverflow ? [] : [
      ...(props.noPondPoints ? [] : [['pondNow', x.ponding == null ? '—' : `${x.ponding}${x.ponding_total != null ? ` / ${x.ponding_total}` : ''}`]]),
      ['overflowNow', x.overflow_m3 == null ? '—' : `${fmtInt(Math.round(x.overflow_m3))} m³`],
    ]),
    [x.area_basis === 'triangle' ? 'areaNowTri' : 'areaNowCell', `${fmtKm2(x.area_m2)} km²`],
    ['depthNow', `${fmtM(x.depth_m)} m`],
  ]
})

/* ---------------- 地图 ---------------- */

function initMap() {
  map = L.map(el.value, { preferCanvas: true, zoomControl: true, attributionControl: true })
  const b = BASEMAPS.light   // 蓝阶越深越暗，暗底图上最深两档会糊进底色
  L.tileLayer(b.url, { maxNativeZoom: b.maxNativeZoom, maxZoom: 19, attribution: b.attribution }).addTo(map)
  // 注记压在水深格子上面，路名地名不被盖住
  map.createPane('labels')
  map.getPane('labels').style.zIndex = 450
  map.getPane('labels').style.pointerEvents = 'none'
  L.tileLayer(b.labels, { maxNativeZoom: b.maxNativeZoom, maxZoom: 19, pane: 'labels' }).addTo(map)
}

function drawCells(geo) {
  cellLayer = L.geoJSON(geo, {
    style: f => styleFor(f.properties.maximum_depth_m),
    onEachFeature: (f, layer) => {
      cells.set(f.properties.cell_id, { layer, max: f.properties.maximum_depth_m, geom: f.geometry })
      layer.bindTooltip(() => tooltipFor(f.properties.cell_id), { sticky: true, className: 'rm-tip' })
    },
  }).addTo(map)
}

function tooltipFor(id) {
  const c = cells.get(id)
  let now = ''
  if (mode.value === 'time' && frame.value) {
    const i = frame.value.cells.indexOf(id)
    now = `<br>${t('sim.map.depthAt', { t: fmtClock(frame.value.time_minutes) })}：` +
      (i >= 0 ? `${fmtM(frame.value.depth_m[i])} m` : t('sim.map.dry'))
  }
  return `${t('sim.map.cell', { id })}<br>${t('sim.map.maxDepth')}：${fmtM(c?.max)} m${now}`
}

function paintMax() {
  for (const { layer, max } of cells.values()) layer.setStyle(styleFor(max))
  paintPonds(null)
}

function paintFrame(f) {
  const d = new Map()
  f.cells.forEach((id, i) => d.set(id, f.depth_m[i]))
  for (const [id, { layer }] of cells) layer.setStyle(styleFor(d.get(id)))
  paintPonds(f, d)
}

/* ---------------- 溢水点 ---------------- */

const mechColor = k => MECHANISM_COLOR[k] || MECHANISM_COLOR.other

/** 溢水点悬浮卡：名称、位置、时刻、水深、面积。位置是代表格（最深格）格心坐标，不随帧变。时间过程下取本帧时刻、本帧片区内最深处、本帧片区内有水的格子面积，
 *  最大水深模式下取全程；未成立的点本帧不画，走不到这里。卡片开着时逐帧刷新（paintPonds）。 */
function pondTip(it) {
  const c = it.c
  const live = it.st && frame.value
  const row = (k, v) => `<span class="k">${k}</span><span class="v">${v}</span>`
  const depth = live ? it.now : c.max_depth_m
  const area = live ? it.wet * cellKm2.value * 1e6 : c.area_m2
  const loc = fmtLatLon(c)
  return `<div class="pf-pop"><h4>${esc(c.name || `#${c.rank}`)}</h4><div class="kv">` +
    (loc ? row(t('sim.map.pondLoc'), loc) : '') +
    row(t('sim.map.pondTime'), live ? fmtClock(frame.value.time_minutes) : t('sim.map.pondWhole')) +
    row(t('sim.map.pondDepth'), depth == null ? t('sim.map.dry') : `${fmtM(depth)} m`) +
    row(t('sim.map.pondArea'), area ? `${fmtInt(Math.round(area))} m²` : t('sim.map.dry')) +
    '</div></div>'
}

const pondIcon = (rank, color, dry) =>
  L.divIcon({ className: `rm-pond${dry ? ' dry' : ''}`, html: `<span style="background:${color}">${rank}</span>`, iconSize: [22, 22] })

function drawPonding(p) {
  pondLayer = L.layerGroup()
  pondItems = []
  for (const c of p.clusters) {
    const cls = props.pondClass ? props.pondClass(c) : null
    const color = props.pondClass ? cls?.color || '#8B8D98' : mechColor(c.mechanism_key)
    const feats = c.cells.map(id => cells.get(id)?.geom).filter(Boolean)
      .map(geometry => ({ type: 'Feature', geometry, properties: {} }))
    const geo = L.geoJSON({ type: 'FeatureCollection', features: feats }, {
      style: props.pondClass
        ? { color, weight: 1.6, opacity: 0.95, fillColor: color, fillOpacity: 0.35 }
        : { color, weight: 1.6, opacity: 0.95, fill: false },
      interactive: false,
    }).addTo(pondLayer)
    const it = { c, color, geo, marker: null, st: null, now: null, wet: 0 }
    pondItems.push(it)
    if (c.lat == null) continue
    const m = L.marker([c.lat, c.lon], { icon: pondIcon(c.rank, color, false), zIndexOffset: 1000 - c.rank })
    if (props.pondPopup) m.bindPopup(props.pondPopup(c), { className: 'rm-pop', maxWidth: 340, offset: [0, -6] })
    else m.bindTooltip(() => pondTip(it), { className: 'rm-tip', direction: 'top', offset: [0, -10] })
    m.on('click', () => focusPond(c.rank, false))
    m.addTo(pondLayer)
    it.marker = m
    pondMarkers.set(c.rank, m)
  }
  if (pondOn.value) pondLayer.addTo(map)
  if (mode.value === 'time' && frame.value) paintFrame(frame.value)
}

/** 溢水点逐帧状态：pending 未成立（隐藏）/ wet 已成立且本帧片区内有水 / dry 已成立但本帧退干。
 *  f 为 null（最大水深、或不联动）时全部按全程结果画。只在状态变化时改图层，播放不卡。 */
function paintPonds(f, d) {
  const live = f && pondLive.value
  let lit = 0
  for (const it of pondItems) {
    let st = null
    it.now = null
    it.wet = 0
    if (live) {
      if (f.time_minutes < it.c.onset_min) st = 'pending'
      else {
        for (const id of it.c.cells) {
          const v = d.get(id)
          if (v == null) continue
          it.wet++
          if (it.now == null || v > it.now) it.now = v
        }
        st = it.now == null ? 'dry' : 'wet'
        lit++
      }
    }
    // 开着的悬浮卡跟着帧刷新（内容是函数，update 会重算）
    if (it.marker?.isTooltipOpen()) it.marker.getTooltip().update()
    if (st === it.st) continue
    const show = st !== 'pending'
    for (const l of [it.geo, it.marker]) {
      if (!l) continue
      if (show && !pondLayer.hasLayer(l)) pondLayer.addLayer(l)
      if (!show && pondLayer.hasLayer(l)) pondLayer.removeLayer(l)
    }
    if (show) {
      it.geo.setStyle({ dashArray: st === 'dry' ? '4 4' : null, opacity: st === 'dry' ? 0.6 : 0.95 })
      it.marker?.setIcon(pondIcon(it.c.rank, it.color, st === 'dry'))
    }
    it.st = st
  }
  pondLit.value = live && pondItems.length && pondItems[0].c.onset_min != null ? lit : null
  pondNow.value = live ? pondItems.map(it => ({ rank: it.c.rank, st: it.st, depth_m: it.now })) : null
}

function focusPond(rank, fly = true) {
  pondActive.value = rank
  emit('active', rank)
  const m = pondMarkers.get(rank)
  if (!m) return
  if (!pondOn.value) pondOn.value = true
  if (fly) map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 14), { duration: 0.6 })
  if (!pondLayer.hasLayer(m)) return   // 本帧还没成立，只飞过去
  props.pondPopup ? m.openPopup() : m.openTooltip()
}

/** 溢水点单独取：首次要现算（全市 250 m 约 6 s），不挡水深图层。成因文案随语言，切语言重取。 */
async function loadPonding() {
  pondErr.value = ''
  if (props.noOverflow || props.noPondPoints) { emit('ponding', null, ''); return }
  try {
    const p = await fetchSimulationPonding(props.runId, props.pondingState || props.state)
    ponding.value = p
    redrawPonding()
    // 效果分析只看这几处溢水点：飞到它们一带，不停在整个演算域
    if (props.fitPonds) fitPonds()
    emit('ponding', p, '')
  } catch (e) {
    pondErr.value = e.message
    emit('ponding', null, e.message)
  }
}
function fitPonds() {
  const ll = [...pondMarkers.values()].map(m => m.getLatLng())
  if (ll.length) map.fitBounds(L.latLngBounds(ll), { padding: [60, 60], maxZoom: 16 })
}
function redrawPonding() {
  pondLayer?.remove()
  pondMarkers.clear()
  pondItems = []
  if (ponding.value) drawPonding(ponding.value)
}
watch(locale, () => cells.size && loadPonding())
// 效果分析换时刻档：分类与点卡跟着换，片区不重取
watch(() => [props.pondClass, props.pondPopup], () => cells.size && redrawPonding())

watch(pondOn, on => {
  if (!pondLayer) return
  on ? pondLayer.addTo(map) : pondLayer.remove()
})

/* ---------------- 残留积水 ---------------- */

function resTip(p) {
  const f2 = x => Number(x).toFixed(2)
  const clk = min => fmtClock(min)
  const s = residual.value.summary
  let h = `<b>${t('sim.residual.tip', { rank: p.rank, cls: t(`sim.residual.cls.${p.class}`) })}</b><br>` +
    (p.poi ? `${esc(p.poi.name)} ${p.poi.distance_m} m<br>` : '') +
    t('sim.residual.tipVol', { vol: fmtInt(Math.round(p.volume_m3)), t0: clk(s.trend_from_minutes ?? 0),
      v0: p.volume_before_m3 == null ? '—' : fmtInt(Math.round(p.volume_before_m3)) }) + '<br>' +
    t('sim.residual.tipLevel', { floor: f2(p.floor_m), surf: f2(p.surface_m), depth: f2(p.max_depth_m) }) + '<br>'
  for (const i of p.inlets) h += t('sim.residual.inletTip', { id: i.id, rim: f2(i.rim_m), ret: fmtInt(Math.round(i.returned_m3)),
    ovf: i.overflowed ? t('sim.residual.overflowed') : '' }) + '<br>'
  if (!p.inlets.length && p.nearest_inlet) h += t('sim.residual.tipNear', { id: p.nearest_inlet.id, d: p.nearest_inlet.distance_m, rim: f2(p.nearest_inlet.rim_m) }) + '<br>'
  if (p.nearest_storage) h += t('sim.residual.tipSto', { id: p.nearest_storage.id, d: p.nearest_storage.distance_m,
    pct: p.nearest_storage.max_fill_pct == null ? '—' : Math.round(p.nearest_storage.max_fill_pct) }) + '<br>'
  return h + `${p.lat}, ${p.lon}`
}

function drawResidual(r) {
  resLayer = L.layerGroup()
  for (const p of r.ponds) {
    const color = RESIDUAL_COLOR[p.class]
    const feats = p.cells.map(id => cells.get(id)?.geom).filter(Boolean)
      .map(geometry => ({ type: 'Feature', geometry, properties: {} }))
    L.geoJSON({ type: 'FeatureCollection', features: feats }, {
      style: { color, weight: 1, opacity: 0.9, fillColor: color, fillOpacity: 0.45 },
    }).bindTooltip(() => resTip(p), { sticky: true, className: 'rm-tip' })
      .on('click', () => focusRes(p.rank, false))
      .addTo(resLayer)
    if (p.rank > RES_MARKED || p.lat == null) continue
    const m = L.marker([p.lat, p.lon], {
      icon: L.divIcon({ className: 'rm-res', html: `<span style="background:${color}">${p.rank}</span>`, iconSize: [20, 20] }),
      zIndexOffset: 500 - p.rank,
    })
    m.bindTooltip(() => resTip(p), { className: 'rm-tip', direction: 'top', offset: [0, -10] })
    m.on('click', () => focusRes(p.rank, false))
    m.addTo(resLayer)
    resMarkers.set(p.rank, m)
  }
  if (resOn.value) resLayer.addTo(map)
}

function focusRes(rank, fly = true) {
  resActive.value = rank
  const p = residual.value?.ponds.find(x => x.rank === rank)
  if (!p) return
  if (!resOn.value) resOn.value = true
  if (fly && p.lat != null) map.flyTo([p.lat, p.lon], Math.max(map.getZoom(), 16), { duration: 0.6 })
  resMarkers.get(rank)?.openTooltip()
}

/** 只有四态交付拆得出；取不到（422 / 404）就当没有这一层，不报错。 */
async function loadResidual() {
  try {
    const r = await fetchSimulationResidual(props.runId, props.state)
    resLayer?.remove()
    resMarkers.clear()
    residual.value = r
    drawResidual(r)
  } catch {
    residual.value = null
  }
}

watch(resOn, on => {
  if (!resLayer) return
  on ? resLayer.addTo(map) : resLayer.remove()
})

/* ---------------- 雨水管网 ---------------- */

/** 结果地图是浅底 + 蓝阶水深，指挥中心那套青蓝管线会糊进水里，改橙棕；粗细层级照旧 */
const NET_STYLE = {
  TRUNK_MAIN: { color: '#9a3f12', weight: 2.6, opacity: 0.95 },
  PRIMARY: { color: '#c05a1c', weight: 1.9, opacity: 0.9 },
  SECONDARY: { color: '#dd7a2e', weight: 1.2, opacity: 0.85 },
  LATERAL: { color: '#e9a15a', weight: 0.8, opacity: 0.8 },
}
const net = shallowRef({ mains: null, laterals: null })
const netOn = ref(false)
const netLoading = ref(false)
const netErr = ref('')
let netLayer = null

/** 这次推演自己的管线（pipes.geojson，engine 写的）：有就画它、可点看属性；没有再退回全市管网落图线 */
const ownPipes = shallowRef(null)
let ownTried = null
async function loadOwnPipes() {
  if (ownTried === props.runId) return
  ownTried = props.runId
  try { ownPipes.value = await fetchSimulationPipes(props.runId) } catch { ownPipes.value = null }
}
function drawOwnPipes() {
  const stage = f => f.properties['设施阶段']
  netLayer = L.geoJSON(ownPipes.value, {
    style: f => (stage(f) === 'Planned' ? { color: '#f59e0b', weight: 3, dashArray: '6 4' } : { color: '#5b8def', weight: 2.5, opacity: 0.9 }),
    onEachFeature: (ft, l) => l.bindPopup(`<div class="pf-pop"><h4>管线 ${esc(ft.properties['编号'])}</h4><div class="kv">${
      ['管径 mm', '管长 m', '管材', '状态', '设施阶段'].map(k => kvRow(esc(k), esc(String(ft.properties[k] ?? '—')))).join('')}</div></div>`, { className: 'rm-pop', minWidth: 200, maxWidth: 300 }),
  }).addTo(map)
}

function drawNetwork() {
  netLayer?.remove()
  netLayer = null
  if (!map || !netOn.value) return
  if (ownPipes.value) { drawOwnPipes(); return }
  const { mains, laterals } = net.value
  if (!mains) return
  const z = map.getZoom()
  if (!laterals && z >= (mains.properties?.lateral_min_zoom ?? 14)) loadNetLayer('laterals')
  const feats = [mains, laterals].filter(Boolean).flatMap(fc => fc.features || [])
  netLayer = L.layerGroup()
  for (const cls of NETWORK_ORDER) {
    const f = feats.find(x => x.properties.key === cls)
    if (!f || z < f.properties.min_zoom) continue
    L.geoJSON(f, { style: { ...NET_STYLE[cls], interactive: false } }).addTo(netLayer)
  }
  netLayer.addTo(map)
}

async function loadNetLayer(layer) {
  if (net.value[layer]) return
  netLoading.value = true
  netErr.value = ''
  try {
    const fc = await loadNet(layer)
    if (!fc.available) throw new Error(t('sim.network.missing'))
    net.value = { ...net.value, [layer]: fc }
  } catch (e) {
    netErr.value = e.message
    if (layer === 'mains') netOn.value = false
  } finally {
    netLoading.value = false
  }
}

watch(netOn, async on => {
  if (on) await loadOwnPipes()
  if (on && !ownPipes.value) await loadNetLayer('mains')
  drawNetwork()
})
watch(net, drawNetwork)

/* ---------------- 储水设施：已建成调蓄塘（第二道现状）/ 各态模型蓄水单元 ---------------- */

const L2_COLOR = '#19c3c0'      // 新增（相对父态）/ 台账塘
const OLD_COLOR = '#8fa3b8'     // 父态模型里就有的蓄水单元与连接
let l2Layer = null
const l2Markers = new Map()   // 塘编号 → marker
const L2_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round">' +
  '<path d="M3 15c2 0 2-1.5 4.5-1.5S9.5 15 12 15s2.5-1.5 4.5-1.5S19 15 21 15"/><path d="M3 19c2 0 2-1.5 4.5-1.5S9.5 19 12 19s2.5-1.5 4.5-1.5S19 19 21 19"/>' +
  '<path d="M7 11V5m0 0 2.5 2.5M7 5 4.5 7.5"/></svg>'

/** 多态任务按态取 /layers/storage（实算态才有，404 就不挂）；单态全市任务仍由详情页给 l2Ponds */
const sto = shallowRef(null)
const stoOn = ref(true)
const stoPonds = computed(() => props.l2Ponds || sto.value?.ponds || null)
const stoPipes = computed(() => (props.l2Ponds ? null : sto.value?.pipes) || null)
const stoInp = computed(() => sto.value?.basis === 'inp_storage')
const stoCounts = computed(() => {
  const ps = stoPonds.value || []
  const links = stoPipes.value?.inlets || []
  return { ponds: ps.length, newPonds: ps.filter(p => p.is_new).length, links: links.length,
    newLinks: links.filter(x => x.is_new).length }
})
async function loadStorage() {
  sto.value = null
  if (!props.state) return
  try {
    sto.value = await fetchSimulationStorage(props.runId, props.state)
  } catch {
    sto.value = null
  }
}

/** 交付的设施 GIS（/layers/facilities）：规划塘 / 泵点 / 排水去向 / 地面取水片；新增用亮色，上一态继承的用灰蓝 */
const fac = shallowRef(null)
const facOn = ref(true)
let facLayer = null
const FAC_NEW = '#f59e0b', FAC_PUMP = '#19c3c0'
async function loadFacilities() {
  fac.value = null
  if (!props.state) return
  try {
    fac.value = await fetchSimulationFacilities(props.runId, props.state)
  } catch {
    fac.value = null
  }
}
const facCounts = computed(() => {
  const L = fac.value?.layers || {}
  const n = k => (L[k]?.features || []).filter(f => f.properties.is_new).length
  return { ponds: L.ponds?.features.length || 0, pumps: n('pumps'), intake: n('intake') }
})
function facPopup(pr) {
  const skip = new Set(['编号', '类型', 'is_new', '所属态', '本态', '说明'])
  const rows = Object.entries(pr).filter(([k, v]) => !skip.has(k) && v != null && v !== '')
    .map(([k, v]) => kvRow(esc(k), esc(String(v)))).join('')
  return `<div class="pf-pop"><h4>${esc(pr['类型'])} ${esc(pr['编号'])}</h4><div class="kv">${kvRow('所属态', esc(pr['所属态']))}${rows}</div></div>`
}
function drawFac() {
  facLayer?.remove()
  facLayer = null
  const L_ = fac.value?.layers
  if (!map || !L_ || !facOn.value) return
  facLayer = L.layerGroup()
  const col = pr => (pr.is_new ? FAC_NEW : OLD_COLOR)
  const add = (fc, opt) => L.geoJSON(fc, {
    ...opt,
    onEachFeature: (ft, l) => l.bindPopup(facPopup(ft.properties), { className: 'rm-pop', minWidth: 220, maxWidth: 320 }),
  }).addTo(facLayer)
  if (L_.ponds) {   // 塘画成面；有储水设施图标（带蓄水量弹窗）时，点面就开那个图标的弹窗，不重复弹
    const g = L.geoJSON(L_.ponds, {
      style: f => ({ color: col(f.properties), weight: 2, fillColor: col(f.properties), fillOpacity: 0.25 }),
      onEachFeature: (ft, l) => {
        const mk = l2Markers.get(ft.properties['编号'])
        if (mk) l.on('click', () => mk.openPopup())
        else l.bindPopup(facPopup(ft.properties), { className: 'rm-pop', minWidth: 220, maxWidth: 320 })
      },
    }).addTo(facLayer)
    g.bringToBack?.()
  }
  if (L_.intake) add(L_.intake, { style: () => ({ color: FAC_NEW, weight: 1.5, dashArray: '4 3', fillColor: FAC_NEW, fillOpacity: 0.35 }) })
  if (L_.outlets) add(L_.outlets, { style: f => ({ color: col(f.properties), weight: 2.5, dashArray: '2 6', lineCap: 'round', opacity: 0.9 }) })
  if (L_.pumps) add(L_.pumps, {
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, weight: 2, color: '#fff', fillColor: f.properties.is_new ? FAC_PUMP : OLD_COLOR, fillOpacity: 1 }),
  })
  facLayer.addTo(map)
}
watch([fac, facOn, stoPonds], drawFac)
watch(facOn, v => { if (fac.value) stoOn.value = v })   // 有设施 GIS 时，一个开关同管塘与设备

const clkMin = m => (m == null ? '—' : `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`)
const n0 = x => (x == null ? '—' : fmtInt(Math.round(x)))
const kvRow = (k, v) => `<span class="k">${k}</span><span class="v">${v}</span>`

function l2Popup(p) {
  const n = n0, row = kvRow
  if (stoInp.value)   // 模型蓄水单元（Mussafah 各规划态）：.inp 容积 + 报告峰值
    return `<div class="pf-pop"><h4>${esc(t('sim.l2.stoTip', { id: p.name }))}</h4><div class="kv">` +
      row(t('sim.l2.pondModelVol'), `${n(p.model_volume_m3)} m³`) +
      row(t('sim.l2.pondDepth'), p.depth_m == null ? '—' : `${Number(p.depth_m).toFixed(2)} m`) +
      row(t('sim.l2.stoPeak'), p.max_stored_m3 == null ? '—' : `${n(p.max_stored_m3)} m³ @ ${clkMin(p.max_stored_min)}`) +
      row(t('sim.l2.pondFill'), p.max_fill_pct == null ? '—' : `${Number(p.max_fill_pct).toFixed(1)}%`) +
      '</div>' + `<div style="margin-top:6px;font-size:12px">${esc(pondInletText(p))}</div>` +
      `<div class="kv">${row('', `${p.lat}, ${p.lon}`)}</div></div>`
  return `<div class="pf-pop"><h4>${esc(t('sim.l2.pondTip', { id: p.id }))}</h4><div class="kv">` +
    row(t('sim.l2.pondDistrict'), esc(p.district)) +
    row(t('sim.l2.pondStatus'), esc(p.status) + (p.lining ? ` · ${esc(p.lining)}` : '')) +
    row(t('sim.l2.pondVol'), `${n(p.volume_m3)} m³<br><small style="opacity:.6">${esc(t(`sim.l2.tier.${p.volume_tier}`))}</small>`) +
    row(t('sim.l2.pondArea'), `${n(p.footprint_m2)} m²`) +
    row(t('sim.l2.pondDepth'), p.depth_m == null ? '—' : `${Number(p.depth_m).toFixed(2)} m`) +
    // 实算 L2 态（全市 #561）：交付塘蓄水表的演算成果
    (p.model_volume_m3 == null ? '' : '</div>' +
      (stoPipes.value ? `<div style="margin-top:6px;font-size:12px">${esc(pondInletText(p))}</div>` : '') +
      `<div class="kv" style="margin-top:6px"><span class="k" style="grid-column:1/-1;opacity:.7">${esc(t('sim.l2.pondModel', { name: p.name }))}</span>` +
      row(t('sim.l2.pondModelVol'), `${n(p.model_volume_m3)} m³`) +
      row(t('sim.l2.pondStored'), `${n(p.stored_end_m3)} m³`) +
      row(t('sim.l2.pondFill'), p.fill_pct == null ? '—' : `${Number(p.fill_pct).toFixed(1)}%`) +
      row(t('sim.l2.pondSurface'), `${n(p.surface_inflow_m3)} m³`) +
      row(t('sim.l2.pondSpill'), `${n(p.spill_m3)} m³`)) +
    row('', `${p.lat}, ${p.lon}`) + '</div></div>'
}

function pondInletText(p) {
  const ins = stoPipes.value?.inlets.filter(x => x.pond === p.name) || []
  if (!ins.length) return t('sim.l2.pipeNone')
  const q = Math.max(...ins.map(x => x.max_flow_m3s ?? 0))
  return t('sim.l2.pipeCount', { n: ins.length, q: q.toFixed(2) })
}

function pipePopup(x) {
  const row = kvRow
  return `<div class="pf-pop"><h4>${esc(t(`sim.l2.linkTip.${x.kind || 'conduit'}`))}</h4><div class="kv">` +
    row(t('sim.l2.pipeId'), `<small>${esc(x.id)}</small>`) +
    row(t('sim.l2.pipeFrom'), `<small>${esc(x.from)} → ${esc(x.to)}</small>`) +
    (x.kind && x.kind !== 'conduit' ? '' :
      row(t('sim.l2.pipeDiam'), x.diameter_m == null ? '—' : `${Number(x.diameter_m).toFixed(2)} m`) +
      row(t('sim.l2.pipeLen'), x.length_m == null ? '—' : `${Number(x.length_m).toFixed(1)} m`)) +
    row(t('sim.l2.pipeQ'), x.max_flow_m3s == null ? '—' : `${x.max_flow_m3s.toFixed(3)} m³/s @ ${clkMin(x.max_flow_min)}`) +
    (x.max_full_flow == null ? '' : row(t('sim.l2.pipeFull'), x.max_full_flow.toFixed(2))) + '</div></div>'
}

function drawL2() {
  l2Layer?.remove()
  l2Markers.clear()
  l2Layer = null
  const list = stoPonds.value
  if (!map || !list?.length || !stoOn.value) return
  l2Layer = L.layerGroup()
  const pp = stoPipes.value
  const inp = stoInp.value
  if (pp) {
    for (const c of pp.context)
      L.polyline(c.latlngs, { color: '#7a8594', weight: 1.2, opacity: 0.7, interactive: false }).addTo(l2Layer)
    for (const x of pp.inlets) {
      const w = x.kind === 'conduit' || !x.kind ? 3 + 2 * Math.min(3, x.diameter_m || 0.6) : 4
      const idle = !(x.max_flow_m3s > 0)
      const color = inp && !x.is_new ? OLD_COLOR : L2_COLOR
      L.polyline(x.latlngs, { color: '#fff', weight: w + 3, opacity: 0.9 }).addTo(l2Layer)
      // 泵画点划线；全程无流量的画淡
      L.polyline(x.latlngs, { color, weight: w, opacity: idle ? 0.45 : 1, dashArray: x.kind === 'pump' ? '2 7' : null, lineCap: 'round' })
        .bindPopup(pipePopup(x), { className: 'rm-pop', minWidth: 240, maxWidth: 320 }).addTo(l2Layer)
    }
  }
  for (const p of list) {
    const bg = inp && !p.is_new ? OLD_COLOR : L2_COLOR
    const m = L.marker([p.lat, p.lon], {
      icon: L.divIcon({ className: 'rm-l2', html: `<span style="background:${bg}">${L2_ICON}</span>`, iconSize: [28, 28] }),
      zIndexOffset: 2000,
    })
    m.bindPopup(l2Popup(p), { className: 'rm-pop', minWidth: 220, maxWidth: 300, offset: [0, -8] })
    m.on('click', () => emit('l2active', p.id))
    m.addTo(l2Layer)
    l2Markers.set(p.id, m)
  }
  l2Layer.addTo(map)
}
watch([stoPonds, stoPipes, stoOn, locale], () => { drawL2(); drawFac() })   // 塘面点击要用图标的 marker，所以图标先画

function focusL2Pond(id) {
  const m = l2Markers.get(id)
  if (!m) return
  map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 15), { duration: 0.6 })
  m.openPopup()
}

/* ---------------- 交付自带的积水点（热点） ---------------- */

const HOT_COLOR = '#e8590c'
const hot = shallowRef(null)
const hotOn = ref(true)
const hotCatchOn = ref(false)
const hotActive = ref(null)
let hotLayer = null
const hotMarkers = new Map()   // 积水点号 → marker
/** 当前态的统计；没有这一态（态清单之外）取第一态，数字不串态 */
const hotState = computed(() => (hot.value?.states || []).includes(props.state) ? props.state : hot.value?.states?.[0])

/** 热点很多（全市 470 个）时不画带编号的药丸，改画按「交付统计的积水等级」着色的小圆点，编号放悬浮提示里 */
const HOT_MANY = 100
const HOT_CLASS_COLOR = { flooded: '#d9434b', shallow: '#e0a44e', no: '#868e96' }

/** 关注积水点的等级（交付自带的 criticality）：药丸按等级着色；没有该字段的任务保持原来的橙色 */
const CRIT_COLOR = { 'Very High': '#c92a2a', 'Very Important': '#c92a2a', High: '#e8590c', Important: '#e8590c', Medium: '#d9a400' }
const critColor = c => CRIT_COLOR[c] || null
const critLegend = computed(() => {
  const seen = new Set((hot.value?.points || []).map(p => p.criticality).filter(c => c && CRIT_COLOR[c]))
  return Object.keys(CRIT_COLOR).filter(k => seen.has(k)).map(k => ({ key: k, color: CRIT_COLOR[k] }))
})

function hotPopup(p) {
  const st = p.stats?.[hotState.value] || {}
  const n = (x, d = 2) => (x == null ? '—' : Number(x).toFixed(d))
  const c = st.catchment || {}
  const nr = st.near_200m || {}
  if (p.outside) {
    // 台账里有、但不在二维计算域内：没有仿真结果，只给位置
    return `<b>${esc(p.id)}</b> · ${esc(p.district || '')}${p.criticality ? ` · ${esc(p.criticality)}` : ''}<br>` +
      `<span style="opacity:.75">${esc(p.location || '')}</span><br>` +
      `<span style="opacity:.75">${t('sim.hotspots.outside')}</span><br>${fmtLatLon(p)}`
  }
  if (!st.catchment) {
    // 没有汇水区统计（全市交付）：面积 / 积水量取离热点 200 m 内单元的统计
    return `<b>${esc(p.id)}</b> · ${esc(p.district || '')}${p.criticality ? ` · ${esc(p.criticality)}` : ''}<br>` +
      `<span style="opacity:.75">${esc(p.location || '')}</span><br>` +
      `${t('sim.hotspots.near')}：${n(nr.max_depth_m)} m<br>` +
      `${t('sim.hotspots.a30Near')}：${n(nr.area_ge_0_30_ha)} ha<br>` +
      `${t('sim.hotspots.volNear')}：${nr.max_volume_m3 == null ? '—' : fmtInt(nr.max_volume_m3)} m³` +
      `${nr.t_max_depth_h == null ? '' : `（${t('sim.hotspots.deepestAt')} T+${n(nr.t_max_depth_h, 2)} h）`}<br>` +
      `${fmtLatLon(p)}`
  }
  return `<b>${esc(p.id)}</b> · ${esc(p.district || '')}${p.criticality ? ` · ${esc(p.criticality)}` : ''}<br>` +
    `<span style="opacity:.75">${esc(p.location || '')}</span><br>` +
    `${t('sim.hotspots.near')}：${n(st.near_200m?.max_depth_m)} m<br>` +
    `${t('sim.hotspots.a30')}：${n(c.area_ge_0_30_ha)} ha<br>` +
    `${t('sim.hotspots.vol')}：${c.max_volume_m3 == null ? '—' : fmtInt(c.max_volume_m3)} m³` +
    `（T+${n(c.max_volume_h, 2)} h）<br>` +
    `${fmtLatLon(p)}`
}

function drawHot() {
  hotLayer?.remove()
  hotMarkers.clear()
  hotLayer = null
  if (!map || !hot.value || !hotOn.value) return
  hotLayer = L.layerGroup()
  if (hotCatchOn.value) {
    // 片区汇水区（本交付自己的）
    const rc = hot.value.region_catchment
    if (rc?.features?.length)
      L.geoJSON(rc, { style: { color: '#7048e8', weight: 2, opacity: 0.9, fillOpacity: 0.04 }, interactive: false }).addTo(hotLayer)
    // 逐热点汇水区；没收敛的（伸出计算窗口）画出来是错的，不画
    L.geoJSON(hot.value.catchments, {
      filter: f => !f.properties.unconverged,
      style: f => (f.properties.kind === 'surface'
        ? { color: HOT_COLOR, weight: 1.2, opacity: 0.8, dashArray: '4 4', fillOpacity: 0.03 }
        : { color: '#1c7ed6', weight: 1.2, opacity: 0.8, fillOpacity: 0.05 }),
      interactive: false,
    }).addTo(hotLayer)
  }
  const many = hot.value.points.length > HOT_MANY
  for (const p of hot.value.points) {
    const cls = p.stats?.[hotState.value]?.near_200m?.class
    const m = many
      ? L.circleMarker([p.lat, p.lon], { radius: 5, weight: 1.2, color: '#fff', fillColor: HOT_CLASS_COLOR[cls] || '#868e96', fillOpacity: 0.95 })
        .bindTooltip(esc(p.id.replace(/^[A-Z]{3}-/, '')), { direction: 'top', offset: [0, -4] })
      : L.marker([p.lat, p.lon], {
        icon: L.divIcon({ className: 'rm-hot', html: `<span${critColor(p.criticality) ? ` style="background:${critColor(p.criticality)}"` : ''}>${esc(p.id.replace(/^[A-Z]{3}-/, ''))}</span>`, iconSize: [34, 22] }),
        zIndexOffset: 3000,
      })
    m.bindPopup(() => hotPopup(p), { className: 'rm-pop', minWidth: 220, maxWidth: 300, offset: [0, -6] })
    m.on('click', () => { hotActive.value = p.id; if (!p.outside) emit('hotspotpick', p.id) })   // 域外点没有圆内曲线； 弹窗照开，另抛出去让详情页弹圆内积水趋势
    m.addTo(hotLayer)
    hotMarkers.set(p.id, m)
  }
  hotLayer.addTo(map)
}
watch([hot, hotOn, hotCatchOn, () => props.state, locale], drawHot)

/* 台账登记的全部积水点：交付统计的那几个（上面的「关注积水点」）以外的点画成小灰点，关注点不重复画 */
const ledgerOn = ref(false)
/** RD-A、全市任务不出「台账全部积水点」：关注积水点（交付的热点）已经是全部，两层会冲突（`noLedger`） */
const ledgerPts = computed(() => (props.noLedger ? [] : hot.value?.ledger_points || []))
let ledgerLayer = null

function ledgerPopup(p) {
  return `<b>${esc(p.id)}</b> · ${esc(p.district || '')}<br>` +
    `<span style="opacity:.75">${esc(p.location || '')}</span><br>${fmtLatLon(p)}`
}

function drawLedger() {
  ledgerLayer?.remove()
  ledgerLayer = null
  if (!map || !ledgerOn.value || !ledgerPts.value.length) return
  const watched = new Set((hot.value?.points || []).map(p => p.id))
  ledgerLayer = L.layerGroup()
  for (const p of ledgerPts.value) {
    if (watched.has(p.id)) continue
    L.circleMarker([p.lat, p.lon], { radius: 6, weight: 1.5, color: '#fff', fillColor: '#868e96', fillOpacity: 0.95 })
      .bindPopup(() => ledgerPopup(p), { className: 'rm-pop', minWidth: 200, maxWidth: 280 })
      .bindTooltip(esc(p.id.replace(/^[A-Z]{3}-/, '')),{ direction: 'top', offset: [0, -4] })
      .addTo(ledgerLayer)
  }
  ledgerLayer.addTo(map)
}
watch([hot, ledgerOn, locale], drawLedger)

function focusHotspot(id) {
  const m = hotMarkers.get(id)
  if (!m) return
  hotActive.value = id
  if (!hotOn.value) hotOn.value = true
  map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 15), { duration: 0.6 })
  hotMarkers.get(id)?.openPopup()
}

async function loadHotspots() {
  try {
    hot.value = await fetchSimulationHotspots(props.runId)
  } catch {
    hot.value = null      // 大多数任务没有这一层（404），当没有
  }
  emit('hotspots', hot.value)
}

/* ---------------- 帧 ---------------- */

function loadFrame(i) {
  if (frames.has(i)) return Promise.resolve(frames.get(i))
  if (pending.has(i)) return pending.get(i)
  const p = fetchSimulationFrame(props.runId, i, props.state)
    .then(f => {
      frames.set(i, f)
      return f
    })
    .finally(() => pending.delete(i))
  pending.set(i, p)
  return p
}

async function showFrame(i) {
  try {
    const f = await loadFrame(i)
    // 快速拖动时旧请求晚到，丢掉
    if (i !== frameIdx.value || mode.value !== 'time') return
    frame.value = f
    paintFrame(f)
  } catch (e) {
    error.value = e.message
    stop()
  }
  if (playing.value)
    for (let k = 1; k <= 4; k++) {
      const j = i + k * speed.value
      if (j < frameList.value.length) loadFrame(j).catch(() => {})
    }
}

function tick() {
  const last = frameList.value.length - 1
  if (frameIdx.value >= last) return stop()
  const next = Math.min(last, frameIdx.value + speed.value)
  // 下一帧没到就等它，不跳帧
  if (!frames.has(next)) return loadFrame(next).catch(() => {})
  frameIdx.value = next
}

function play() {
  if (mode.value !== 'time') mode.value = 'time'
  if (frameIdx.value >= frameList.value.length - 1) frameIdx.value = 0
  playing.value = true
  timer = setInterval(tick, 300)
}
function stop() {
  playing.value = false
  clearInterval(timer)
  timer = null
}

watch(frameIdx, i => mode.value === 'time' && showFrame(i))
watch(mode, m => {
  if (m === 'max') {
    stop()
    paintMax()
  } else showFrame(frameIdx.value)
})

onMounted(async () => {
  initMap()
  drawSim()
  map.on('zoomend', () => netOn.value && !ownPipes.value && drawNetwork())
  drawL2()
  try {
    const [m, geo] = await Promise.all([fetchSimulationLayers(props.runId, props.state), fetchSimulationMaxDepth(props.runId, props.state)])
    meta.value = m
    emit('meta', m)
    drawCells(geo)
    if (m.bounds) map.fitBounds(m.bounds, { padding: [16, 16] })
    // 时间过程默认停在雨停那一帧
    const stopRain = m.frames.findIndex(f => f.time_minutes >= (m.model_configuration?.rainfall_duration_minutes ?? 0))
    frameIdx.value = Math.max(0, stopRain)
  } catch (e) {
    error.value = e.message
    return
  }
  loadPonding()
  loadResidual()
  loadHotspots()
  loadStorage()
  loadFacilities()
  // 逐帧当前值（小，一次取完）；取不到就退回按本帧格子算
  fetchSimulationTimeline(props.runId, props.state).then(x => (timeline.value = x)).catch(() => {})
})
/* ---------------- 积水点算法的点（RD-A） ---------------- */
const simOn = ref(true)
let simLayer = null
let prevSimSel = null
const simMarkers = new Map()   // 积水点号 → circleMarker，选中变化时只重画被点的那两个
/** 点很多（全市几千个）时圆点画小、描边细；few 个点（RD-A）照旧 */
const simStyle = (p, sel, many) => ({
  radius: sel ? 11 : many ? Math.max(3, Math.min(10, 2.5 + Math.sqrt((p.vol || 0) / 150))) : Math.max(5, Math.min(12, 4 + Math.sqrt((p.vol || 0) / 60))),
  color: sel ? '#4786c9' : '#fff', weight: sel ? 3 : many ? 0.8 : 1.5, fillColor: p.color || '#d9434b', fillOpacity: many ? 0.85 : 0.95,
})
function drawSim() {
  simLayer?.remove()
  simLayer = null
  simMarkers.clear()
  if (!map || !props.simPoints?.length || !simOn.value) return
  simLayer = L.layerGroup()
  const many = props.simPoints.length > 300
  // 先画没有 near 标记的，再画 near（在热点 200 m 内）的，红点盖在橙点上面
  const pts = many ? [...props.simPoints].sort((a, b) => Number(!!a.near) - Number(!!b.near)) : props.simPoints
  for (const p of pts) {
    const m = L.circleMarker([p.lat, p.lon], simStyle(p, p.id === props.simSelected, many)).addTo(simLayer)
    m.bindTooltip(p.label || p.id, { className: 'rm-tip' })
    m.on('click', () => emit('simpoint', p.id))
    simMarkers.set(p.id, m)
  }
  simLayer.addTo(map)
  prevSimSel = props.simSelected
}
watch(() => [props.simPoints, simOn.value], drawSim)
watch(() => props.simSelected, sel => {
  // 只改前后两个点的样式；整张重画几千个点会卡
  const many = (props.simPoints?.length || 0) > 300
  for (const id of [prevSimSel, sel]) {
    const m = id && simMarkers.get(id)
    const p = m && props.simPoints.find(x => x.id === id)
    if (m && p) { m.setStyle(simStyle(p, id === sel, many)); m.setRadius(simStyle(p, id === sel, many).radius); if (id === sel) m.bringToFront() }
  }
  prevSimSel = sel
})
/** 飞到某个坐标（方案效果卡片点名时用：那边的点号是基线态的，和当前态的溢水点号对不上） */
function flyToPoint(lat, lon) {
  if (map && lat != null) map.flyTo([lat, lon], Math.max(map.getZoom(), 16), { duration: 0.6 })
}
defineExpose({ focusPond, flyToPoint, focusL2Pond, focusHotspot })
onBeforeUnmount(() => {
  simLayer?.remove()
  facLayer?.remove()
  stop()
  map?.remove()
})
</script>

<template>
  <section class="sim-card rm">
    <div class="rm-head">
      <div class="bh">{{ t('sim.map.title') }}</div>
      <div class="rm-modes">
        <button :class="{ on: mode === 'max' }" @click="mode = 'max'">{{ t('sim.map.modeMax') }}</button>
        <button :class="{ on: mode === 'time' }" @click="mode = 'time'">{{ t('sim.map.modeTime') }}</button>
      </div>
      <label v-if="!noOverflow && !noPondPoints" class="rm-pond-toggle" :class="{ off: !ponding }" :title="t('sim.ponding.toggleTip')">
        <input v-model="pondOn" type="checkbox" :disabled="!ponding" />
        {{ t('sim.ponding.toggle') }}<template v-if="ponding">（{{ ponding.summary.cluster_count }}）</template>
      </label>
      <label v-if="hot" class="rm-pond-toggle">
        <input v-model="hotOn" type="checkbox" />
        {{ t('sim.hotspots.toggle') }}（{{ hot.points.length }}）
      </label>
      <label v-if="ledgerPts.length" class="rm-pond-toggle" :title="t('sim.hotspots.ledgerTip')">
        <input v-model="ledgerOn" type="checkbox" />
        {{ t('sim.hotspots.ledgerToggle') }}（{{ ledgerPts.length }}）
      </label>
      <label v-if="hot && hotOn && (hot.catchments?.features?.length || hot.region_catchment?.features?.length)" class="rm-pond-toggle" :title="t('sim.hotspots.catchTip')">
        <input v-model="hotCatchOn" type="checkbox" />
        {{ t('sim.hotspots.catchToggle') }}
      </label>
      <label v-if="sto && !fac" class="rm-pond-toggle">
        <input v-model="stoOn" type="checkbox" />
        {{ t('sim.l2.stoToggle') }}（{{ stoCounts.ponds }}）
      </label>
      <label v-if="simPoints?.length" class="rm-pond-toggle">
        <input v-model="simOn" type="checkbox" />
        {{ t('sim.rda.simToggle') }}（{{ simPoints.length }}）
      </label>
      <label v-if="fac" class="rm-pond-toggle" :title="t('sim.fac.tip')">
        <input v-model="facOn" type="checkbox" />
        {{ t('sim.fac.toggle') }}（{{ t('sim.fac.count', { p: facCounts.pumps, i: facCounts.intake, s: facCounts.ponds }) }}）
      </label>
      <label v-if="residual" class="rm-pond-toggle">
        <input v-model="resOn" type="checkbox" />
        {{ t('sim.residual.toggle') }}（{{ residual.summary.pond_count }}）
      </label>
      <label class="rm-pond-toggle" :class="{ off: netLoading }" :title="netErr || t('sim.network.tip')">
        <input v-model="netOn" type="checkbox" :disabled="netLoading && !netOn" />
        {{ netLoading ? t('sim.network.loading') : t('sim.network.toggle') }}
      </label>
    </div>

    <div v-if="meta" class="rm-stats">
      <div v-for="[k, v] in nowStats || stats" :key="k">
        <div class="sim-cap">{{ t(`sim.map.stats.${k}`) }}</div>
        <div class="v">{{ v }}</div>
      </div>
    </div>

    <!-- 时间条放在地图上方且常驻：放在下面会被 560 px 的地图挤出首屏，滚轮在地图上又是缩放，找不到播放键 -->
    <div v-if="meta" class="rm-time" :class="{ idle: mode !== 'time' }">
      <button class="sim-btn primary" @click="playing ? stop() : play()">{{ playing ? t('sim.map.pause') : t('sim.map.play') }}</button>
      <input v-model.number="frameIdx" type="range" min="0" :max="frameList.length - 1" step="1" @input="stop(); mode = 'time'" />
      <span class="clock">{{ fmtClock(curMin) }}</span>
      <span class="sim-tag" :class="rainMin != null && curMin <= rainMin ? 'rain' : 'dry'">
        {{ rainMin != null && curMin <= rainMin ? t('sim.map.raining') : t('sim.map.afterRain') }}
      </span>
      <select v-model.number="speed" class="rm-speed">
        <option v-for="s in SPEEDS" :key="s" :value="s">{{ t('sim.map.speed', { m: s * 5 }) }}</option>
      </select>
    </div>
    <p v-if="l2Note" class="rm-l2note">{{ l2Note }}</p>
    <div class="rm-map-wrap">
      <div ref="el" class="rm-map" />
      <div v-if="stoPonds?.length && stoOn" class="rm-legend rm-legend-l2">
        <template v-if="stoInp">
          <div class="cap">{{ t('sim.l2.stoLegend') }}</div>
          <div class="row"><span class="dot" />{{ t('sim.l2.stoNew') }}<b>{{ stoCounts.ponds }}</b></div>
        </template>
        <div v-else class="row"><span class="dot" />{{ t('sim.l2.legend') }}<b>{{ stoCounts.ponds }}</b></div>
        <template v-if="stoPipes">
          <div class="row"><span class="ln" />{{ t(stoInp ? 'sim.l2.legendLink' : 'sim.l2.legendInlet') }}<b>{{ stoCounts.links }}</b></div>
          <div v-if="stoInp" class="row"><span class="ln pump" />{{ t('sim.l2.legendPump') }}</div>
          <div class="row"><span class="ln idle" />{{ t('sim.l2.legendIdle') }}</div>
          <div v-if="stoPipes.context.length" class="row"><span class="ln ctx" />{{ t('sim.l2.legendCtx', { r: stoPipes.context_radius_m }) }}</div>
        </template>
      </div>
      <div v-if="fac && facOn" class="rm-legend rm-legend-fac">
        <div class="cap">{{ t('sim.fac.legend') }}</div>
        <div class="row"><span class="sw" :style="{ background: FAC_PUMP }" />{{ t('sim.fac.pump') }}<b>{{ facCounts.pumps }}</b></div>
        <div class="row"><span class="sw" :style="{ background: FAC_NEW }" />{{ t('sim.fac.pond') }}<b>{{ facCounts.ponds }}</b></div>
        <div v-if="facCounts.intake" class="row"><span class="sw" :style="{ background: FAC_NEW, opacity: 0.5 }" />{{ t('sim.fac.intake') }}<b>{{ facCounts.intake }}</b></div>
      </div>
      <div v-if="residual && resOn" class="rm-legend rm-legend-res">
        <div class="cap">{{ t('sim.residual.toggle') }}</div>
        <div v-for="(c, k) in RESIDUAL_COLOR" :key="k" class="row">
          <span class="sw" :style="{ background: c }" />{{ t(`sim.residual.cls.${k}`) }}
        </div>
      </div>
      <div v-if="pondLegend && pondOn" class="rm-legend rm-legend-perf">
        <div class="cap">{{ pondLegend.title }}</div>
        <div v-for="r in pondLegend.rows" :key="r.label" class="row">
          <span class="sw" :style="{ background: r.color }" />{{ r.label }}<b v-if="r.n != null">{{ r.n }}</b>
        </div>
        <div v-if="pondLegend.note" class="note">{{ pondLegend.note }}</div>
      </div>
      <div v-if="hotOn && critLegend.length" class="rm-legend rm-legend-res">
        <div class="cap">{{ t('sim.hotspots.critLegend') }}</div>
        <div v-for="c in critLegend" :key="c.key" class="row"><span class="sw" :style="{ background: c.color }" />{{ c.key }}</div>
      </div>
      <div class="rm-legend">
        <div class="cap">{{ mode === 'max' ? t('sim.map.maxDepth') : t('sim.map.depth') }}（m）</div>
        <div v-for="(b, i) in BINS" :key="b.min" class="row">
          <span class="sw" :style="{ background: b.color }" />
          {{ b.min.toFixed(2) }}{{ BINS[i + 1] ? ` – ${BINS[i + 1].min.toFixed(2)}` : '+' }}
        </div>
      </div>
      <div v-if="mode === 'time' && meta" class="rm-clock">
        <div class="t">{{ fmtClock(curMin) }}</div>
        <div class="s">{{ rainMin != null && curMin <= rainMin ? t('sim.map.raining') : t('sim.map.afterRain') }}
          <template v-if="now"> ｜ {{ fmtKm2(now.area_m2) }} km²</template></div>
      </div>
      <div v-if="!meta && !error" class="rm-loading">{{ t('sim.map.loading') }}</div>
    </div>


    <HotspotPanel v-if="hot && !noPondPoints" :data="hot" :state="hotState" :active="hotActive" @focus="focusHotspot" />
    <ResidualPanel v-if="meta && residual && (showResidual || resOn)" :data="residual" :active="resActive" @focus="focusRes" />
    <PondingPanel v-if="meta && !noOverflow && !noPondPoints":data="ponding" :error="pondErr" :active="pondActive" @focus="focusPond" />

    <p v-if="error" class="rm-err">{{ error }}</p>
    <p v-if="netErr" class="rm-err">{{ t('sim.network.failed') }}{{ netErr }}</p>
    <p v-if="meta" class="rm-note">
      {{ meta.solver }} ｜ {{ t('sim.map.grid', { m: meta.domain?.cell_size_m, n: fmtInt(meta.cell_count) }) }} ｜
      {{ t(meta.coupling?.coupling_mode === 'bidirectional' ? 'sim.map.claimTwoWay' : 'sim.map.claim') }}
    </p>
    <p v-if="meta?.partial" class="rm-err">{{ t('sim.map.partial', {
      done: meta.delivery.completed_window_count, exp: meta.delivery.expected_window_count,
      tail: `${Math.round(meta.delivery.missing_tail_seconds / 60)} min`,
    }) }}</p>
  </section>
</template>

<style scoped>
.rm { padding: 16px 18px 12px; margin-top: 20px; }
.rm-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.rm-head .bh { font-weight: 600; font-size: 15px; margin-right: auto; }
.rm-head { gap: 14px; }
.rm-pond-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--fg); cursor: pointer; }
.rm-pond-toggle.off { opacity: 0.5; cursor: default; }
.rm-modes { display: inline-flex; background: var(--field); border: 1px solid var(--line-2); border-radius: 6px; padding: 2px; }
.rm-modes button { border: 0; background: none; padding: 5px 12px; border-radius: 4px; cursor: pointer; color: var(--fg-2); }
.rm-modes button.on { background: var(--blue); color: #fff; }
.rm-stats { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin-bottom: 12px; }
.rm-stats .v { font-size: 17px; font-weight: 600; margin-top: 2px; }
.rm-map-wrap { position: relative; }
.rm-map { height: 560px; border-radius: 6px; overflow: hidden; background: #e8e8e8; }
.rm-legend { position: absolute; right: 10px; bottom: 24px; z-index: 500; background: rgba(23, 24, 27, 0.88);
  border: 1px solid var(--line-2); border-radius: 6px; padding: 8px 10px; font-size: 12px; color: var(--fg-2); }
.rm-legend-res { right: auto; left: 10px; }
.rm-legend-fac .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
.rm-legend-fac .row b { margin-left: 10px; color: var(--fg); font-weight: 600; }
/* 放左上（缩放键右边）：左下是残留积水图例 */
.rm-legend-l2 { right: auto; bottom: auto; left: 56px; top: 10px; }
.rm-legend-l2 .dot.old { background: #8fa3b8; }
.rm-legend-l2 .ln.pump { border-top: 4px dotted #19c3c0; }
.rm-legend-l2 .dot { width: 12px; height: 12px; border-radius: 50%; background: #19c3c0; border: 1.5px solid #fff; }
.rm-legend-l2 .ln { width: 18px; height: 0; border-top: 4px solid #19c3c0; }
.rm-legend-l2 .ln.idle { opacity: .45; }
.rm-legend-l2 .ln.ctx { border-top: 1.5px solid #7a8594; }
.rm-legend-l2 .row b { margin-left: 10px; color: var(--fg); font-weight: 600; }
.rm-l2note { margin: 0 0 10px; padding: 8px 12px; border-radius: 6px; background: var(--amber-bg); color: var(--amber); font-size: 12.5px; }
.rm-legend-perf { bottom: auto; top: 10px; max-width: 230px; }
.rm-legend-perf .row b { margin-left: auto; padding-left: 10px; color: var(--fg); font-weight: 600; font-variant-numeric: tabular-nums; }
.rm-legend-perf .note { margin-top: 6px; color: var(--muted); font-size: 11px; line-height: 1.4; }
.rm-legend .cap { color: var(--fg); margin-bottom: 4px; }
.rm-legend .row { display: flex; align-items: center; gap: 6px; line-height: 18px; }
.rm-legend .sw { width: 14px; height: 10px; border-radius: 2px; }
.rm-loading { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--muted); z-index: 500; pointer-events: none; }
.rm-time { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.rm-time.idle .clock, .rm-time.idle .sim-tag { opacity: 0.5; }
.rm-clock { position: absolute; left: 56px; top: 10px; z-index: 500; background: rgba(23, 24, 27, 0.88);
  border: 1px solid var(--line-2); border-radius: 6px; padding: 6px 12px; pointer-events: none; }
.rm-clock .t { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.rm-clock .s { font-size: 12px; color: var(--fg-2); }
.rm-time input[type='range'] { flex: 1; min-width: 200px; accent-color: var(--blue); }
.rm-time .clock { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 64px; }
.rm-time .sim-tag.rain { background: var(--blue-bg); color: var(--blue-lt); }
.rm-time .sim-tag.dry { background: var(--amber-bg); color: var(--amber); }
.rm-speed { background: var(--field); color: var(--fg); border: 1px solid var(--line-2); border-radius: 6px; height: 34px; padding: 0 8px; }
.rm-err { color: var(--red); font-size: 13px; margin: 8px 0 0; }
.rm-note { color: var(--muted); font-size: 12px; margin: 10px 0 0; }
@media (max-width: 900px) {
  .rm-stats { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .rm-map { height: 420px; }
}
</style>

<style>
.leaflet-tooltip.rm-tip { background: #1c1e21; color: #fff; border: 1px solid rgba(255, 255, 255, 0.12); font-size: 12px; }
.leaflet-tooltip.rm-tip::before { display: none; }
.rm-hot span { display: flex; align-items: center; justify-content: center; height: 22px; padding: 0 6px; border-radius: 11px;
  background: #e8590c; color: #fff; font: 700 12px/1 system-ui, sans-serif; border: 1.5px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 1px 5px rgba(0, 0, 0, 0.55); }
.rm-res span { display: flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 3px;
  color: #fff; font: 600 11px/1 system-ui, sans-serif; border: 1.5px solid rgba(255, 255, 255, 0.9);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.5); }
.leaflet-popup.rm-pop .leaflet-popup-content-wrapper { background: #1c1e21; color: #fff; border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45); }
.leaflet-popup.rm-pop .leaflet-popup-tip { background: #1c1e21; }
.leaflet-popup.rm-pop .leaflet-popup-content { margin: 14px 16px; }
.leaflet-popup.rm-pop a.leaflet-popup-close-button { color: rgba(255, 255, 255, 0.6); }
.pf-pop h4 { margin: 0 18px 10px 0; font-size: 14px; font-weight: 600; letter-spacing: 0.02em; line-height: 1.4; }
.pf-pop .kv { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 7px 10px; font-size: 12.5px; line-height: 1.5; }
.pf-pop .k { color: rgba(255, 255, 255, 0.5); }
.pf-pop .v i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; vertical-align: 0; }
.rm-l2 span { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.95); box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45); }
.rm-pond.dry span { background: #8B8D98 !important; opacity: 0.75; }
.rm-pond span { display: flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%;
  color: #fff; font: 600 11px/1 system-ui, sans-serif; border: 1.5px solid rgba(255, 255, 255, 0.9);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.5); }
</style>
