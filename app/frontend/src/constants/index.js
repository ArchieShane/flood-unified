/* 常量。凡是给人看的名字都不在这里定死 —— 这里只留 key，
   文案统一走 i18n 的 `dataset.*` / `priority.*` / `basemap.*`，
   随语言切换即时重算（`datasetList()` 等是函数，不是模块级数组）。 */
import { t } from '../i18n'
import { UNFILLED } from '../utils/format'

/** 可选重现期。与后端 `idf.FLEET_RETURN_PERIODS` 同一套。 */
export const RPS = [5, 10, 25, 50, 100]

/** 汇水区优先级。`v` 是发给后端的值，不随语言变。 */
export const PRIORITY_KEYS = [
  { v: '', key: 'none' },
  { v: 1, key: 'p1' },
  { v: 2, key: 'p2' },
  { v: 3, key: 'p3' },
  { v: 4, key: 'p4' },
  { v: 5, key: 'p5' },
]

export const priorityList = () => PRIORITY_KEYS.map(p => ({ v: p.v, label: t(`priority.${p.key}`) }))

/** IDF 数据集按钮。`key` 是发给后端的 dataset key。 */
export const DATASET_KEYS = [
  { key: 'auto', k: 'auto' },
  { key: 'adqcc_zone_b', k: 'zoneB' },
  { key: 'adqcc_zone_a', k: 'zoneA' },
  { key: 'dubai_dm', k: 'dubai' },
]

export const datasetList = () =>
  DATASET_KEYS.map(d => ({ key: d.key, label: t(`dataset.${d.k}`), hint: t(`dataset.${d.k}Hint`) }))

export const datasetLabel = key => {
  const hit = DATASET_KEYS.find(d => d.key === key)
  return hit ? t(`dataset.${hit.k}`) : key
}

/** 颜色口径 —— 图例、地图点、防线卡三处共用同一份。
 *  `crit` 的键是台账取值与 `UNFILLED` 哨兵，都是协议值，不随语言变。 */
export const COLOR = {
  line1: '#4786C9',
  line2: '#66C56B',
  line3a: '#E0A44E',
  line3b: '#E0574E',
  line3c: '#B3352C',
  //: 没跑过二维演算、三道防线不出结论的点。**必须与三道防线的颜色明显不同** ——
  //: 落进 line3a 的暖色里会被读成「需要 1 台泵」，那是把「没算」说成了结论。
  uncalibrated: 'rgba(255,255,255,.30)',
  none: 'rgba(255,255,255,.38)',
  crit: {
    'Very High': '#E0574E',
    High: '#E0A44E',
    Medium: '#4786C9',
    Low: '#66C56B',
    [UNFILLED]: 'rgba(255,255,255,.4)',
  },
}

export const RADIUS = { 'Very High': 6.5, High: 5.5, Medium: 4.5, Low: 4 }

/* ------------------------------------------------------------------
   模型识别的易积水片区（/api/model-ponding）——与台账点是并列的两套数据。
   ------------------------------------------------------------------ */

/** 地图上的两套积水点数据源。`key` 是协议值，文案走 `source.*`。 */
export const SOURCE_KEYS = ['ledger', 'model']

/** 四类成因的颜色。key 来自后端 `mechanism_key`，不随语言变。
 *  排序与配色按**处置优先级**：深坑（致命）→ 管网顶托 → 滨海滞水 → 一般洼地。 */
export const MECHANISM_COLOR = {
  deep_sink: '#B3352C',
  network_overflow: '#E0574E',
  coastal_lowland: '#9B6BD8',
  surface_depression: '#4786C9',
  other: 'rgba(255,255,255,.45)',
}

/** 残留积水三类原因（/layers/residual 的 class，协议值）。与溢水点成因色区分开：琥珀 / 青绿 / 洋红。 */
export const RESIDUAL_COLOR = {
  no_inlet: '#E0A04E',
  below_rim: '#1FA89A',
  not_taking: '#D6336C',
}

/** 片区多边形的画法。片区是 20 m 方格的集合（未融合），描边细一点才不糊成一团。 */
export const MECHANISM_STYLE = key => ({
  color: MECHANISM_COLOR[key] || MECHANISM_COLOR.other,
  weight: 0.6,
  opacity: 0.75,
  fillColor: MECHANISM_COLOR[key] || MECHANISM_COLOR.other,
  fillOpacity: 0.34,
  interactive: false,
})

/** 片区代表点的半径按**演算积水量**分档 —— 与台账那边按上报严重度分档同一个用意：
 *  点多大代表这处有多严重。片区没有行政严重度，而积水量是它身上最硬的那个量
 *  （ANUGA 逐帧求和，不经任何归属代理），故拿它当分档依据。单位 m³。 */
