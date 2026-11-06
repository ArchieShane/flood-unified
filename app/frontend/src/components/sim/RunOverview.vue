<script setup>
/* 任务详情右侧的「{防线}仿真」总览（Figma 01 Line 1 Simulation 高保真）。
   数据全部来自 ResultMap 已取回的两份：/layers（最大水深、积水面积）与 /layers/ponding
   （易积水片区及其退水统计，退水口径见 sim_ponding.recession）。这里不再另发请求。
   演算末仍有水的片区单列「未退干」，退水时间只在已退干的片区里取中位 —— 不拿演算时长顶替。
   底部按钮进「效果分析」（Figma Performance Analysis，PerfPanel）：要多态任务才有，单一交付置灰。 */
import { computed } from 'vue'

import { t } from '../../i18n'
import { fmtInt, fmtKm2 } from '../../utils/sim'

const props = defineProps({
  run: { type: Object, required: true },
  meta: { type: Object, default: null },
  ponding: { type: Object, default: null },
  error: { type: String, default: '' },
  active: { type: Number, default: null },
})
const emit = defineEmits(['focus', 'perf'])

const num = x => (x === null || x === undefined ? '—' : String(Number(x)))
const fmtH = h => (h == null ? '—' : Number(h).toFixed(1))

const rainText = computed(() => {
  const r = props.run
  if (r.rain_source === 'observed_replay') return r.replay_event_ref || r.rain_label || '—'
  return t('sim.storm', { d: num(r.depth_mm), h: num(r.duration_h) })
})

const rec = computed(() => props.ponding?.recession || null)
const kpis = computed(() => {
  const r = props.meta?.results || {}
  // sww 多态交付有「同一时刻」的最大积水面积（三角形 ≥ 2 cm，交付口径）；别的交付只有全程 ≥ 1 cm 的格并集
  const peak = r.peak_inundated_area_m2 != null
  const a = peak ? r.peak_inundated_area_m2 : r.inundated_area_ge_0_01m2
  const med = rec.value?.median_h
  return [
    { k: 'maxDepth', v: r.maximum_depth_m == null ? '—' : Number(r.maximum_depth_m).toFixed(2), u: 'm' },
    { k: peak ? 'maxAreaPeak' : 'maxArea', v: a == null ? '—' : fmtKm2(a / 1e6), u: 'km²' },
    med != null
      ? { k: 'recession', v: fmtH(med), u: 'h' }
      : { k: 'recession', v: rec.value ? t('sim.panel.undrainedAll') : '—', u: '', text: true },
  ]
})

const BIN_COLORS = ['var(--green)', 'var(--blue-lt)', 'var(--amber)', 'var(--red)']
const bins = computed(() => {
  if (!rec.value) return []
  const labels = t('sim.panel.bins')
  const rows = rec.value.counts.map((n, i) => ({ label: labels[i], n, color: BIN_COLORS[i] }))
  rows.push({ label: t('sim.panel.undrained'), n: rec.value.undrained, color: 'rgba(255, 255, 255, 0.35)' })
  const max = Math.max(1, ...rows.map(r => r.n))
  return rows.map(r => ({ ...r, pct: (r.n / max) * 100 }))
})
const noRecession = computed(() =>
  (props.ponding?.deviations || []).some(k => k === 'no_recession' || k === 'partial_run'))

/** 峰值水深前 10：≥ 0.30 m（片区入选的水深判据）标红，其余标橙 */
const top = computed(() => {
  const c = [...(props.ponding?.clusters || [])].sort((a, b) => b.max_depth_m - a.max_depth_m).slice(0, 10)
  const max = c[0]?.max_depth_m || 1
  return c.map(x => ({
    ...x,
    pct: (x.max_depth_m / max) * 100,
    color: x.max_depth_m >= (props.ponding?.criteria?.select_max_depth_m ?? 0.3) ? 'var(--red)' : 'var(--amber)',
  }))
})
</script>

