<script setup>
/* 点击一个积水点弹出的「积水趋势」：该点所属连片单元逐帧的积水量 Σ(水深×面积)，可切到 ≥ 阈值的积水面积、片内最大水深。
   数据由外面取（RD-A：/api/rda/states/{态}/ponding/series；全市：/api/simulations/{id}/ponding-points/{编号}/series），
   曲线峰值 = 清单里这个点的峰值积水量，所以弹框里的数字和列表对得上。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { t } from '../../i18n'
import { fmtInt } from '../../utils/sim'

const props = defineProps({
  id: { type: String, required: true },
  series: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  rainMin: { type: Number, default: null },   // 降雨历时（min），给了就画雨停线
  kind: { type: String, default: 'point' },   // point = 模型算出的积水点；hotspot = 用户关注的积水点（客户热点，200 m 圆内）
  facts: { type: Array, default: () => [] },  // 原来点这个点时展示的信息 [[名, 值], …]：积水点 = 详情卡片，关注积水点 = 地图弹窗；弹框里照样给全
})
const emit = defineEmits(['close'])

const METRICS = [
  { key: 'vol_m3', label: 'sim.pointCurve.vol', unit: 'm³', digits: 0 },
  { key: 'area_m2', label: 'sim.pointCurve.area', unit: 'm²', digits: 0 },
  { key: 'depth_m', label: 'sim.pointCurve.depth', unit: 'm', digits: 2 },
]
const metric = ref(METRICS[0])
const fmt = v => (metric.value.digits ? Number(v).toFixed(metric.value.digits) : fmtInt(Math.round(v)))

const W = 760
const HT = 300
const PAD = { l: 70, r: 20, t: 14, b: 40 }
const PW = W - PAD.l - PAD.r
const PH = HT - PAD.t - PAD.b

function niceTicks(max, count = 5) {
  const raw = max / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw)
  const top = Math.ceil(max / step) * step
  const out = []
  for (let v = 0; v <= top + step / 2; v += step) out.push(v)
  return { ticks: out, top, step }
}

const pts = computed(() => {
  const s = props.series
  if (!s) return []
  return s.t_h.map((h, i) => ({ h, v: s[metric.value.key][i] }))
})
const peak = computed(() => pts.value.reduce((a, p) => (p.v > a.v ? p : a), { h: 0, v: 0 }))
const chart = computed(() => {
  const P = pts.value
  if (P.length < 2 || !(peak.value.v > 0)) return null
  const T = P[P.length - 1].h
  const y = niceTicks(peak.value.v)
  const xT = niceTicks(T, 8)
  const x = h => PAD.l + (h / T) * PW
  const yv = v => PAD.t + PH - (v / y.top) * PH
  const line = P.map(p => `${x(p.h).toFixed(1)},${yv(p.v).toFixed(1)}`).join(' ')
  const rainH = props.rainMin != null ? props.rainMin / 60 : null
  return {
    line, x, y: yv, T,
    area: `${PAD.l},${PAD.t + PH} ${line} ${x(T).toFixed(1)},${PAD.t + PH}`,
    left: y.ticks.map(v => ({ v, y: yv(v), label: metric.value.digits ? v.toFixed(y.step < 1 ? 1 : 0) : fmtInt(v) })),
    bottom: xT.ticks.filter(h => h <= T + 1e-6).map(h => ({ h, x: x(h) })),
    rainX: rainH != null && rainH < T ? x(rainH) : null,
  }
})

const hover = ref(null)
function onMove(ev) {
  const c = chart.value
  if (!c) return
  const r = ev.currentTarget.getBoundingClientRect()
  const h = ((((ev.clientX - r.left) / r.width) * W - PAD.l) / PW) * c.T
  let best = null
  for (const p of pts.value) if (!best || Math.abs(p.h - h) < Math.abs(best.h - h)) best = p
  hover.value = h >= 0 && h <= c.T ? best : null
}
const cur = computed(() => hover.value || peak.value)
const clock = h => `T+${Math.floor(h)}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`

/** 退水：峰值后最后一帧还剩多少（没退干就如实给出） */
const endText = computed(() => {
  const s = props.series
  if (!s) return ''
  return t('sim.pointCurve.end', { v: fmtInt(Math.round(s.vol_m3[s.vol_m3.length - 1])), h: s.t_h[s.t_h.length - 1].toFixed(1) })
})

