<script setup>
/* 同场雨对比：同一雨型下三道防线各自最近一次结果的「仍积水点数」。
   数据来自 summary.same_storm；某道防线还没算的列显示「未算」，不画零高度的柱子冒充 0。 */
import { computed, ref, watch } from 'vue'

import { t } from '../../i18n'
import { fmtInt, stormLabel } from '../../utils/sim'

const props = defineProps({ storms: { type: Array, default: () => [] } })

const picked = ref(null)
watch(() => props.storms, s => {
  if (!s.some(x => x.storm_preset_key === picked.value)) picked.value = s[0]?.storm_preset_key ?? null
}, { immediate: true })

const current = computed(() => props.storms.find(s => s.storm_preset_key === picked.value))
const max = computed(() => Math.max(1, ...(current.value?.lines || []).map(l => l.still_pond_count || 0)))
const CLS = { line1: 'l1', line2_current: 'l2c', line2_planned: 'l2p' }
</script>

<template>
  <section class="sim-card sim-panel">
    <div class="head"><span class="sim-cap">{{ t('sim.sameStorm.title') }}</span></div>
    <template v-if="current">
      <div class="sim-chips" style="margin-bottom: 6px">
        <button
          v-for="s in storms"
          :key="s.storm_preset_key"
          class="sim-chip"
          :class="{ on: s.storm_preset_key === picked }"
          @click="picked = s.storm_preset_key"
        >{{ stormLabel(s) }}</button>
      </div>
      <div class="sim-bars">
        <div v-for="l in current.lines" :key="l.defence_line_key" class="col">
          <template v-if="l.still_pond_count !== null">
            <span class="num" :class="CLS[l.defence_line_key]">{{ fmtInt(l.still_pond_count) }}</span>
            <span
              class="bar"
              :class="CLS[l.defence_line_key]"
              :style="{ height: (l.still_pond_count / max) * 100 * 0.78 + '%' }"
            />
          </template>
          <span v-else class="missing">{{ t('sim.sameStorm.missing') }}</span>
        </div>
      </div>
      <div class="sim-bar-labels">
        <span v-for="l in current.lines" :key="l.defence_line_key">{{ t(`sim.line.${l.defence_line_key}`) }}</span>
      </div>
    </template>
    <p v-else class="sim-empty">{{ t('sim.sameStorm.empty') }}</p>
  </section>
</template>