<template>
  <aside class="sim-card ro">
    <h2 class="ro-title">{{ t('sim.panel.title', { line: run.defence_line_label || '' }) }}</h2>

    <section class="ro-sec">
      <h3>{{ t('sim.panel.thisRun') }}</h3>
      <div class="ro-kv">
        <span class="k">{{ t('sim.panel.rain') }}</span>
        <span class="v">{{ rainText }}</span>
        <span class="k">{{ t('sim.panel.extent') }}</span>
        <span class="v">{{ run.extent || '—' }} {{ run.grid_m ? `${num(run.grid_m)} m` : '' }}</span>
        <span class="k">{{ t('sim.panel.basis') }}</span><span class="v">{{ run.defence_line_label || '—' }}</span>
      </div>
    </section>

    <section class="ro-sec">
      <h3>{{ t('sim.panel.result') }}</h3>
      <div class="ro-kpis">
        <div v-for="x in kpis" :key="x.k">
          <div class="big" :class="{ text: x.text }">{{ x.v }}<small v-if="x.u">{{ x.u }}</small></div>
          <div class="cap">{{ t(`sim.panel.${x.k}`) }}</div>
          <div v-if="x.k === 'recession' && rec" class="cap sub">
            {{ t('sim.panel.drainedOf', { d: fmtInt(ponding.clusters.length - rec.undrained), n: fmtInt(ponding.clusters.length) }) }}
          </div>
        </div>
      </div>

      <div class="ro-box">
        <h4>{{ t('sim.panel.recessTime') }}</h4>
        <p v-if="error" class="ro-err">{{ error }}</p>
        <p v-else-if="!ponding" class="ro-note">{{ t('sim.panel.loading') }}</p>
        <div v-for="b in bins" :key="b.label" class="ro-bin">
          <span class="lb">{{ b.label }}</span>
          <span class="track"><span class="bar" :style="{ width: b.pct + '%', background: b.color }" /></span>
          <span class="n">{{ fmtInt(b.n) }}</span>
        </div>
        <p v-if="ponding" class="ro-note">{{ t('sim.panel.recessRule', { n: ponding.clusters.length }) }}</p>
        <p v-if="noRecession" class="ro-warn">{{ t('sim.panel.noRecession') }}</p>
      </div>
    </section>

    <section v-if="top.length" class="ro-sec">
      <h3>{{ t('sim.panel.top') }}</h3>
      <div class="ro-top">
        <button v-for="c in top" :key="c.rank" class="ro-row" :class="{ on: c.rank === active }"
                :title="c.name || `${c.lat}, ${c.lon}`" @click="emit('focus', c.rank)">
          <span class="nm">{{ c.name || `#${c.rank} ${c.lat}, ${c.lon}` }}</span>
          <span class="track"><span class="bar" :style="{ width: c.pct + '%', background: c.color }" /></span>
          <span class="d">{{ c.max_depth_m.toFixed(2) }} m</span>
          <span class="h" :class="{ und: !c.drained }">{{ c.drained ? `${fmtH(c.recession_h)} h` : t('sim.panel.notDrained') }}</span>
        </button>
      </div>
    </section>

    <button class="ro-gap" :disabled="!run.states" :title="run.states ? '' : t('sim.panel.gapSoon')" @click="emit('perf')">
      {{ t('sim.panel.gap') }}
    </button>
  </aside>
</template>

<style scoped>
.ro { padding: 22px 22px 18px; display: flex; flex-direction: column; background: linear-gradient(180deg, #1f2124, #17181b); }
.ro-title { font-size: 20px; font-weight: 500; margin: 0; padding-bottom: 14px; border-bottom: 1px solid var(--line-2); }
.ro-sec { margin-top: 18px; }
.ro-sec h3 { font-size: 15px; font-weight: 700; margin: 0 0 12px; }
.ro-kv { display: grid; grid-template-columns: 112px minmax(0, 1fr); row-gap: 12px; column-gap: 12px; font-size: 13px; }
.ro-kv .k { color: var(--muted); }
.ro-kv .v { color: var(--fg); }
.ro-kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 4px 0 16px; }
.ro-kpis .big { font-size: 26px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.ro-kpis .big.text { font-size: 18px; line-height: 34px; color: var(--amber); }
.ro-kpis .big small { font-size: 13px; font-weight: 400; color: var(--fg-2); margin-left: 4px; }
.ro-kpis .cap { font-size: 12px; color: var(--muted); margin-top: 4px; }
.ro-kpis .cap.sub { margin-top: 2px; color: var(--faint); }
.ro-box { background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 14px 16px; }
.ro-box h4 { font-size: 13px; font-weight: 600; margin: 0 0 10px; }
.ro-bin { display: grid; grid-template-columns: 104px minmax(0, 1fr) 40px; align-items: center; gap: 10px;
  font-size: 13px; line-height: 30px; }
.ro-bin .lb { color: var(--fg-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ro-bin .n { text-align: right; font-variant-numeric: tabular-nums; }
.track { display: block; height: 7px; border-radius: 4px; background: rgba(255, 255, 255, 0.06); overflow: hidden; }
.bar { display: block; height: 100%; border-radius: 4px; min-width: 2px; }
.ro-note { color: var(--muted); font-size: 11.5px; margin: 8px 0 0; line-height: 1.5; }
.ro-warn { color: var(--amber); font-size: 11.5px; margin: 6px 0 0; line-height: 1.5; }
.ro-err { color: var(--red); font-size: 12px; margin: 0; }
.ro-top { max-height: 330px; overflow-y: auto; margin-right: -6px; padding-right: 6px;
  mask-image: linear-gradient(180deg, #000 88%, transparent); }
.ro-row { display: grid; grid-template-columns: minmax(0, 1fr) 72px 54px 62px; align-items: center; gap: 8px;
  width: 100%; border: 0; background: none; padding: 0 4px; line-height: 34px; font-size: 12.5px; text-align: left;
  cursor: pointer; border-radius: 4px; }
.ro-row:hover { background: rgba(255, 255, 255, 0.04); }
.ro-row.on { background: var(--blue-bg); }
.ro-row .nm { color: var(--fg-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ro-row .d, .ro-row .h { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ro-row .h.und { color: var(--amber); font-size: 11.5px; }
.ro-gap { margin-top: 18px; height: 44px; border: 0; border-radius: 8px; background: #4aa3ea; color: #fff;
  font-size: 15px; font-weight: 500; cursor: pointer; }
.ro-gap:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
