<script setup>
/* 全市任务详情右侧的「第二道现状仿真」总览（高保真 Line 2 Current Simulation），版式与第一道总览（RunOverview）对齐：
   降雨情景 → 与第一道对比（三项，各两根条 + 减少了多少）→ 峰值水深前 10 → 效果分析。
   第二道没有二维演算：三项与前 10 一律按「L2 = L1 × (1 − 减少比例)」折算（用户 2026-09-28 定的口径），
   减少比例取 /api/simulations/{id}/l2 的 estimate（一维报告全市洪水量减少 ≈ 2.6%；33 / 96 mm 借 61 mm 的比例）。
   前 10 用地图已取回的 /layers/ponding；页面底部照实标「估算」。
   三项指标（用户 2026-09-28 定）：Outflow Volume（管网溢出地表）/ Flooded Nodes（溢流节点）/ Flood Duration（节点平均溢流时长），
   都是耦合 SWMM 报告的全程量（sim_perf._rpt）；第一道取 /l2 的 l1_network。
   规划态（plan，Mussafah 的 L2+A / L2+B）：三行 = 第一道 / 第二道现状 / 该态，第一道与该态取效果分析（/states/perf）的
   network.base / vs（两态溢流都在共同演算窗内结束才 comparable）；第二道现状取交付的 L2 态实算（perf.columns），不折算。
   右上角的减少量是该态比第一道；前 10 取该态自己的溢水点，不折算。 */
import { computed } from 'vue'

import { t } from '../../i18n'
import { fmtInt, fmtLatLon, stateCode } from '../../utils/sim'

const props = defineProps({
  run: { type: Object, required: true },
  data: { type: Object, default: null },     // /l2
  meta: { type: Object, default: null },     // 第一道 /layers
  ponding: { type: Object, default: null },  // 第一道 /layers/ponding
  error: { type: String, default: '' },
  active: { type: Number, default: null },
  plan: { type: Object, default: null },     // { key, label, perf }：规划态对比（perf = /states/perf?vs=key）
})
const emit = defineEmits(['focus', 'perf'])

const num = x => (x === null || x === undefined ? '—' : String(Number(x)))
const lineLabel = computed(() => t('sim.detail.lineTab.line2_current'))
const titleLabel = computed(() => stateCode(props.plan?.label) || lineLabel.value)
const l1Label = computed(() => t('sim.detail.lineTab.line1'))

const rainText = computed(() => t('sim.storm', { d: num(props.run.depth_mm), h: num(props.run.duration_h) }))

const est = computed(() => props.data?.estimate || null)
const keep = computed(() => (est.value ? 1 - est.value.reduction_ratio : null))
const l2Of = x => (x == null || keep.value == null ? null : x * keep.value)

/** 三项对比。第一道先取到显示精度再折算（0.91 × 0.974 = 0.89），第二道能拿屏幕上的第一道直接复算；条长按各行最大值归一。
 *  右上角减少量：普通第二道页是第二道比第一道；规划态页是该态（实算）比第一道。 */
const SPEC = [
  { k: 'outflow', src: 'overflow_to_surface_m3', u: 'm³', fmt: v => fmtInt(Math.round(v)) },
  { k: 'nodes', src: 'flooded_node_count', u: '', fmt: v => fmtInt(Math.round(v)) },
  { k: 'duration', src: 'mean_flood_hours', u: 'h', fmt: v => Number(v).toFixed(2) },
]
const perf = computed(() => props.plan?.perf || null)
/** 第一道：普通第二道页取 /l2 的 l1_network（全程）；规划态取效果分析的 network.base（与该态同口径） */
const baseNet = computed(() => (props.plan ? perf.value?.network.base : props.data?.l1_network) || null)
const planNet = computed(() => perf.value?.network.vs || null)
const baseVals = computed(() => Object.fromEntries(SPEC.map(x => [x.k, baseNet.value?.[x.src] ?? null])))
/** 规划态页的第二道现状取交付实算（perf.columns 里 column=line2_current 那一态；Mussafah 的 L2 ≡ L1），
 *  不再按 2.6% 折算——折算值和实算的规划态混排会出现「规划态比第二道还多」的倒挂（用户 2026-09-28 定） */
