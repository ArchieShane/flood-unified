<script setup>
/* 一条派生雨型的读数 + 柱线图（`/api/hyetograph` 的返回体原样喂进来）。
   指挥中心的雨型图页签与仿真任务详情共用；样式按主题各写一份（command-centre.css / simulation.css 的 hy-*）。 */
import { computed, ref } from 'vue'

import { t } from '../i18n'
import { fmt } from '../utils/format'

const props = defineProps({
  /** /api/hyetograph 的返回 */
  h: { type: Object, required: true },
  title: { type: String, default: '' },
})

const mm = P => fmt(P, Number.isInteger(P) ? 0 : 1)
function eqText(eq) {
  if (eq.kind === 'within') return t('hyeto.eqWithin', { n: fmt(eq.years, eq.years < 10 ? 1 : 0) })
  if (eq.kind === 'above') return t('hyeto.eqAbove', { r: fmt(eq.ratio, 2) })
  return t('hyeto.eqBelow', { r: fmt(eq.ratio, 2) })
}

// SVG 画布：固定 viewBox，宽度随容器缩放
const W = 960
const HT = 320
const PAD = { l: 58, r: 62, t: 16, b: 42 }
const PW = W - PAD.l - PAD.r
const PH = HT - PAD.t - PAD.b

/** 取「好看」的刻度：1 / 2 / 2.5 / 5 × 10^k。 */
function niceTicks(max, count = 5) {
  const raw = max / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw)
  const top = Math.ceil(max / step) * step
  const out = []
  for (let v = 0; v <= top + step / 2; v += step) out.push(v)
  return { ticks: out, top, step }
}

const chart = computed(() => {
  const { t_min: ts, step_depth_mm: depth, intensity_mm_hr: intensity, step_min: step, duration_min: D, peak_pct } =
    props.h
  const yL = niceTicks(Math.max(...depth))
  const yR = niceTicks(Math.max(...intensity))
  const xT = niceTicks(D, 8)
  const x = m => PAD.l + (m / D) * PW
  const yl = v => PAD.t + PH - (v / yL.top) * PH
  const yr = v => PAD.t + PH - (v / yR.top) * PH
  const bw = (step / D) * PW
  const gap = Math.min(2, bw * 0.12)
  return {
    bars: depth.map((d, i) => ({ x: x(ts[i]) + gap / 2, y: yl(d), w: bw - gap, h: PAD.t + PH - yl(d) })),
    line: intensity.map((v, i) => `${x(ts[i] + step / 2).toFixed(1)},${yr(v).toFixed(1)}`).join(' '),
    dots: depth.length <= 60 ? intensity.map((v, i) => ({ cx: x(ts[i] + step / 2), cy: yr(v) })) : [],
    left: yL.ticks.map(v => ({ v, y: yl(v), label: fmt(v, yL.step < 1 ? 1 : 0) })),
    right: yR.ticks.map(v => ({ v, y: yr(v), label: fmt(v, yR.step < 1 ? 1 : 0) })),
    bottom: xT.ticks.filter(v => v <= D).map(v => ({ v, x: x(v) })),
    peakX: x((peak_pct / 100) * D),
  }
})

const hover = ref(null)
function onMove(ev) {
  const r = ev.currentTarget.getBoundingClientRect()
  const mx = ((ev.clientX - r.left) / r.width) * W
  const n = props.h.step_depth_mm.length
  const i = Math.floor(((mx - PAD.l) / PW) * n)
  hover.value = i >= 0 && i < n ? i : null
}
const hoverText = computed(() => {
  const H = props.h
  const i = hover.value ?? H.peak_index
  return t('hyeto.hover', {
    a: H.t_min[i],
    b: H.t_min[i] + H.step_min,
    d: fmt(H.step_depth_mm[i], 2),
    i: fmt(H.intensity_mm_hr[i], 1),
  })
})
</script>

