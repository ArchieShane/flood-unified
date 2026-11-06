<script setup>
/* 一条「随时间变化」的折线卡片：横轴为降雨开始后的小时数，纵轴一个指标；鼠标移上去读各时刻的值，不移动时显示峰值。
   可选再带一条线（`second`）画在右轴上，左右两个刻度各自取整、刻度线对齐同一组网格（总积水量 + 最大积水深度那张图）。
   `VolumeCurve.vue` 取一次数据，把各张图的数据传下来。 */
import { computed, ref } from 'vue'

import { t } from '../../i18n'

const props = defineProps({
  title: { type: String, required: true },
  points: { type: Array, required: true },            // [{ m: 降雨起算分钟, v: 值 }]，已去掉 null
  unit: { type: String, required: true },
  axisY: { type: String, required: true },
  fmt: { type: Function, required: true },            // 读数的格式化
  note: { type: String, default: '' },
  rainMin: { type: Number, default: null },            // 降雨历时（min），给了就画雨停线
  /** 画在右轴的第二条线：{ points, unit, axisY, fmt }；不给就是单线图 */
  second: { type: Object, default: null },
})

const W = 960
const HT = 280
const dual = computed(() => !!props.second?.points?.length)
const PAD = computed(() => ({ l: 78, r: dual.value ? 66 : 24, t: 16, b: 42 }))
const PW = computed(() => W - PAD.value.l - PAD.value.r)
const PH = HT - 16 - 42

function niceTicks(max, count = 5) {
  const raw = max / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw)
  const top = Math.ceil(max / step) * step
  const out = []
  for (let v = 0; v <= top + step / 2; v += step) out.push(v)
  return { ticks: out, top, step }
}
/** 右轴：刻度个数跟左轴一致（刻度线才对得上同一组网格），步长取 1 / 2 / 2.5 / 5 × 10^k 里放得下最大值的那个 */
function ticksN(max, n) {
  const raw = max / n
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw)
  return { ticks: Array.from({ length: n + 1 }, (_, i) => i * step), top: step * n, step }
}
/** 纵轴刻度：按步长实际需要的小数位留位（步长 2.5 就留 1 位，不能取整成 3 / 8；0.05 留 2 位），整数步长加千分位 */
const tickLabel = (v, step) => {
  let d = 0
  while (d < 4 && Math.abs(step * 10 ** d - Math.round(step * 10 ** d)) > 1e-9) d++
  return v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
}
const peakOf = pts => pts.reduce((a, p) => (p.v > a.v ? p : a), { m: 0, v: 0 })

const peak = computed(() => peakOf(props.points))
const peak2 = computed(() => (dual.value ? peakOf(props.second.points) : null))
const chart = computed(() => {
  const P = props.points
  if (P.length < 2 || !(peak.value.v > 0)) return null
  const { l, t: pt } = PAD.value
  const T = P[P.length - 1].m
  const y = niceTicks(peak.value.v)
  const xT = niceTicks(T / 60, 8)
  const x = m => l + (m / T) * PW.value
  const yv = v => pt + PH - (v / y.top) * PH
  const mk = (pts, f) => pts.map(p => `${x(p.m).toFixed(1)},${f(p.v).toFixed(1)}`).join(' ')
  const line = mk(P, yv)
  let r = null
  if (dual.value && peak2.value.v > 0) {
    const y2 = ticksN(peak2.value.v, y.ticks.length - 1)
    const f2 = v => pt + PH - (v / y2.top) * PH
    r = { y: f2, line: mk(props.second.points, f2), right: y2.ticks.map(v => ({ v, y: f2(v), label: tickLabel(v, y2.step) })) }
  }
  return {
    line, r,
    area: `${l},${pt + PH} ${line} ${x(T).toFixed(1)},${pt + PH}`,
    left: y.ticks.map(v => ({ v, y: yv(v), label: tickLabel(v, y.step) })),
    bottom: xT.ticks.filter(h => h * 60 <= T + 1e-6).map(h => ({ h, x: x(h * 60) })),
    rainX: props.rainMin != null && props.rainMin < T ? x(props.rainMin) : null,
    x, y: yv, T,
  }
})

const hover = ref(null)
const nearest = (pts, m) => pts.reduce((b, p) => (!b || Math.abs(p.m - m) < Math.abs(b.m - m) ? p : b), null)
function onMove(ev) {
  const c = chart.value
  if (!c) return
  const r = ev.currentTarget.getBoundingClientRect()
  const m = ((((ev.clientX - r.left) / r.width) * W - PAD.value.l) / PW.value) * c.T
  hover.value = m >= 0 && m <= c.T ? m : null
}
/** 读数：悬停取离鼠标最近的一帧，不悬停取各自的峰值（两条线的峰值时刻通常不同，所以各给各的时刻） */
const cur = computed(() => (hover.value == null ? peak.value : nearest(props.points, hover.value)))
const cur2 = computed(() => {
  if (!dual.value || !chart.value?.r) return null
  return hover.value == null ? peak2.value : nearest(props.second.points, hover.value)
})
const clock = min => `T+${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, '0')}`
</script>

