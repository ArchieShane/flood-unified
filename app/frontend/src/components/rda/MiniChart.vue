<script setup>
/* 极简折线 / 柱状图（SVG，无依赖）：RDA 页的时间轴面积曲线、降雨过程、热点水深过程共用。
   series: [{ x: number[], y: number[], color, width, dash, bars }]；marker 是当前时刻竖线；hline 是水平参考线（如 0.30 m）。 */
import { computed } from 'vue'

const props = defineProps({
  series: { type: Array, required: true },
  xMax: { type: Number, default: null },
  yMax: { type: Number, default: null },
  marker: { type: Number, default: null },
  hline: { type: Number, default: null },
  height: { type: Number, default: 90 },
  xTicks: { type: Number, default: 11 },
  yLabel: { type: String, default: '' },
})
const W = 440
const pad = { l: 34, r: 6, t: 6, b: 16 }

const xmax = computed(() => props.xMax ?? Math.max(1, ...props.series.flatMap(s => s.x)))
const ymax = computed(() => {
  const m = props.yMax ?? Math.max(...props.series.flatMap(s => s.y), 0.001)
  return m || 1
})
const sx = x => pad.l + (x / xmax.value) * (W - pad.l - pad.r)
const sy = y => props.height - pad.b - (y / ymax.value) * (props.height - pad.t - pad.b)
const path = s => s.x.map((x, i) => `${i ? 'L' : 'M'}${sx(x).toFixed(1)},${sy(s.y[i]).toFixed(1)}`).join('')
const barW = s => Math.max(1, ((W - pad.l - pad.r) / s.x.length) * 0.8)
const yTicks = computed(() => [0, 0.5, 1].map(f => ({ v: f * ymax.value, y: sy(f * ymax.value) })))
const fmt = v => (v >= 100 ? Math.round(v) : v >= 10 ? v.toFixed(0) : v.toFixed(2).replace(/\.?0+$/, ''))
</script>

<template>
  <svg :viewBox="`0 0 ${W} ${height}`" class="mc" preserveAspectRatio="none">
    <g class="grid">
      <line v-for="t in yTicks" :key="t.v" :x1="pad.l" :x2="W - pad.r" :y1="t.y" :y2="t.y" />
      <text v-for="t in yTicks" :key="'t' + t.v" :x="pad.l - 4" :y="t.y + 3" text-anchor="end">{{ fmt(t.v) }}</text>
      <text v-for="i in xTicks" :key="'x' + i" :x="sx((i - 1) * (xmax / (xTicks - 1)))" :y="height - 4" text-anchor="middle">
        {{ fmt((i - 1) * (xmax / (xTicks - 1))) }}
      </text>
    </g>
    <line v-if="hline != null" :x1="pad.l" :x2="W - pad.r" :y1="sy(hline)" :y2="sy(hline)" class="hl" />
    <template v-for="(s, i) in series" :key="i">
      <template v-if="s.bars">
        <rect v-for="(x, j) in s.x" :key="j" :x="sx(x) - barW(s) / 2" :y="sy(s.y[j])" :width="barW(s)"
          :height="Math.max(0, height - pad.b - sy(s.y[j]))" :fill="s.color" />
      </template>
      <path v-else :d="path(s)" fill="none" :stroke="s.color" :stroke-width="s.width || 1.6" :stroke-dasharray="s.dash || ''" />
    </template>
    <line v-if="marker != null" :x1="sx(marker)" :x2="sx(marker)" :y1="pad.t" :y2="height - pad.b" class="mk" />
    <text v-if="yLabel" :x="pad.l + 2" :y="pad.t + 8" class="yl">{{ yLabel }}</text>
  </svg>
</template>

<style scoped>
.mc { width: 100%; display: block; }
.grid line { stroke: rgba(255, 255, 255, 0.08); }
.grid text { fill: rgba(255, 255, 255, 0.45); font-size: 9px; }
.hl { stroke: #d9434b; stroke-dasharray: 3 3; }
.mk { stroke: #e0a44e; stroke-width: 1.2; }
.yl { fill: rgba(255, 255, 255, 0.5); font-size: 9px; }
</style>
