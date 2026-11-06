<script setup>
/* 详情页右上角「Compare」：同一场雨在三道防线上的结果并排（列 = run.line_runs）。
   多态任务（run.states，一个任务涵盖几种工况，如 Mussafah 四态）列 = 各态，同一任务按 ?state= 各取一次，Δ 相对第一态。
   本任务那一列直接吃 ResultMap 已取回的 meta / ponding，别的列各取一次 /layers 与 /layers/ponding。
   口径同 RunOverview：退水时间只在已退干的片区里取中位，未退干单列；某道没排任务或没交付图层的
   那一列如实写「未排」「无成果」，不拿别的雨或别的防线顶替。Δ 一律相对第一道。
   多态任务不放退水时间几行（Mussafah 的溢水点都是封闭洼地的死水，四态全落「未退干」，说明不了方案），
   表下接「方案效果」（StateEffect）：同一时刻各态比第一态少多少水、少几个溢水点。 */
import { computed, ref, watch } from 'vue'

import { fetchSimulationLayers, fetchSimulationPonding } from '../../api'
import { locale, t } from '../../i18n'
import StateEffect from './StateEffect.vue'
import { fmtInt, fmtKm2, stateCode } from '../../utils/sim'

const props = defineProps({
  run: { type: Object, required: true },
  state: { type: String, default: null },     // 多态任务当前显示的态
  meta: { type: Object, default: null },
  ponding: { type: Object, default: null },
})
const emit = defineEmits(['open', 'state', 'locate'])

const byState = computed(() => !!props.run.states)
/** 列：{ key, tab, title, run_id, st, has_layers, self, defence_line_key } */
const lanes = computed(() => byState.value
  ? props.run.states.map(s => ({
      key: s.key, tab: stateCode(s.key), title: stateCode(s.label), run_id: props.run.id, st: s.key, has_layers: true,
      self: s.key === props.state, defence_line_key: s.defence_line_key,
    }))
  : (props.run.line_runs || []).map(lr => ({
      ...lr, key: lr.defence_line_key, tab: t(`sim.detail.lineTab.${lr.defence_line_key}`), title: lr.run_id ? `#${lr.run_id}` : '',
      st: null, self: lr.run_id === props.run.id,
    })))
const ck = c => `${c.run_id}|${c.st || ''}`

/** `run_id|态` → { meta, ponding, error } */
const cache = ref({})

async function loadOthers() {
  for (const c of lanes.value) {
    if (!c.run_id || !c.has_layers || c.self || cache.value[ck(c)]) continue
    cache.value[ck(c)] = { loading: true }
    try {
      const [meta, ponding] = await Promise.all([fetchSimulationLayers(c.run_id, c.st), fetchSimulationPonding(c.run_id, c.st)])
      cache.value[ck(c)] = { meta, ponding }
    } catch (e) {
      cache.value[ck(c)] = { error: e.message }
    }
  }
}
watch([() => props.run.id, () => props.state], loadOthers, { immediate: true })
watch([() => props.run.id, locale], () => { cache.value = {}; loadOthers() })

const cols = computed(() => lanes.value.map(lr => {
  const self = lr.self
  const d = self ? { meta: props.meta, ponding: props.ponding, loading: !props.ponding } : cache.value[ck(lr)] || {}
  let state = 'ok'
  if (!lr.run_id) state = 'none'
  else if (!lr.has_layers) state = 'pending'
  else if (d.error) state = 'error'
  else if (d.loading || !d.ponding) state = 'loading'
  const r = d.meta?.results || {}
  const rec = d.ponding?.recession
  const area = r.peak_inundated_area_m2 ?? r.inundated_area_ge_0_01m2
  return {
    ...lr, self, state, error: d.error,
    v: state !== 'ok' ? {} : {
      ponds: d.ponding.clusters.length,
      maxDepth: r.maximum_depth_m == null ? null : Number(r.maximum_depth_m),
      maxArea: area == null ? null : area / 1e6,
      recession: rec?.median_h ?? null,
      b0: rec?.counts[0], b1: rec?.counts[1], b2: rec?.counts[2], b3: rec?.counts[3],
      undrained: rec?.undrained,
    },
  }
}))

const ROWS = computed(() => {
  const bins = t('sim.panel.bins')
  const rows = [
    { k: 'ponds', label: t('sim.compare.ponds'), fmt: fmtInt, lowerBetter: true },
    { k: 'maxDepth', label: t('sim.panel.maxDepth'), unit: 'm', fmt: x => x.toFixed(2), lowerBetter: true },
    { k: 'maxArea', label: t(props.meta?.results?.peak_inundated_area_m2 != null ? 'sim.panel.maxAreaPeak' : 'sim.panel.maxArea'), unit: 'km²', fmt: fmtKm2, lowerBetter: true },
    { k: 'recession', label: t('sim.panel.recession'), unit: 'h', fmt: x => x.toFixed(1), lowerBetter: true },
    { k: 'b0', label: bins[0], fmt: fmtInt, group: t('sim.panel.recessTime') },
    { k: 'b1', label: bins[1], fmt: fmtInt },
    { k: 'b2', label: bins[2], fmt: fmtInt },
    { k: 'b3', label: bins[3], fmt: fmtInt, lowerBetter: true },
    { k: 'undrained', label: t('sim.panel.undrained'), fmt: fmtInt, lowerBetter: true },
  ]
  return byState.value ? rows.slice(0, 3) : rows
})

