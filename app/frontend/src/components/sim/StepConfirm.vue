<script setup>
/* 向导 ④ 确认：参数汇总 + 预估条（任务数 / 约几小时 / 何时就绪）。
   预估由后端按同范围、同网格的历史耗时中位数算；没有同口径历史就显示「暂无估算」。 */
import { computed, onMounted } from 'vue'

import RunSummary from './RunSummary.vue'
import { t } from '../../i18n'
import { useSimWizardStore } from '../../stores/simWizard'
import { fmtWhen } from '../../utils/sim'

const W = useSimWizardStore()
const e = computed(() => W.estimate)
const startUtc = computed(() => (W.startDate ? `${W.startDate}T${W.startTime || '00:00'}:00Z` : null))

onMounted(() => W.refreshEstimate().catch(() => (W.estimate = null)))
</script>

<template>
  <section class="sim-card wiz-confirm">
    <h2 class="wiz-h2" style="margin: 0">{{ t('sim.confirm.title') }}</h2>
    <RunSummary
      :run="W.form"
      :lines="W.lines"
      :start-utc="startUtc"
      :is-design-basis="!!W.preset?.is_design_basis && !W.custom"
      :is-custom="W.custom"
      editable
      @edit="W.step = $event"
    />
    <div class="wiz-est">
      <div>
        <div class="v">{{ e?.est_run_count ?? W.runCount }}</div>
        <div class="l">{{ t('sim.confirm.simulations') }}</div>
      </div>
      <div>
        <template v-if="e?.est_runtime_h != null">
          <div class="v">{{ t('sim.confirm.about', { h: Math.round(e.est_runtime_h) }) }}</div>
          <div class="l">{{ t('sim.confirm.onTarget', { t: e.compute_target }) }}</div>
        </template>
        <template v-else>
          <div class="v none">{{ t('sim.confirm.noEstimate') }}</div>
          <div class="l">{{ t('sim.confirm.noEstimateHint') }}</div>
        </template>
      </div>
      <div>
        <div class="v" :class="{ none: !e?.est_ready_at }">{{ e?.est_ready_at ? fmtWhen(e.est_ready_at) : '—' }}</div>
        <div class="l">{{ t('sim.confirm.ready') }}</div>
      </div>
    </div>
    <slot name="foot" />
  </section>
</template>