const l2Real = computed(() => perf.value?.columns?.find(c => c.column === 'line2_current') || null)
const l2Vals = computed(() => Object.fromEntries(SPEC.map(x => [x.k, l2Real.value?.network?.[x.src] ?? null])))
const planVals = computed(() => Object.fromEntries(SPEC.map(x => [x.k, planNet.value?.[x.src] ?? null])))
const dims = computed(() => SPEC.map(x => {
  const raw = baseVals.value[x.k]
  const l1 = raw == null ? null : Number(x.fmt(raw).replace(/,/g, ''))
  const rows = [
    { key: 'l1', label: l1Label.value, v: l1, color: 'var(--red)' },
    { key: 'l2', label: lineLabel.value, v: props.plan ? l2Vals.value[x.k] : l2Of(l1), color: 'var(--amber)' },
  ]
  if (props.plan) rows.push({ key: 'plan', label: props.plan.key, v: planVals.value[x.k] ?? null, color: 'var(--blue-lt)' })
  const max = Math.max(0, ...rows.map(r => r.v ?? 0))
  const subj = rows.at(-1).v
  // 规划态是两份实算相减，用第一道原值（取整后的 0.99 对 0.993 会算成「增加」）；第二道折算本就基于取整值
  const ref = props.plan ? raw : l1
  const diff = ref == null || subj == null ? null : ref - subj
  const fd = v => x.fmt(v)
  return {
    ...x,
    rows: rows.map(r => ({ ...r, text: r.v == null ? '—' : x.fmt(r.v), pct: max ? ((r.v ?? 0) / max) * 100 : 0 })),
    dv: diff == null ? null : `${diff >= 0 ? '−' : '+'}${fd(Math.abs(diff))}`,
    // 溢流节点数是整数，第二道折算后四舍五入
    dp: diff == null || !ref ? null : `${diff >= 0 ? '−' : '+'}${Math.abs((diff / ref) * 100).toFixed(1)}%`,
    up: diff != null && diff < 0,
  }
}))
const pctText = computed(() => (est.value ? (est.value.reduction_ratio * 100).toFixed(1) : '—'))
const netNote = computed(() => t('sim.l2.netNote'))
const notComparable = computed(() => props.plan && perf.value && !perf.value.network.comparable)

/** 峰值水深前 10：沿用第一道的溢水点与名次，水深、退水时间按同一比例折算；未退干的仍写未退干 */
const top = computed(() => {
  if (!props.plan && keep.value == null) return []
  const f = props.plan ? v => v : l2Of
  const c = [...(props.ponding?.clusters || [])].sort((a, b) => b.max_depth_m - a.max_depth_m).slice(0, 10)
  const th = props.ponding?.criteria?.select_max_depth_m ?? 0.3
  const max = f(Number(c[0]?.max_depth_m?.toFixed(2))) || 1
  return c.map(x => {
    const d = f(Number(x.max_depth_m.toFixed(2)))
    return {
      ...x,
      d2: d,
      h2: x.drained ? f(Number(Number(x.recession_h).toFixed(1))) : null,
      pct: (d / max) * 100,
      color: d >= th ? 'var(--red)' : 'var(--amber)',
    }
  })
})
</script>