const base = computed(() => (byState.value ? cols.value[0] : cols.value.find(c => c.defence_line_key === 'line1')))
const openCol = c => (byState.value ? emit('state', c.st) : emit('open', c.run_id))
function delta(row, col) {
  if (col === base.value || col.state !== 'ok' || base.value?.state !== 'ok') return null
  const a = base.value.v[row.k]
  const b = col.v[row.k]
  if (a == null || b == null || a === b) return null
  const d = b - a
  const mag = row.fmt(Math.abs(d))
  if (!/[1-9]/.test(mag)) return null          // 按显示精度四舍五入后是 0（0.9935 对 0.9938），不写「−0.00」
  const txt = (d > 0 ? '+' : '−') + mag
  return { txt, cls: row.lowerBetter ? (d < 0 ? 'good' : 'bad') : '' }
}
const stateText = s => t(`sim.compare.state.${s}`)
</script>

<template>
  <aside class="sim-card ro rc">
    <h2 class="ro-title">{{ byState ? t('sim.compare.titleStates', { n: cols.length }) : t('sim.compare.title') }}</h2>
    <p class="rc-sub">{{ byState ? t('sim.compare.subStates', { base: cols[0]?.tab }) : t('sim.compare.sub') }}</p>

    <table class="rc-tbl">
      <thead>
        <tr>
          <th />
          <th v-for="c in cols" :key="c.key" :class="{ self: c.self }">
            <button v-if="c.run_id && !c.self" class="rc-col" :title="c.title" @click="openCol(c)">{{ c.tab }}</button>
            <span v-else :title="c.title">{{ c.tab }}</span>
            <small>{{ byState ? t(`sim.detail.lineTab.${c.defence_line_key}`) : c.title }}</small>
          </th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in ROWS" :key="row.k">
          <tr v-if="row.group" class="grp"><td :colspan="cols.length + 1">{{ row.group }}</td></tr>
          <tr>
            <td class="lb">{{ row.label }}<small v-if="row.unit"> {{ row.unit }}</small></td>
            <td v-for="c in cols" :key="c.key" :class="{ self: c.self }">
              <template v-if="c.state === 'ok'">
                <span class="v">{{ c.v[row.k] == null ? '—' : row.fmt(c.v[row.k]) }}</span>
                <span v-if="delta(row, c)" class="d" :class="delta(row, c).cls">{{ delta(row, c).txt }}</span>
              </template>
              <span v-else-if="row.k === 'ponds'" class="st" :class="c.state" :title="c.error || ''">{{ stateText(c.state) }}</span>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <p class="ro-note">{{ byState ? t('sim.compare.noteStates') : t('sim.compare.note') }}</p>
    <StateEffect v-if="byState" :run-id="run.id" @locate="emit('locate', $event)" />
  </aside>
</template>

<style scoped>
/* 外框与 RunOverview 同款（那边是 scoped，这里照抄三条） */
.ro { padding: 22px 22px 18px; display: flex; flex-direction: column; background: linear-gradient(180deg, #1f2124, #17181b); }
.ro-title { font-size: 20px; font-weight: 500; margin: 0; padding-bottom: 14px; border-bottom: 1px solid var(--line-2); }
.ro-note { color: var(--muted); font-size: 11.5px; margin: 12px 0 0; line-height: 1.5; }
.rc-sub { color: var(--muted); font-size: 12px; margin: 10px 0 4px; line-height: 1.5; }
.rc-tbl { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; table-layout: fixed; }
.rc-tbl th { font-weight: 600; text-align: right; padding: 8px 4px; vertical-align: bottom; font-size: 12.5px; }
.rc-tbl th:first-child { width: 34%; }
.rc-tbl th small { display: block; color: var(--faint); font-weight: 400; font-size: 11px; margin-top: 2px; }
.rc-tbl th.self { color: var(--blue-lt); }
.rc-col { border: 0; background: none; padding: 0; color: inherit; font: inherit; cursor: pointer; text-align: right; }
.rc-col:hover { color: var(--blue-lt); text-decoration: underline; }
.rc-tbl td { padding: 7px 4px; text-align: right; border-top: 1px solid var(--line-2); vertical-align: top;
  font-variant-numeric: tabular-nums; }
.rc-tbl td.self { background: rgba(71, 134, 201, 0.08); }
.rc-tbl td.lb { text-align: left; color: var(--fg-2); }
.rc-tbl td.lb small { color: var(--faint); }
.rc-tbl tr.grp td { border-top: 0; padding: 14px 4px 4px; text-align: left; font-weight: 600; font-size: 12.5px; }
.rc-tbl .v { display: block; }
.rc-tbl .d { display: block; font-size: 11px; color: var(--muted); }
.rc-tbl .d.good { color: var(--green); }
.rc-tbl .d.bad { color: var(--red); }
.rc-tbl .st { display: block; color: var(--faint); font-size: 12px; white-space: normal; }
.rc-tbl .st.error { color: var(--red); }
.rc-tbl .st.loading { color: var(--muted); }
</style>