const onKey = e => e.key === 'Escape' && emit('close')
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div class="pcm" role="dialog" aria-modal="true" :aria-label="t('sim.pointCurve.title', { id })" @click.self="emit('close')">
      <div class="pcm-box">
        <header class="pcm-head">
          <b>{{ kind === 'hotspot' ? t('sim.pointCurve.titleHot', { id }) : t('sim.pointCurve.title', { id: id.replace('SIM-', '') }) }}</b>
          <button class="pcm-x" :aria-label="t('sim.pointCurve.close')" @click="emit('close')">×</button>
        </header>

        <dl v-if="facts.length" class="pcm-facts">
          <template v-for="[k, v] in facts" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
        <p v-if="loading" class="pcm-msg">{{ t('sim.pointCurve.loading') }}</p>
        <p v-else-if="error" class="pcm-msg err">{{ error }}</p>
        <template v-else-if="series && chart">
          <div class="pcm-seg" role="tablist">
            <button v-for="m in METRICS" :key="m.key" role="tab" :class="{ on: metric.key === m.key }" :aria-selected="metric.key === m.key"
                    @click="metric = m; hover = null">{{ t(m.label) }}</button>
          </div>
          <div class="pcm-read">
            <span>{{ clock(cur.h) }} · <b>{{ fmt(cur.v) }} {{ metric.unit }}</b><template v-if="!hover"> ({{ t('sim.pointCurve.peak') }})</template></span>
            <span class="pcm-sub">{{ endText }}</span>
          </div>
          <svg class="pcm-svg" :viewBox="`0 0 ${W} ${HT}`" role="img" :aria-label="t(metric.label)" @mousemove="onMove" @mouseleave="hover = null">
            <g v-for="g in chart.left" :key="'g' + g.v">
              <line :x1="PAD.l" :x2="W - PAD.r" :y1="g.y" :y2="g.y" class="pcm-grid" />
              <text :x="PAD.l - 8" :y="g.y + 4" text-anchor="end" class="pcm-tx">{{ g.label }}</text>
            </g>
            <text v-for="g in chart.bottom" :key="'b' + g.h" :x="g.x" :y="HT - PAD.b + 18" text-anchor="middle" class="pcm-tx">{{ g.h }}</text>
            <polygon :points="chart.area" class="pcm-fill" />
            <line v-if="chart.rainX != null" :x1="chart.rainX" :x2="chart.rainX" :y1="PAD.t" :y2="PAD.t + PH" class="pcm-rain" />
            <text v-if="chart.rainX != null" :x="chart.rainX + 6" :y="PAD.t + 12" class="pcm-tx">{{ t('sim.detail.volRainEnd') }}</text>
            <polyline :points="chart.line" class="pcm-line" />
            <circle :cx="chart.x(cur.h)" :cy="chart.y(cur.v)" r="4" class="pcm-dot" />
            <line :x1="PAD.l" :x2="W - PAD.r" :y1="PAD.t + PH" :y2="PAD.t + PH" class="pcm-axis" />
            <text :x="PAD.l + PW / 2" :y="HT - 6" text-anchor="middle" class="pcm-tx">{{ t('sim.detail.volAxisX') }}</text>
            <text :transform="`translate(14 ${PAD.t + PH / 2}) rotate(-90)`" text-anchor="middle" class="pcm-tx">{{ t(metric.label) }}（{{ metric.unit }}）</text>
          </svg>
          <p class="pcm-note">{{ kind === 'hotspot'
            ? t('sim.pointCurve.noteHot', { r: series.radius_m, d: Number(series.depth_threshold_m).toFixed(2), n: series.n_cells })
            : t('sim.pointCurve.note', { d: Number(series.depth_threshold_m).toFixed(2), n: series.n_cells }) }}</p>
        </template>
        <p v-else class="pcm-msg">{{ t('sim.pointCurve.empty') }}</p>
      </div>
    </div>
  </Teleport>
</template>

<style>
.pcm { position: fixed; inset: 0; z-index: 2000; background: rgba(0, 0, 0, 0.55); display: flex; align-items: center; justify-content: center; padding: 24px; }
.pcm-box { background: var(--card, #1c1e21); color: var(--fg, #fff); border: 1px solid var(--line-2, rgba(255, 255, 255, 0.12)); border-radius: 8px;
  width: min(820px, 100%); max-height: 100%; overflow: auto; padding: 16px 20px 14px; }
.pcm-head { display: flex; align-items: center; justify-content: space-between; font-size: 15px; }
.pcm-x { background: none; border: 0; color: var(--muted, rgba(255, 255, 255, 0.5)); font-size: 22px; line-height: 1; cursor: pointer; padding: 0 4px; }
.pcm-x:hover { color: var(--fg, #fff); }
.pcm-facts { display: grid; grid-template-columns: max-content 1fr; gap: 3px 14px; margin: 10px 0 0; padding: 10px 12px; font-size: 12px; line-height: 17px;
  background: var(--card-2, #212327); border-radius: 6px; }
.pcm-facts dt { color: var(--muted, rgba(255, 255, 255, 0.5)); }
.pcm-facts dd { margin: 0; color: var(--fg, #fff); overflow-wrap: anywhere; }
.pcm-msg { color: var(--muted, rgba(255, 255, 255, 0.5)); padding: 28px 0; text-align: center; margin: 0; }
.pcm-msg.err { color: var(--red, #d9434b); }
.pcm-seg { display: inline-flex; gap: 2px; margin-top: 10px; background: var(--card-2, #212327); border-radius: 6px; padding: 2px; }
.pcm-seg button { background: none; border: 0; color: var(--fg-2, rgba(255, 255, 255, 0.72)); font-size: 12px; padding: 4px 12px; border-radius: 4px; cursor: pointer; }
.pcm-seg button.on { background: var(--blue, #4786c9); color: #fff; }
.pcm-read { display: flex; gap: 14px; flex-wrap: wrap; align-items: baseline; margin-top: 10px; font-size: 13px; font-variant-numeric: tabular-nums; }
.pcm-sub { margin-left: auto; color: var(--muted, rgba(255, 255, 255, 0.5)); font-size: 12px; }
.pcm-svg { width: 100%; height: auto; display: block; margin-top: 4px; }
.pcm-svg text { font-size: 12px; }
.pcm-grid { stroke: var(--line, rgba(255, 255, 255, 0.07)); }
.pcm-axis { stroke: var(--faint, rgba(255, 255, 255, 0.3)); }
.pcm-tx { fill: var(--muted, rgba(255, 255, 255, 0.5)); }
.pcm-fill { fill: var(--blue, #4786c9); opacity: 0.12; }
.pcm-line { fill: none; stroke: var(--blue, #4786c9); stroke-width: 2; stroke-linejoin: round; }
.pcm-dot { fill: var(--red, #d9434b); }
.pcm-rain { stroke: var(--muted, rgba(255, 255, 255, 0.5)); stroke-dasharray: 4 4; }
.pcm-note { color: var(--muted, rgba(255, 255, 255, 0.5)); font-size: 12px; line-height: 16px; margin: 4px 0 0; }
</style>