<template>
  <aside class="sim-card ro l2">
    <h2 class="ro-title">{{ t('sim.panel.title', { line: titleLabel }) }}</h2>

    <section class="ro-sec">
      <h3>{{ t('sim.l2.rainTitle') }}</h3>
      <div class="ro-kv">
        <span class="k">{{ t('sim.panel.rain') }}</span>
        <span class="v">{{ rainText }}</span>
        <span class="k">{{ t('sim.panel.extent') }}</span>
        <span class="v">{{ run.extent || '—' }} {{ run.grid_m ? `${num(run.grid_m)} m` : '' }}</span>
        <span class="k">{{ t('sim.panel.basis') }}</span><span class="v">{{ titleLabel }}</span>
      </div>
    </section>

    <section class="ro-sec">
      <h3>{{ t(plan ? 'sim.l2.vsL1L2' : 'sim.l2.vsL1') }}</h3>
      <p v-if="error" class="ro-err">{{ error }}</p>
      <p v-else-if="!data || (plan && !perf)" class="ro-note" style="margin: 0 0 10px">{{ t('sim.l2.loading') }}</p>
      <div v-for="x in dims" :key="x.k" class="ro-box l2-cmp">
        <div class="l2-head">
          <h4>{{ t(`sim.l2.dim.${x.k}`) }}</h4>
          <span v-if="x.dv != null" class="l2-down" :class="{ up: x.up }">
            <em v-if="plan">{{ stateCode(plan.key) }}</em>{{ x.dv }} {{ x.u }}<small>{{ x.dp }}</small></span>
        </div>
        <div v-for="r in x.rows" :key="r.key" class="l2-bar">
          <span class="lb" :title="r.label">{{ r.label }}</span>
          <span class="track"><span v-if="r.v != null" class="bar" :style="{ width: r.pct + '%', background: r.color }" /></span>
          <span class="n">{{ r.text }}<small v-if="r.v != null">{{ x.u }}</small></span>
        </div>
      </div>
    </section>

    <p class="ro-note">{{ netNote }}</p>
    <p v-if="notComparable" class="ro-warn">{{ t('sim.l2.notComparable') }}</p>

    <section v-if="top.length" class="ro-sec">
      <h3>{{ t('sim.panel.top') }}</h3>
      <div class="ro-top">
        <button v-for="c in top" :key="c.rank" class="ro-row" :class="{ on: c.rank === active }"
                :title="[c.name, fmtLatLon(c)].filter(Boolean).join('\n')" @click="emit('focus', c.rank)">
          <span class="nm">{{ c.name || `#${c.rank}` }}<small v-if="fmtLatLon(c)">{{ fmtLatLon(c) }}</small></span>
          <span class="track"><span class="bar" :style="{ width: c.pct + '%', background: c.color }" /></span>
          <span class="d">{{ c.d2.toFixed(2) }} m</span>
          <span class="h" :class="{ und: c.h2 == null }">{{ c.h2 != null ? `${c.h2.toFixed(1)} h` : t('sim.panel.notDrained') }}</span>
        </button>
      </div>
    </section>

    <p v-if="plan && est && perf" class="ro-warn">{{ t('sim.l2.estPlan', { s: plan.key, h: Math.round(perf.common_end_minutes / 6) / 10, same: l2Real?.same_as ? t('sim.l2.samePlan') : '' }) }}</p>
    <p v-else-if="est" class="ro-warn">{{ t(est.borrowed ? 'sim.l2.estBorrowed' : 'sim.l2.est', {
      pct: pctText, d: num(est.from_storm.depth_mm), h: num(est.from_storm.duration_h) }) }}</p>

    <p v-if="data?.same_as && !plan" class="ro-note">{{ t('sim.l2.estSameAs', { s: data.state, as: data.same_as }) }}</p>

    <button class="ro-gap" :disabled="!plan" :title="plan ? '' : t('sim.l2.perfSoon')" @click="emit('perf')">{{ t('sim.panel.gap') }}</button>
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
.ro-box { background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 14px 16px; }
.ro-box + .ro-box { margin-top: 10px; }
.l2-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.l2-head h4 { font-size: 13px; font-weight: 600; margin: 0; }
.l2-down { color: var(--green); font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.l2-down small { margin-left: 6px; font-weight: 400; opacity: 0.85; }
.l2-down.up { color: var(--amber); }
.l2-down em { font-style: normal; font-weight: 400; color: var(--muted); margin-right: 6px; }
.l2-bar { display: grid; grid-template-columns: 96px minmax(0, 1fr) 76px; align-items: center; gap: 10px; font-size: 13px; line-height: 32px; }
.l2-bar .lb { color: var(--fg-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.l2-bar .n { text-align: right; font-variant-numeric: tabular-nums; }
.l2-bar .n small { font-size: 11px; color: var(--muted); margin-left: 3px; }
.track { display: block; height: 7px; border-radius: 4px; background: rgba(255, 255, 255, 0.06); overflow: hidden; }
.bar { display: block; height: 100%; border-radius: 4px; min-width: 2px; }
.ro-note { color: var(--muted); font-size: 11.5px; margin: 6px 0 0; line-height: 1.5; }
.ro-warn { color: var(--amber); font-size: 11.5px; margin: 14px 0 0; line-height: 1.5; }
.ro-err { color: var(--red); font-size: 12px; margin: 0 0 10px; }
.ro-top { max-height: 330px; overflow-y: auto; margin-right: -6px; padding-right: 6px;
  mask-image: linear-gradient(180deg, #000 88%, transparent); }
.ro-row { display: grid; grid-template-columns: minmax(0, 1fr) 72px 54px 62px; align-items: center; gap: 8px;
  width: 100%; border: 0; background: none; padding: 0 4px; line-height: 34px; font-size: 12.5px; text-align: left;
  cursor: pointer; border-radius: 4px; color: inherit; font-family: inherit; }
.ro-row:hover { background: rgba(255, 255, 255, 0.04); }
.ro-row.on { background: var(--blue-bg); }
.ro-row .nm { color: var(--fg-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.35; padding: 5px 0; }
.ro-row .nm small { display: block; font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.ro-row .d, .ro-row .h { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ro-row .h.und { color: var(--amber); font-size: 11.5px; }
.ro-gap { margin-top: 18px; height: 44px; border: 0; border-radius: 8px; background: #4aa3ea; color: #fff;
  font-size: 15px; font-weight: 500; cursor: pointer; }
.ro-gap:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
