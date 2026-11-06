<script setup>
/* 结果地图下方的易积水片区清单（/layers/ponding，算法见 docs/analysis_ponding）。
   偏离 key 是协议值：后端判定这份成果哪里没按文档口径算，这里逐条译成人话，**必须原样亮出来**，
   不能让降级结果冒充文档口径。点表格一行 → 地图飞到该片区（emit focus）。 */
import { computed, ref } from 'vue'

import { MECHANISM_COLOR } from '../../constants'
import { t } from '../../i18n'
import { fmtInt } from '../../utils/sim'

const props = defineProps({
  data: { type: Object, default: null },
  error: { type: String, default: '' },
  active: { type: Number, default: null },
})
const emit = defineEmits(['focus'])

const PAGE = 20
const showAll = ref(false)
const rows = computed(() => {
  const c = props.data?.clusters || []
  return showAll.value ? c : c.slice(0, PAGE)
})
const mechs = computed(() => {
  const m = new Map()
  for (const c of props.data?.clusters || []) {
    const e = m.get(c.mechanism_key) || { key: c.mechanism_key, label: c.mechanism, n: 0 }
    e.n += 1
    m.set(c.mechanism_key, e)
  }
  return [...m.values()]
})
const fmt = (x, d = 2) => (x == null ? '—' : Number(x).toFixed(d))
const ha = m2 => (m2 == null ? '—' : (m2 / 1e4).toFixed(2))
</script>

