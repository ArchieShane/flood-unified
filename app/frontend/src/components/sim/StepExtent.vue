<script setup>
/* 向导 ② 范围与边界。下拉框的取值来自 /api/simulations/options 的 choices。 */
import { computed } from 'vue'

import { t } from '../../i18n'
import { useSimWizardStore } from '../../stores/simWizard'

const W = useSimWizardStore()
const f = computed(() => W.form)
const o = computed(() => W.options)
const num = x => String(Number(x))

const extentKey = computed({
  get: () => `${f.value.extent}|${Number(f.value.grid_m)}`,
  set: k => {
    const e = o.value.choices.extent.find(x => `${x.extent}|${Number(x.grid_m)}` === k)
    Object.assign(f.value, { extent: e.extent, grid_m: Number(e.grid_m), terrain: e.terrain })
  },
})
const tideKey = computed({
  get: () => f.value.tide_label,
  set: k => {
    const e = o.value.choices.tide.find(x => x.tide_label === k)
    Object.assign(f.value, e)
  },
})
const afterLabel = mode =>
  mode === 'until_drained'
    ? `${t('sim.extent.untilDrained')} ｜ ${t('sim.extent.maxH', { h: num(f.value.after_rain_max_h) })}`
    : `${t('sim.extent.fixed')} ｜ ${t('sim.extent.forH', { h: num(f.value.after_rain_max_h) })}`
</script>

<template>
  <section>
    <h2 class="wiz-h2">{{ t('sim.extent.title') }}</h2>
    <div class="wiz-form">
      <div class="sec sim-cap">{{ t('sim.extent.secExtent') }}</div>
      <div class="wiz-field">
        <label>{{ t('sim.extent.extent') }}</label>
        <select v-model="extentKey" class="wiz-input">
          <option v-for="e in o.choices.extent" :key="e.extent + e.grid_m" :value="`${e.extent}|${Number(e.grid_m)}`">
            {{ e.extent }} ｜ {{ t('sim.extent.grid', { m: num(e.grid_m) }) }}
          </option>
        </select>
      </div>
      <div class="wiz-field">
        <label>{{ t('sim.extent.terrain') }}</label>
        <input class="wiz-input" readonly :value="f.terrain" />
      </div>

      <div class="sec sim-cap">{{ t('sim.extent.secBoundary') }}</div>
      <div class="wiz-field">
        <label>{{ t('sim.extent.tide') }}</label>
        <select v-model="tideKey" class="wiz-input">
          <option v-for="e in o.choices.tide" :key="e.tide_label" :value="e.tide_label">
            {{ e.tide_label }} ｜ {{ t('sim.extent.gatesAbove', { m: num(e.gate_close_above_m) }) }}
          </option>
        </select>
      </div>
      <div class="wiz-field">
        <label>{{ t('sim.extent.modelStart') }}</label>
        <div class="pair">
          <input v-model="W.startDate" class="wiz-input" type="date" />
          <input v-model="W.startTime" class="wiz-input" type="time" step="3600" />
        </div>
      </div>
      <div class="wiz-field">
        <label>{{ t('sim.extent.afterRain') }}</label>
        <select v-model="f.after_rain_mode" class="wiz-input">
          <option v-for="m in o.choices.after_rain_mode" :key="m" :value="m">{{ afterLabel(m) }}</option>
        </select>
      </div>
      <div class="wiz-field">
        <label>{{ t('sim.extent.baseflow', { mm: f.baseflow_min_dia_mm }) }}</label>
        <div class="wiz-with-tag">
          <input v-model.number="f.baseflow_pct" class="wiz-input" type="number" min="0" max="100" />
          <span v-if="f.baseflow_note" class="sim-tag">{{ f.baseflow_note }}</span>
        </div>
      </div>
    </div>
  </section>
</template>
