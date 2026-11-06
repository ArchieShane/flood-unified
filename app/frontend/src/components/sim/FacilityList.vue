<script setup>
/* 「各态新增设备清单」：点 L2 / L2 planned 要看到比上一态多了哪些设备（交付里的设施 GIS，/layers/facilities）。
   每个设备带「建模状态」：已建模 = 进了模型、有作用；未建模（数据缺参数）= 只有位置，不生效。L1 是基线，不列。 */
import { computed, ref, watch } from 'vue'

import { fetchSimulationFacilities } from '../../api'
import { t } from '../../i18n'
import { stateCode } from '../../utils/sim'

const props = defineProps({
  runId: { type: Number, required: true },
  states: { type: Array, required: true },   // run.states
})

const groups = ref([])
let seq = 0
watch(() => [props.runId, props.states], async () => {
  const my = ++seq
  const out = []
  for (const s of props.states.slice(1)) {
    try {
      const d = await fetchSimulationFacilities(props.runId, s.key)
      const rows = Object.values(d?.layers || {}).flatMap(l => l?.features || []).map(f => f.properties).filter(p => p.is_new !== false)
      out.push({ key: s.key, title: stateCode(s.key), rows })
    } catch { /* 这一态没有设施 GIS：不列 */ }
  }
  if (my === seq) groups.value = out
}, { immediate: true })

const isOff = r => String(r['建模状态'] || '').startsWith('未建模')
const summary = g => ({ on: g.rows.filter(r => !isOff(r)).length, off: g.rows.filter(isOff).length })
const shown = computed(() => groups.value.filter(g => g.rows.length))
</script>

<template>
  <section v-if="shown.length" class="fl">
    <h3>{{ t('sim.facList.title') }}</h3>
    <p class="fl-sub">{{ t('sim.facList.sub') }}</p>
    <div v-for="g in shown" :key="g.key" class="fl-card">
      <h4>{{ g.title }}<small>{{ t('sim.facList.sum', { n: g.rows.length, on: summary(g).on, off: summary(g).off }) }}</small></h4>
      <table>
        <thead><tr><th>{{ t('sim.facList.type') }}</th><th>{{ t('sim.facList.id') }}</th><th>{{ t('sim.facList.status') }}</th><th>{{ t('sim.facList.note') }}</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in g.rows" :key="i" :class="{ off: isOff(r) }">
            <td>{{ r['类型'] }}</td><td>{{ r['编号'] }}</td><td>{{ r['建模状态'] || '—' }}</td><td>{{ r['说明'] || '' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.fl h3 { margin: 0 0 4px; font-size: 16px; }
.fl-sub { margin: 0 0 10px; font-size: 12px; opacity: 0.7; line-height: 1.5; }
.fl-card { background: var(--sim-card, rgba(255, 255, 255, 0.04)); border-radius: 12px; padding: 16px; margin-bottom: 12px; overflow-x: auto; }
.fl-card h4 { margin: 0 0 8px; font-size: 14px; }
.fl-card h4 small { margin-left: 10px; font-weight: 400; opacity: 0.7; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.08); vertical-align: top; }
th { opacity: 0.7; font-weight: 500; }
tr.off td { color: #f59e0b; }
</style>
