<script setup>
/* 任务详情的雨型图：按任务记下的雨量 / 历时 / 峰值位置取同一条派生雨型（`/api/hyetograph`），
   与指挥中心「雨型图」页签同源同算法。只画设计暴雨；实测回放没有这条派生雨型，不出图。
   步长不传，由后端按历时取默认（3 h 以内 5 min，更长 15 min）；历史任务没记峰值位置的取后端默认 40%。 */
import { computed, ref, watch } from 'vue'

import { fetchHyetograph, fetchHyetographPresets } from '../../api'
import { locale, t } from '../../i18n'
import { stormLabel } from '../../utils/sim'
import HyetographChart from '../HyetographChart.vue'

const props = defineProps({
  run: { type: Object, required: true },
  isCustom: { type: Boolean, default: false },
})

const h = ref(null)
const base = ref(null) // 只取证据边界 / 方法 / 来源三句文案
const err = ref('')

const params = computed(() => {
  const r = props.run
  if (!r.depth_mm || !r.duration_h) return null
  return {
    P: Number(r.depth_mm),
    D: Math.round(Number(r.duration_h) * 60),
    peakPct: r.peak_position_pct == null ? undefined : Number(r.peak_position_pct),
  }
})

let ticket = 0
watch(
  params,
  async p => {
    h.value = null
    err.value = ''
    if (!p) return
    const my = ++ticket
    try {
      const d = await fetchHyetograph(p)
      if (my === ticket) h.value = d
    } catch (e) {
      if (my === ticket) err.value = e.message
    }
  },
  { immediate: true },
)

// 证据边界是后端文案，换语言得重取
watch(
  locale,
  async () => {
    try {
      base.value = await fetchHyetographPresets()
    } catch {
      base.value = null
    }
  },
  { immediate: true },
)

const title = computed(() => (props.isCustom ? t('sim.rain.customStorm') : stormLabel(props.run)))
</script>

<template>
  <section v-if="params" class="sim-card rh">
    <div class="rh-head">{{ t('sim.detail.hyeto') }}</div>
    <p v-if="err" class="sim-empty" style="padding: 0">{{ t('hyeto.loadError', { msg: err }) }}</p>
    <p v-else-if="!h" class="sim-empty" style="padding: 0">{{ t('hyeto.loading') }}</p>
    <HyetographChart v-else :h="h" :title="title" />
    <template v-if="base">
      <div class="note warn" style="margin-top: 10px">{{ base.boundary }}</div>
      <div class="note" style="margin-top: 4px">{{ base.method }} · {{ base.source }}</div>
    </template>
  </section>
</template>

<style>
/* HyetographChart 的类名在子组件里，这里不用 scoped；一律挂在 .rh 下，不串到别处 */
.rh { padding: 16px 18px 14px; margin-bottom: 20px; }
.rh-head { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
.rh .note { color: var(--muted); font-size: 12px; line-height: 16px; }
.rh .note.warn { color: var(--amber); }
.rh .stat { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
.rh .stat > div { background: var(--card-2); border-radius: 6px; padding: 8px 10px; }
.rh .stat .v { font-size: 18px; font-weight: 600; }
.rh .stat .v.warn { color: var(--amber); }
.rh .stat .l { color: var(--muted); font-size: 12px; margin-top: 2px; }
.rh .hy-cap { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; margin-top: 14px; }
.rh .hy-hover { margin-left: auto; color: var(--fg-2); font-size: 12px; font-variant-numeric: tabular-nums; }
.rh .hy-svg { width: 100%; height: auto; display: block; margin-top: 6px; }
.rh .hy-svg text { font-size: 12px; }
.rh .hy-gridline { stroke: var(--line); }
.rh .hy-axis { stroke: var(--faint); }
.rh .hy-tl { fill: var(--blue-lt); }
.rh .hy-tr { fill: var(--red); }
.rh .hy-tx { fill: var(--muted); }
.rh .hy-bar { fill: var(--blue); }
.rh .hy-bar.hot { fill: var(--blue-lt); }
.rh .hy-line { fill: none; stroke: var(--red); stroke-width: 2; stroke-linejoin: round; }
.rh .hy-dot { fill: var(--red); }
.rh .hy-peak { stroke: var(--muted); stroke-dasharray: 4 4; }
.rh .hy-legend { display: flex; gap: 18px; margin-top: 4px; }
.rh .hy-sw { display: inline-block; vertical-align: middle; margin-right: 6px; }
.rh .hy-sw.bar { width: 10px; height: 10px; background: var(--blue); border-radius: 2px; }
.rh .hy-sw.line { width: 14px; height: 2px; background: var(--red); }
.rh .hy-sw.peak { width: 14px; border-top: 1px dashed var(--muted); }
@media (max-width: 720px) {
  .rh .stat { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