<template>
  <div class="hy-chart">
    <div class="stat hy-stat">
      <div><div class="v">{{ mm(h.depth_mm) }} mm</div><div class="l">{{ t('hyeto.kTotal') }}</div></div>
      <div>
        <div class="v">{{ fmt(h.peak_intensity_5min_mm_hr, 1) }} mm/h</div>
        <div class="l">{{ t('hyeto.kPeakI') }}</div>
      </div>
      <div>
        <div class="v">{{ h.t_min[h.peak_index] }}–{{ h.t_min[h.peak_index] + h.step_min }} min</div>
        <div class="l">{{ t('hyeto.kPeakT') }}</div>
      </div>
      <div>
        <div class="v" :class="{ warn: h.equivalent.kind !== 'within' }">{{ eqText(h.equivalent) }}</div>
        <div class="l">
          {{ t('hyeto.kEq') }}<template v-if="h.equivalent.kind !== 'within'"> · {{ t('hyeto.extrapolated') }}</template>
        </div>
      </div>
    </div>

    <div class="hy-cap">
      <b v-if="title">{{ title }}</b>
      <span class="note">{{ t('hyeto.caption', { step: h.step_min, peak: h.peak_pct, n: h.step_depth_mm.length })
        }}<template v-if="h.step_min !== 5">
          · {{ t('hyeto.stepPeakNote', { step: h.step_min, v: fmt(h.intensity_mm_hr[h.peak_index], 1) }) }}</template
        ></span>
      <span class="hy-hover">{{ hoverText }}</span>
    </div>

    <svg
      class="hy-svg"
      :viewBox="`0 0 ${W} ${HT}`"
      role="img"
      :aria-label="title"
      @mousemove="onMove"
      @mouseleave="hover = null"
    >
      <g v-for="g in chart.left" :key="'gl' + g.v">
        <line :x1="PAD.l" :x2="W - PAD.r" :y1="g.y" :y2="g.y" class="hy-gridline" />
        <text :x="PAD.l - 8" :y="g.y + 4" text-anchor="end" class="hy-tl">{{ g.label }}</text>
      </g>
      <text v-for="g in chart.right" :key="'gr' + g.v" :x="W - PAD.r + 8" :y="g.y + 4" class="hy-tr">
        {{ g.label }}
      </text>
      <text v-for="g in chart.bottom" :key="'gb' + g.v" :x="g.x" :y="HT - PAD.b + 18" text-anchor="middle" class="hy-tx">
        {{ g.v }}
      </text>

      <rect
        v-for="(b, i) in chart.bars"
        :key="'b' + i"
        :x="b.x"
        :y="b.y"
        :width="b.w"
        :height="b.h"
        class="hy-bar"
        :class="{ hot: i === hover }"
      />
      <line :x1="chart.peakX" :x2="chart.peakX" :y1="PAD.t" :y2="PAD.t + PH" class="hy-peak" />
      <polyline :points="chart.line" class="hy-line" />
      <circle v-for="(d, i) in chart.dots" :key="'d' + i" :cx="d.cx" :cy="d.cy" r="3" class="hy-dot" />
      <line :x1="PAD.l" :x2="W - PAD.r" :y1="PAD.t + PH" :y2="PAD.t + PH" class="hy-axis" />

      <text :x="PAD.l + PW / 2" :y="HT - 6" text-anchor="middle" class="hy-tx">{{ t('hyeto.axisX') }}</text>
      <text :transform="`translate(14 ${PAD.t + PH / 2}) rotate(-90)`" text-anchor="middle" class="hy-tl">
        {{ t('hyeto.axisDepth', { n: h.step_min }) }}
      </text>
      <text :transform="`translate(${W - 16} ${PAD.t + PH / 2}) rotate(90)`" text-anchor="middle" class="hy-tr">
        {{ t('hyeto.axisI', { n: h.step_min }) }}
      </text>
    </svg>

    <div class="hy-legend note">
      <span><i class="hy-sw bar" />{{ t('hyeto.legDepth') }}</span>
      <span><i class="hy-sw line" />{{ t('hyeto.legI', { n: h.step_min }) }}</span>
      <span><i class="hy-sw peak" />{{ t('hyeto.legPeak', { n: h.peak_pct }) }}</span>
    </div>
  </div>
</template>
