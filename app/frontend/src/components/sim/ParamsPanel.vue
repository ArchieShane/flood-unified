<script setup>
/* 「推演参数」：本次推演用到的全部参数（成果目录根上的 params.json，engine 的 tools/run_params.py 写的）。
   分组折叠，每项带 来源 / 出处 / 待核对 标记，顶上一条「规则 vs 本次运行」的一致性核对。没有 params.json 的任务整块不出。 */
import { computed, ref, watch } from 'vue'

import { fetchSimulationParams } from '../../api'
import { locale, t } from '../../i18n'

const props = defineProps({ runId: { type: Number, required: true } })

const data = ref(null)
const q = ref('')
const showCheck = ref(false)
let seq = 0
watch(() => props.runId, async id => {
  const my = ++seq
  data.value = null
  try {
    const d = await fetchSimulationParams(id)
    if (my === seq) data.value = d
  } catch { /* 没有 params.json：不出这个模块 */ }
}, { immediate: true })

const en = computed(() => locale.value === 'en')
const name = o => (en.value ? o.en : o.zh) || o.zh
const fmt = v => {
  if (v === true) return t('sim.params.yes')
  if (v === false) return t('sim.params.no')
  if (Array.isArray(v)) return v.map(fmt).join('\n')
  if (v && typeof v === 'object') return Object.entries(v).map(([k, x]) => `${k}: ${fmt(x)}`).join('；')
  return v == null ? '—' : String(v)
}
const match = (it, s) => !s || [it.zh, it.en, it.key, fmt(it.value), it.source, it.ref].some(x => String(x ?? '').toLowerCase().includes(s))
const groups = computed(() => {
  const s = q.value.trim().toLowerCase()
  return (data.value?.groups || []).map(g => ({ ...g, shown: g.items.filter(i => match(i, s)) })).filter(g => g.shown.length)
})
const ck = computed(() => data.value?.checks)
const bad = computed(() => (ck.value?.items || []).filter(c => !c.ok))
</script>

<template>
  <section v-if="data" class="pp sim-card">
    <header>
      <h3>{{ t('sim.params.title') }}</h3>
      <span v-if="ck?.n" class="pp-ck" :class="{ bad: ck.n_mismatch }" role="button" tabindex="0" @click="showCheck = !showCheck" @keydown.enter="showCheck = !showCheck">
        {{ ck.n_mismatch ? t('sim.params.mismatch', { m: ck.n_mismatch, n: ck.n }) : t('sim.params.allOk', { n: ck.n }) }}
      </span>
    </header>
    <p class="pp-sub">{{ t('sim.params.sub', { series: data.rules.series, version: data.rules.version }) }}</p>

    <table v-if="showCheck && ck?.items?.length" class="pp-tbl pp-check">
      <caption>{{ t('sim.params.checkTitle') }}</caption>
      <thead><tr><th>{{ t('sim.params.colName') }}</th><th>{{ t('sim.params.colRule') }}</th><th>{{ t('sim.params.colActual') }}</th></tr></thead>
      <tbody>
        <tr v-for="(c, i) in ck.items" :key="i" :class="{ bad: !c.ok }">
          <td>{{ name(c) }}</td><td>{{ fmt(c.rule) }}</td><td>{{ fmt(c.actual) }}</td>
        </tr>
      </tbody>
    </table>

    <input v-model="q" class="pp-q" type="search" :placeholder="t('sim.params.search')">
    <p v-if="!groups.length" class="pp-sub">{{ t('sim.params.none') }}</p>
    <details v-for="(g, gi) in groups" :key="g.key" class="pp-g" :open="!!q || gi === 0">
      <summary>{{ name(g) }} <small>{{ t('sim.params.items', { n: g.shown.length }) }}</small></summary>
      <table class="pp-tbl">
        <thead><tr><th>{{ t('sim.params.colName') }}</th><th>{{ t('sim.params.colValue') }}</th><th>{{ t('sim.params.colSource') }}</th><th>{{ t('sim.params.colRef') }}</th></tr></thead>
        <tbody>
          <tr v-for="it in g.shown" :key="it.key">
            <td>{{ name(it) }}</td>
            <td class="v">{{ fmt(it.value) }}<small v-if="it.unit"> {{ it.unit }}</small></td>
            <td>{{ it.source || '—' }}<span v-if="it.status" class="pp-flag">{{ t('sim.params.pending') }}</span></td>
            <td class="ref">{{ it.ref || '' }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </section>
</template>

<style scoped>
.pp { margin-top: 12px; padding: 16px; }
.pp header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.pp h3 { margin: 0; font-size: 15px; }
.pp-ck { font-size: 12px; padding: 2px 10px; border-radius: 10px; background: rgba(60, 180, 110, 0.18); color: #57d38c; cursor: pointer; }
.pp-ck.bad { background: rgba(230, 70, 70, 0.2); color: #ff7a7a; }
.pp-sub { margin: 8px 0; font-size: 12px; opacity: 0.7; line-height: 1.5; }
.pp-q { width: 100%; max-width: 360px; margin: 4px 0 10px; padding: 6px 10px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.15); background: transparent; color: inherit; }
.pp-g { border-top: 1px solid rgba(255, 255, 255, 0.08); padding: 8px 0; }
.pp-g summary { cursor: pointer; font-size: 13px; font-weight: 600; }
.pp-g summary small { font-weight: 400; opacity: 0.6; margin-left: 8px; }
.pp-tbl { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
.pp-tbl th { text-align: left; font-weight: 500; opacity: 0.6; padding: 4px 8px; }
.pp-tbl td { padding: 5px 8px; border-top: 1px solid rgba(255, 255, 255, 0.05); vertical-align: top; }
.pp-tbl .v { white-space: pre-line; word-break: break-word; }
.pp-tbl .ref { opacity: 0.6; }
.pp-tbl small { opacity: 0.6; }
.pp-check caption { text-align: left; font-size: 12px; opacity: 0.7; padding: 6px 8px; }
.pp-check .bad td { color: #ff7a7a; }
.pp-flag { margin-left: 6px; padding: 0 6px; border-radius: 8px; background: rgba(240, 180, 60, 0.2); color: #f0b43c; }
</style>