<template>
  <div class="pp">
    <div class="pp-head">
      <div class="bh">{{ t('sim.ponding.title') }}</div>
      <span v-if="data" class="pp-sum">
        {{ t('sim.ponding.summary', { cells: fmtInt(data.summary.selected_cells), n: data.summary.cluster_count,
                                      ha: ha(data.summary.area_m2) }) }}
      </span>
      <span v-for="m in mechs" :key="m.key" class="pp-mech">
        <span class="sw" :style="{ background: MECHANISM_COLOR[m.key] || MECHANISM_COLOR.other }" />{{ m.label }} {{ m.n }}
      </span>
    </div>
    <details class="pp-what">
      <summary>{{ t('sim.ponding.what.q') }}</summary>
      <p>{{ t('sim.ponding.what.lead') }}</p>
      <p>{{ t('sim.ponding.what.rule') }}</p>
      <ul>
        <li>{{ t('sim.ponding.what.deep') }}</li>
        <li>{{ t('sim.ponding.what.long') }}</li>
      </ul>
      <p>{{ t('sim.ponding.what.group') }}</p>
      <p>{{ t('sim.ponding.what.why') }}</p>
      <p>{{ t('sim.ponding.what.rank') }}</p>
      <p class="caveat">{{ t('sim.ponding.what.caveat') }}</p>
      <p class="pp-rule">{{ t('sim.ponding.rule') }}</p>
    </details>

    <p v-if="error" class="pp-err">{{ error }}</p>
    <p v-else-if="!data" class="pp-rule">{{ t('sim.ponding.loading') }}</p>

    <div v-if="data?.deviations.length" class="pp-dev">
      <div class="cap">{{ t('sim.ponding.devTitle', { n: data.deviations.length }) }}</div>
      <ul>
        <li v-for="k in data.deviations" :key="k">{{ t(`sim.ponding.dev.${k}`) }}</li>
      </ul>
    </div>
    <p v-else-if="data" class="pp-ok">{{ t('sim.ponding.noDev') }}</p>

    <div v-if="data?.clusters.length" class="pp-table-wrap">
      <table class="pp-table">
        <thead>
          <tr>
            <th>#</th>
            <th>{{ t('sim.ponding.cols.score') }}</th>
            <th>{{ t('sim.ponding.cols.name') }}</th>
            <th>{{ t('sim.ponding.cols.mechanism') }}</th>
            <th class="n">{{ t('sim.ponding.cols.maxDepth') }}</th>
            <th class="n">{{ t('sim.ponding.cols.dur30') }}</th>
            <th class="n">{{ t('sim.ponding.cols.dur15') }}</th>
            <th class="n">{{ t('sim.ponding.cols.endDepth') }}</th>
            <th class="n">{{ t('sim.ponding.cols.drain') }}</th>
            <th class="n">{{ t('sim.ponding.cols.area') }}</th>
            <th class="n">{{ t('sim.ponding.cols.sink') }}</th>
            <th class="n">{{ t('sim.ponding.cols.swmm') }}</th>
            <th>{{ t('sim.ponding.cols.loc') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.rank" :class="{ on: c.rank === active }" @click="emit('focus', c.rank)">
            <td>{{ c.rank }}</td>
            <td>{{ fmt(c.score, 1) }}</td>
            <td class="name" :title="c.name || ''">{{ c.name || '—' }}</td>
            <td><span class="sw" :style="{ background: MECHANISM_COLOR[c.mechanism_key] || MECHANISM_COLOR.other }" />{{ c.mechanism }}</td>
            <td class="n">{{ fmt(c.max_depth_m, 3) }}</td>
            <td class="n">{{ fmt(c.dur_ge_0_30m_h) }}</td>
            <td class="n">{{ fmt(c.dur_ge_0_15m_h) }}</td>
            <td class="n">{{ fmt(c.depth_end_m, 3) }}</td>
            <td class="n">{{ fmt(c.drain_time_h) }}</td>
            <td class="n">{{ ha(c.area_m2) }}</td>
            <td class="n">{{ fmt(c.sink_depth_m) }}</td>
            <td class="n">{{ fmtInt(Math.round(c.swmm_inflow_m3)) }}</td>
            <td class="loc">{{ c.lat }}, {{ c.lon }}</td>
          </tr>
        </tbody>
      </table>
      <button v-if="data.clusters.length > PAGE" class="sim-btn pp-more" @click="showAll = !showAll">
        {{ showAll ? t('sim.ponding.less') : t('sim.ponding.more', { n: data.clusters.length }) }}
      </button>
    </div>
    <p v-else-if="data" class="pp-rule">{{ t('sim.ponding.empty') }}</p>
  </div>
</template>

<style scoped>
.pp { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 14px; }
.pp-head { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.pp-head .bh { font-weight: 600; font-size: 15px; }
.pp-sum { color: var(--fg-2); font-size: 13px; }
.pp-mech { font-size: 12px; color: var(--fg-2); display: inline-flex; align-items: center; gap: 5px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.pp-mech .sw { margin-right: 0; }
.pp-rule { color: var(--muted); font-size: 12px; margin: 6px 0 0; }
.pp-what { margin-top: 8px; font-size: 13px; color: var(--fg); }
.pp-what summary { cursor: pointer; color: var(--blue, #6aa8ff); font-size: 12.5px; width: fit-content; }
.pp-what[open] { background: rgba(255, 255, 255, 0.03); border: 1px solid var(--line); border-radius: 6px; padding: 8px 12px 10px; }
.pp-what p { margin: 6px 0 0; line-height: 1.65; }
.pp-what ul { margin: 4px 0 0; padding-left: 20px; line-height: 1.65; }
.pp-what .caveat { color: var(--amber); }
.pp-err { color: var(--red); font-size: 13px; margin: 8px 0 0; }
.pp-ok { color: #8fd79a; font-size: 12px; margin: 8px 0 0; }
.pp-dev { margin-top: 10px; background: var(--amber-bg); border: 1px solid rgba(224, 164, 78, 0.35); border-radius: 6px;
  padding: 8px 12px; font-size: 12.5px; color: var(--fg); }
.pp-dev .cap { color: var(--amber); font-weight: 600; margin-bottom: 4px; }
.pp-dev ul { margin: 0; padding-left: 18px; line-height: 1.6; }
.pp-table-wrap { margin-top: 12px; overflow-x: auto; }
.pp-table { width: 100%; border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.pp-table th { text-align: left; font-weight: 500; color: var(--muted); padding: 6px 8px; border-bottom: 1px solid var(--line-2);
  white-space: nowrap; }
.pp-table td { padding: 6px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
.pp-table .n { text-align: right; }
.pp-table .loc { color: var(--fg-2); }
.pp-table .name { max-width: 280px; overflow: hidden; text-overflow: ellipsis; }
.pp-table tbody tr { cursor: pointer; }
.pp-table tbody tr:hover { background: rgba(255, 255, 255, 0.04); }
.pp-table tbody tr.on { background: var(--blue-bg); }
.pp-more { margin-top: 10px; }
</style>
