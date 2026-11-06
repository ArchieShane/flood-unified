<script setup>
/* 「需要排 → 可以排 → 排完剩余」：每个非基线态一张卡（基线 = 第一态 L1）。
   数据是成果目录根上的 ponding_ge.json（各态水深 ≥ 阈值的积水量，sim_ponding_ge.py 离线算）：
   需要排的总积水量 = 基线态；排完剩余 = 本态；可以排 = 基线 − 本态。没有这个文件就整块不出。 */
import { computed, ref, watch } from 'vue'

import { fetchSimulationPondingGe } from '../../api'
import { t } from '../../i18n'
import { fmtInt, stateCode } from '../../utils/sim'

const props = defineProps({
  runId: { type: Number, required: true },
  states: { type: Array, required: true },   // run.states
})

const ge = ref(null)
let seq = 0
watch(() => props.runId, async id => {
  const my = ++seq
  ge.value = null
  try {
    const d = await fetchSimulationPondingGe(id)
    if (my === seq) ge.value = d
  } catch { /* 没有 ponding_ge.json：不出这个模块 */ }
}, { immediate: true })

const pct = x => `${Number((x * 100).toFixed(1))}%`
const cards = computed(() => {
  const vol = ge.value?.volume_m3
  const base = props.states[0]
  if (!vol || vol[base.key] == null) return []
  const need = vol[base.key]
  return props.states.slice(1).filter(s => vol[s.key] != null).map(s => {
    const left = vol[s.key]
    return { key: s.key, title: stateCode(s.key), need, left, drain: need - left, base: stateCode(base.key) }
  })
})
</script>

<template>
  <div v-if="cards.length" class="bp">
    <section v-for="c in cards" :key="c.key" class="bp-card">
      <h4>{{ c.title }}</h4>
      <div class="bp-row">
        <div><span>{{ t('sim.balance_panel.need') }}</span><b>{{ fmtInt(c.need) }}</b><small>m³</small></div>
        <i>→</i>
        <div class="drain"><span>{{ t('sim.balance_panel.drain') }}</span><b>{{ fmtInt(c.drain) }}</b><small>m³</small></div>
        <i>→</i>
        <div><span>{{ t('sim.balance_panel.left') }}</span><b>{{ fmtInt(c.left) }}</b><small>m³</small></div>
      </div>
      <p>{{ t('sim.balance_panel.fDrain', { drain: fmtInt(c.drain), total: fmtInt(c.need), pct: c.need ? pct(c.drain / c.need) : '—' }) }}</p>
      <p>{{ t('sim.balance_panel.fLeft', { need: fmtInt(c.need), drain: fmtInt(c.drain), left: fmtInt(c.left) }) }}</p>
    </section>
    <p class="bp-note">{{ t('sim.balance_panel.note', { th: ge.threshold_m, base: cards[0].base }) }}</p>
  </div>
</template>

<style scoped>
.bp { display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }
.bp-card { background: var(--sim-card, rgba(255, 255, 255, 0.04)); border-radius: 12px; padding: 16px; }
.bp-card h4 { margin: 0 0 8px; font-size: 14px; }
.bp-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.bp-row > div { display: grid; gap: 2px; }
.bp-row span { font-size: 12px; opacity: 0.7; }
.bp-row b { font-size: 22px; font-weight: 600; }
.bp-row small { font-size: 12px; opacity: 0.7; }
.bp-row i { font-style: normal; opacity: 0.6; font-size: 20px; }
.bp-row .drain b { color: #6aa9ff; }
.bp-card p, .bp-note { margin: 6px 0 0; font-size: 12px; opacity: 0.7; line-height: 1.5; }
</style>
