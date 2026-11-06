<script setup>
/* 「成果数据位置」：这次推演的成果在 NAS 上的位置（outputs.json，engine 写的），以及同一区域之前的推演。
   后者是为了以后在同一个汇水区上做 L2 sugg / L3：直接找到它之前跑过的 L1 及其成果。没有 outputs.json 的任务整块不出。 */
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchSimulationOutputs } from '../../api'
import { t } from '../../i18n'

const props = defineProps({ runId: { type: Number, required: true } })

const data = ref(null)
const copied = ref('')
let seq = 0
watch(() => props.runId, async id => {
  const my = ++seq
  data.value = null
  try {
    const d = await fetchSimulationOutputs(id)
    if (my === seq) data.value = d
  } catch { /* 没有 outputs.json：不出这个模块 */ }
}, { immediate: true })

const rows = computed(() => {
  const p = data.value?.paths
  if (!p) return []
  return [['deliveries', p.deliveries], ['flood2d', p.flood2d], ['shp_dat', p.shp_dat], ['work', p.work_dir_on_host]].filter(r => r[1])
})
const files = computed(() => {
  const f = data.value?.files
  return f ? [['inp', f.delivery_1d_inp], ['npz', f.delivery_2d_npz], ['pilot', f.pilot], ['params', f.params]].filter(r => r[1]) : []
})
async function copy(text) {
  try { await navigator.clipboard.writeText(text); copied.value = text; setTimeout(() => (copied.value = ''), 1500) } catch { /* 浏览器不让复制：忽略 */ }
}
</script>

<template>
  <section v-if="data" class="op sim-card">
    <h3>{{ t('sim.outputs.title') }}</h3>
    <p class="op-sub">{{ t('sim.outputs.sub') }}</p>
    <table class="op-tbl">
      <thead><tr><th>{{ t('sim.outputs.where') }}</th><th /><th /></tr></thead>
      <tbody>
        <tr v-for="[k, v] in rows" :key="k">
          <td>{{ t(`sim.outputs.${k}`) }}</td>
          <td class="path">{{ v }}</td>
          <td><button class="op-btn" @click="copy(v)">{{ copied === v ? t('sim.outputs.copied') : t('sim.outputs.copy') }}</button></td>
        </tr>
        <tr v-if="data.host"><td>{{ t('sim.outputs.host') }}</td><td class="path">{{ data.host }}</td><td /></tr>
      </tbody>
    </table>
    <details class="op-g">
      <summary>{{ t('sim.outputs.files') }}</summary>
      <table class="op-tbl">
        <tbody>
          <tr v-for="[k, v] in files" :key="k"><td>{{ t(`sim.outputs.${k}`) }}</td><td class="path">{{ v }}</td></tr>
          <tr v-if="data.continue_from"><td>{{ t('sim.outputs.cont') }}</td><td>{{ data.continue_from.how }}</td></tr>
        </tbody>
      </table>
    </details>
    <h4>{{ t('sim.outputs.same') }}<small> · {{ data.region }}</small></h4>
    <p v-if="!data.same_region_runs?.length" class="op-sub">{{ t('sim.outputs.sameNone') }}</p>
    <table v-else class="op-tbl">
      <thead><tr><th>{{ t('sim.outputs.colRun') }}</th><th>{{ t('sim.outputs.colRain') }}</th><th>{{ t('sim.outputs.colStates') }}</th><th>{{ t('sim.outputs.colPath') }}</th></tr></thead>
      <tbody>
        <tr v-for="r in data.same_region_runs" :key="r.job_id">
          <td><RouterLink v-if="r.run_id" :to="`/simulations/${r.run_id}`">#{{ r.run_id }} · {{ r.job_id }}</RouterLink><span v-else>{{ r.job_id }}</span></td>
          <td>{{ r.rain_mm }} mm / {{ r.dur_h }} h</td>
          <td>{{ (r.states || []).join(' / ') }}</td>
          <td class="path">{{ r.paths?.deliveries }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.op { margin-top: 12px; padding: 16px; }
.op h3 { margin: 0; font-size: 15px; }
.op h4 { margin: 14px 0 4px; font-size: 13px; }
.op h4 small { font-weight: 400; opacity: 0.6; }
.op-sub { margin: 8px 0; font-size: 12px; opacity: 0.7; line-height: 1.5; }
.op-tbl { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 12px; }
.op-tbl th { text-align: left; font-weight: 500; opacity: 0.6; padding: 4px 8px; }
.op-tbl td { padding: 5px 8px; border-top: 1px solid rgba(255, 255, 255, 0.05); vertical-align: top; }
.op-tbl .path { font-family: ui-monospace, monospace; word-break: break-all; }
.op-btn { background: transparent; color: inherit; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 6px; padding: 1px 8px; cursor: pointer; font-size: 11px; }
.op-g { margin-top: 8px; }
.op-g summary { cursor: pointer; font-size: 12px; opacity: 0.8; }
</style>