export const modelRadius = m3 =>
  m3 >= 5000 ? 7 : m3 >= 1500 ? 5.5 : m3 >= 400 ? 4.5 : 4

/** 场景级可覆盖的假设参数 —— 这几个是旋钮，12″/8″/6″ 分档表是结构不在此列。
 *
 *  `pumps_per_point` 与 `pump_capacity` 是泵侧**配置**（每点配几台 × 每台抽多少），
 *  `tankers_per_point` / `tanker_capacity` / `tanker_turnaround_min` 是罐车侧
 *  （每点配几辆 × 单车载量 / 往返周转）。
 *
 *  ⚠ **两侧串联，不是二选一** —— 泵把水抽进罐车、罐车运走，这个点的外运能力
 *  取两侧较小值。所以调大泵而不加车，一滴水也多排不出去；后端回的
 *  `chain_bottleneck` 就是告诉页面该加哪一侧。
 *
 *  `pump_duty_factor` / `pump_availability`
 *  只在清空了 `pump_capacity`、退回分档表时才起作用（折减的是铭牌值）。 */
export const OVERRIDABLE = [
  'drawdown_hours',
  'pumps_per_point',
  'pump_capacity',
  'tankers_per_point',
  'tanker_capacity',
  'tanker_turnaround_min',
  'pump_duty_factor',
  'pump_availability',
]

/** 按钮框定的片区 —— 阿布扎比岛上的巴廷区。
 *  key 写死成 `ADM / AL BATEEN` 而不是片区名，是因为有重名：`AL BATEEN` 在
 *  阿布扎比岛（ADM，18 个点，管网数据齐全）和艾因（AAM，3 个点，无管网数据）
 *  下各有一个，相距约 130 km。后端 `/api/scenario` 的 district 参数是通用的，
 *  要再开别的片区，把按钮换成下拉即可，不用动后端。 */
export const QUICK_DISTRICT = 'ADM / AL BATEEN'

/** 四套免密钥瓦片底图。CARTO 的 basemaps 现在要 API key，故改用 Esri / OSM。 */
const ESRI_CANVAS =
  'Tiles &copy; <a href="https://www.esri.com/">Esri</a> — Esri, HERE, Garmin, &copy; OpenStreetMap contributors'

export const BASEMAPS = {
  dark: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    labels: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
    maxNativeZoom: 16,
    attribution: ESRI_CANVAS,
  },
  light: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    labels: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
    maxNativeZoom: 16,
    attribution: ESRI_CANVAS,
  },
  osm: {
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    labels: null,
    maxNativeZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  },
  imagery: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    labels: 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    maxNativeZoom: 19,
    attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics',
  },
}

export const BASEMAP_ORDER = ['dark', 'light', 'osm', 'imagery']

export const basemapLabel = key => t(`basemap.${key}`)

/** 三层覆盖面的画法。extent 决定算不算，另外两层是它内部的细分。 */
export const COVERAGE_STYLE = {
  extent: { color: '#4786C9', weight: 1.5, dashArray: '6 4', fillOpacity: 0.06, fillColor: '#4786C9' },
  network: { color: '#4786C9', weight: 0, fillOpacity: 0.16, fillColor: '#4786C9' },
  computable: { color: '#66C56B', weight: 0, fillOpacity: 0.2, fillColor: '#66C56B' },
}

/** 管网四级的画法。粗细与颜色按层级递减 —— 地图上一眼分得出主干与支管。
 *  `min_zoom` 不在这里：它跟着数据一起来自后端 `network_batch.CLASSES`，
 *  哪一级什么时候起画是数据口径的一部分，不该在前端另写一份。 */
export const NETWORK_STYLE = {
  TRUNK_MAIN: { color: '#5FD4E8', weight: 2.6, opacity: 0.95 },
  PRIMARY: { color: '#4FB8D8', weight: 1.9, opacity: 0.88 },
  SECONDARY: { color: '#4390C2', weight: 1.2, opacity: 0.78 },
  LATERAL: { color: '#3E6E9E', weight: 0.7, opacity: 0.65 },
}

/** 管网线的绘制顺序（先画的在下）。支管在最下，主干压在最上。 */
export const NETWORK_ORDER = ['LATERAL', 'SECONDARY', 'PRIMARY', 'TRUNK_MAIN']

export const DISTRICT_STYLE = {
  color: '#7aa7d6',
  weight: 2,
  opacity: 0.9,
  dashArray: '6 4',
  fillColor: '#4786c9',
  fillOpacity: 0.07,
  interactive: false,
}
