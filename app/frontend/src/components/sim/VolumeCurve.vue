<script setup>
/* 全域随时间的三条折线：总积水量 / 总积水面积 / 最大积水深度，RD-A 与全市任务详情共用。
   数据取 `/layers/timeline`（与地图逐帧同一份，time_minutes 从降雨起算）：
   · 总积水量 `volume_m3` = Σ(水深 × 单元面积)；
   · 总积水面积 `wet_area_m2` = 同一时刻水深 ≥ 干湿阈值（交付口径，一般 2 cm）的单元面积之和；
   · 最大积水深度 `max_depth_m` = 同一时刻全域最深的单元。
   后两项只有 `surface_series` 交付才有（`area_basis = triangle`）；哪一项这份交付拿不出（null）就只是那一张不出图，不拿别的口径顶替。 */
import { computed, ref, watch } from 'vue'

import { fetchSimulationTimeline } from '../../api'
import { t } from '../../i18n'
import { fmtInt, fmtKm2 } from '../../utils/sim'
import TimeSeriesChart from './TimeSeriesChart.vue'

const props = defineProps({
  runId: { type: [Number, String], required: true },
  state: { type: String, default: null },
  rainMin: { type: Number, default: null },   // 降雨历时（min），给了就在图上画雨停线
})

const tl = ref(null)
let ticket = 0
watch(() => [props.runId, props.state], async () => {
  tl.value = null
  const my = ++ticket
  try {
    const x = await fetchSimulationTimeline(props.runId, props.state)
    if (my === ticket) tl.value = x
  } catch {
    if (my === ticket) tl.value = null
  }
}, { immediate: true })

const series = (key, k = 1) => {
  const x = tl.value
  if (!x?.[key]) return []
  return x.time_minutes.map((m, i) => ({ m, v: x[key][i] == null ? null : x[key][i] / k })).filter(p => p.v != null)
}

const charts = computed(() => {
  const x = tl.value
  if (!x) return []
  const thr = x.wet_threshold_m == null ? '' : Number(x.wet_threshold_m).toFixed(2)
  const vol = { points: series('volume_m3'), unit: 'm³', axisY: t('sim.detail.volAxisY'), fmt: v => fmtInt(Math.round(v)) }
  const depth = { points: series('max_depth_m'), unit: 'm', axisY: t('sim.detail.depthAxisY'), fmt: v => Number(v).toFixed(2) }
  const out = []
  // 总积水量（左轴）+ 最大积水深度（右轴）一张图；哪一项拿不出就只剩另一项的单线图
  if (vol.points.length && depth.points.length) {
    out.push({ key: 'vol', title: t('sim.detail.volDepthCurve'), ...vol, second: depth, note: `${t('sim.detail.volNote')} ${t('sim.detail.depthNote')}` })
  } else if (vol.points.length) {
    out.push({ key: 'vol', title: t('sim.detail.volCurve'), ...vol, note: t('sim.detail.volNote') })
  } else if (depth.points.length) {
    out.push({ key: 'depth', title: t('sim.detail.depthCurve'), ...depth, note: t('sim.detail.depthNote') })
  }
  const area = series('wet_area_m2', 1e6)
  if (area.length) {
    out.push({ key: 'area', title: t('sim.detail.areaCurve'), points: area, unit: 'km²', axisY: t('sim.detail.areaAxisY'),
      fmt: v => fmtKm2(v), note: t('sim.detail.areaNote', { d: thr }) })
  }
  return out
})
</script>

<template>
  <TimeSeriesChart v-for="c in charts" :key="c.key" :title="c.title" :points="c.points" :unit="c.unit" :axis-y="c.axisY"
                   :fmt="c.fmt" :note="c.note" :second="c.second" :rain-min="rainMin" />
</template>
