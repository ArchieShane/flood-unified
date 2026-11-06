<script setup>
/* 向导 ① 降雨情景：来源三选一（NCM 预报在路线图上，灰掉）→ 3×3 雨型格子 / 自定义暴雨 → 所选情景的设置。 */
import { computed } from 'vue'

import AppIcon from '../AppIcon.vue'
import { t } from '../../i18n'
import { useSimWizardStore } from '../../stores/simWizard'
import { stormLabel } from '../../utils/sim'

const W = useSimWizardStore()
const f = computed(() => W.form)
const o = computed(() => W.options)
const num = x => String(Number(x))
const cell = (d, h) => o.value.storm_presets.find(p => Number(p.depth_mm) === Number(d) && Number(p.duration_h) === Number(h))
const scenarioName = computed(() => (W.custom ? t('sim.rain.customStorm') : stormLabel(f.value)))
</script>

<template>
  <section>
    <h2 class="wiz-h2">{{ t('sim.rain.title') }}</h2>
    <div class="wiz-seg">
      <button
        v-for="s in o.rain_sources"
        :key="s.key"
        :class="{ on: f.rain_source === s.key }"
        :disabled="!s.available"
        @click="W.setSource(s.key)"
      >
        {{ t(`sim.rain.sources.${s.key}`) }}<span v-if="!s.available" class="road">{{ t('sim.rain.roadmap') }}</span>
      </button>
    </div>

    <template v-if="f.rain_source === 'design_storm'">
      <div class="wiz-grid">
        <span />
        <span v-for="h in o.storm_durations_h" :key="h" class="ch">{{ num(h) }} h</span>
        <template v-for="d in o.storm_depths_mm" :key="d">
          <span class="rh">{{ num(d) }} mm</span>
          <button
            v-for="h in o.storm_durations_h"
            :key="h"
            class="wiz-cell"
            :class="{ on: !W.custom && f.storm_preset_key === cell(d, h)?.key }"
            @click="W.pickPreset(cell(d, h).key)"
          >
            {{ t('sim.storm', { d: num(d), h: num(h) }) }}
            <span v-if="cell(d, h)?.is_design_basis" class="sim-tag">{{ t('sim.rain.designBasis') }}</span>
          </button>
        </template>
      </div>
      <button class="wiz-custom" :class="{ on: W.custom }" @click="W.pickCustom()">
        <AppIcon name="plusCircle" :size="18" />{{ t('sim.rain.customStorm') }}
      </button>
    </template>

    <div class="wiz-settings">
      <div class="ttl">
        {{ t('sim.rain.settingsFor') }}
        <b v-if="f.rain_source === 'design_storm'">{{ scenarioName }}</b>
        <b v-else>{{ f.replay_event_ref || t(`sim.rain.sources.${f.rain_source}`) }}</b>
      </div>
      <div class="wiz-form">
        <div v-if="f.rain_source === 'observed_replay'" class="wiz-field" style="grid-column: 1 / -1">
          <label>{{ t('sim.rain.replayEvent') }}</label>
          <input v-model.trim="f.replay_event_ref" class="wiz-input" :placeholder="t('sim.rain.replayPh')" />
        </div>
        <template v-else>
          <div class="wiz-field">
            <label>{{ t('sim.rain.temporalPattern') }}</label>
            <select v-model="f.temporal_pattern" class="wiz-input">
              <option v-for="c in o.choices.temporal_pattern" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
          <div class="wiz-field">
            <label>{{ t('sim.rain.peakPosition') }}</label>
            <input v-model.number="f.peak_position_pct" class="wiz-input" type="number" min="0" max="100" />
          </div>
        </template>
        <div class="wiz-field">
          <label>{{ t('sim.rain.spatial') }}</label>
          <select v-model="f.spatial_distribution" class="wiz-input">
            <option v-for="c in o.choices.spatial_distribution" :key="c" :value="c">{{ c }}</option>
          </select>
        </div>
        <div v-if="f.rain_source === 'design_storm'" class="wiz-field">
          <label>{{ t('sim.rain.depthDuration') }}</label>
          <div v-if="W.custom" class="pair">
            <input v-model.number="f.depth_mm" class="wiz-input" type="number" min="0" step="0.1" :placeholder="t('sim.rain.depth')" />
            <input v-model.number="f.duration_h" class="wiz-input" type="number" min="0" step="0.25" :placeholder="t('sim.rain.duration')" />
          </div>
          <input v-else class="wiz-input" readonly :value="`${num(f.depth_mm)} mm ｜ ${num(f.duration_h)} h`" />
        </div>
      </div>
    </div>
  </section>
</template>