<template>
  <section v-if="chart" class="sim-card vc">
    <div class="vc-head">
      <span class="vc-title">{{ title }}</span>
      <span class="vc-hover">
        <template v-if="cur2">
          <template v-if="hover != null">{{ clock(cur.m) }} · </template>
          <i class="vc-sw" />{{ fmt(cur.v) }} {{ unit }}<template v-if="hover == null"> @ {{ clock(cur.m) }}</template>&emsp;
          <i class="vc-sw b" />{{ second.fmt(cur2.v) }} {{ second.unit }}<template v-if="hover == null"> @ {{ clock(cur2.m) }} ({{ t('sim.detail.volPeak') }})</template>
        </template>
        <template v-else>{{ clock(cur.m) }} · {{ fmt(cur.v) }} {{ unit }}<template v-if="hover == null"> ({{ t('sim.detail.volPeak') }})</template></template>
      </span>
    </div>
    <svg class="vc-svg" :viewBox="`0 0 ${W} ${HT}`" role="img" :aria-label="title" @mousemove="onMove" @mouseleave="hover = null">
      <g v-for="g in chart.left" :key="'g' + g.v">
        <line :x1="PAD.l" :x2="W - PAD.r" :y1="g.y" :y2="g.y" class="vc-grid" />
        <text :x="PAD.l - 8" :y="g.y + 4" text-anchor="end" class="vc-tx" :class="{ a: chart.r }">{{ g.label }}</text>
      </g>
      <text v-for="g in chart.r?.right || []" :key="'r' + g.v" :x="W - PAD.r + 8" :y="g.y + 4" class="vc-tx b">{{ g.label }}</text>
      <text v-for="g in chart.bottom" :key="'b' + g.h" :x="g.x" :y="HT - PAD.b + 18" text-anchor="middle" class="vc-tx">{{ g.h }}</text>
      <polygon :points="chart.area" class="vc-fill" />
      <line v-if="chart.rainX != null" :x1="chart.rainX" :x2="chart.rainX" :y1="PAD.t" :y2="PAD.t + PH" class="vc-rain" />
      <text v-if="chart.rainX != null" :x="chart.rainX + 6" :y="PAD.t + 12" class="vc-tx">{{ t('sim.detail.volRainEnd') }}</text>
      <polyline :points="chart.line" class="vc-line" />
      <polyline v-if="chart.r" :points="chart.r.line" class="vc-line b" />
      <circle :cx="chart.x(cur.m)" :cy="chart.y(cur.v)" r="4" :class="chart.r ? 'vc-dot a' : 'vc-dot'" />
      <circle v-if="cur2 && chart.r" :cx="chart.x(cur2.m)" :cy="chart.r.y(cur2.v)" r="4" class="vc-dot b" />
      <line :x1="PAD.l" :x2="W - PAD.r" :y1="PAD.t + PH" :y2="PAD.t + PH" class="vc-axis" />
      <text :x="PAD.l + PW / 2" :y="HT - 6" text-anchor="middle" class="vc-tx">{{ t('sim.detail.volAxisX') }}</text>
      <text :transform="`translate(14 ${PAD.t + PH / 2}) rotate(-90)`" text-anchor="middle" class="vc-tx" :class="{ a: chart.r }">{{ axisY }}</text>
      <text v-if="chart.r" :transform="`translate(${W - 12} ${PAD.t + PH / 2}) rotate(90)`" text-anchor="middle" class="vc-tx b">{{ second.axisY }}</text>
    </svg>
    <p v-if="note" class="vc-note">{{ note }}</p>
  </section>
</template>

<style>
.vc { padding: 16px 18px 12px; margin-top: 20px; }
.vc-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.vc-title { font-weight: 600; font-size: 15px; }
.vc-hover { margin-left: auto; color: var(--fg-2); font-size: 12px; font-variant-numeric: tabular-nums; }
.vc-svg { width: 100%; height: auto; display: block; margin-top: 6px; }
.vc-svg text { font-size: 12px; }
.vc-grid { stroke: var(--line); }
.vc-axis { stroke: var(--faint); }
.vc-tx { fill: var(--muted); }
.vc-tx.a { fill: var(--blue-lt, #7fb0e6); }
.vc-tx.b { fill: var(--amber, #e0a44e); }
.vc-fill { fill: var(--blue); opacity: 0.12; }
.vc-line { fill: none; stroke: var(--blue); stroke-width: 2; stroke-linejoin: round; }
.vc-line.b { stroke: var(--amber, #e0a44e); }
.vc-dot { fill: var(--red); }
.vc-dot.a { fill: var(--blue-lt, #7fb0e6); }
.vc-dot.b { fill: var(--amber, #e0a44e); }
.vc-sw { display: inline-block; width: 14px; height: 2px; vertical-align: middle; margin-right: 6px; background: var(--blue); }
.vc-sw.b { background: var(--amber, #e0a44e); }
.vc-rain { stroke: var(--muted); stroke-dasharray: 4 4; }
.vc-note { color: var(--muted); font-size: 12px; line-height: 16px; margin: 4px 0 0; }
</style>
