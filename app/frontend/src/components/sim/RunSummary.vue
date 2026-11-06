<script setup>
/* 任务参数的三块汇总（降雨 / 范围与边界 / 防线）。向导第 4 步与任务详情页共用；
   editable 时每块右上角带「Edit ›」，点了回到对应的那一步。 */
import { computed } from 'vue'

import AppIcon from '../AppIcon.vue'
import { t } from '../../i18n'
import { fmtInt, stormLabel } from '../../utils/sim'

const props = defineProps({
  run: { type: Object, required: true },
  /** /api/simulations/options 的 defence_lines（带资源目录） */
  lines: { type: Array, required: true },
  /** 模型起算时刻（UTC ISO） */
  startUtc: { type: String, default: null },
  isDesignBasis: { type: Boolean, default: false },
  isCustom: { type: Boolean, default: false },
  editable: { type: Boolean, default: false },
})
defineEmits(['edit'])

const num = x => (x === null || x === undefined ? '—' : String(Number(x)))
const r = computed(() => props.run)
const picked = computed(() => new Set(r.value.resource_ids || []))
const lineRows = computed(() =>
  props.lines.map(ln => ({
    key: ln.key,
    items: ln.resources.filter(x => picked.value.has(x.id)).map(x => `${x.label} ${fmtInt(x.quantity)}`),
  })),
)
const startText = computed(() => {
  if (!props.startUtc) return '—'
  const s = new Date(props.startUtc).toISOString()
  return `${s.slice(0, 10).replaceAll('-', '/')} ${s.slice(11, 16)} UTC`
})
const afterText = computed(() =>
  r.value.after_rain_mode === 'fixed'
    ? `${t('sim.extent.fixed')} ｜ ${t('sim.extent.forH', { h: num(r.value.after_rain_max_h) })}`
    : `${t('sim.extent.untilDrained')} ｜ ${t('sim.extent.maxH', { h: num(r.value.after_rain_max_h) })}`,
)
</script>

<template>
  <div class="wiz-block">
    <div class="bh">
      <span class="ok"><AppIcon name="check" :size="13" /></span>{{ t('sim.rain.title') }}
      <button v-if="editable" class="sim-link" @click="$emit('edit', 0)">{{ t('sim.wizard.edit') }}</button>
    </div>
    <div class="wiz-kv">
      <span class="k">{{ t('sim.confirm.source') }}</span><span class="v">{{ t(`sim.rain.sources.${r.rain_source}`) }}</span>
      <template v-if="r.rain_source === 'observed_replay'">
        <span class="k">{{ t('sim.confirm.replayEvent') }}</span><span class="v hl">{{ r.replay_event_ref }}</span>
      </template>
      <template v-else>
        <span class="k">{{ t('sim.confirm.scenario') }}</span>
        <span class="v hl">
          {{ isCustom ? t('sim.rain.customStorm') : stormLabel(r) }}
          <template v-if="isDesignBasis"><span class="sep">｜</span>{{ t('sim.rain.designBasis') }}</template>
        </span>
        <span class="k">{{ t('sim.confirm.depthDuration') }}</span>
        <span class="v">{{ num(r.depth_mm) }} mm<span class="sep">｜</span>{{ num(r.duration_h) }} h</span>
        <span class="k">{{ t('sim.confirm.temporal') }}</span><span class="v">{{ r.temporal_pattern || '—' }}</span>
        <span class="k">{{ t('sim.confirm.peak') }}</span><span class="v">{{ r.peak_position_pct == null ? '—' : num(r.peak_position_pct) + '%' }}</span>
      </template>
      <span class="k">{{ t('sim.confirm.spatial') }}</span><span class="v">{{ r.spatial_distribution || '—' }}</span>
    </div>
  </div>

  <div class="wiz-block">
    <div class="bh">
      <span class="ok"><AppIcon name="check" :size="13" /></span>{{ t('sim.extent.title') }}
      <button v-if="editable" class="sim-link" @click="$emit('edit', 1)">{{ t('sim.wizard.edit') }}</button>
    </div>
    <div class="wiz-kv">
      <span class="k">{{ t('sim.confirm.extent') }}</span>
      <span class="v">{{ r.extent }}<span class="sep">｜</span>{{ t('sim.extent.grid', { m: num(r.grid_m) }) }}</span>
      <span class="k">{{ t('sim.confirm.terrain') }}</span><span class="v">{{ r.terrain || '—' }}</span>
      <span class="k">{{ t('sim.confirm.tideBoundary') }}</span>
      <span class="v">
        {{ r.tide_label || '—' }}
        <template v-if="r.gate_close_above_m != null">
          <span class="sep">｜</span>{{ t('sim.extent.gatesAbove', { m: num(r.gate_close_above_m) }) }}
        </template>
      </span>
      <span class="k">{{ t('sim.confirm.modelStart') }}</span><span class="v">{{ startText }}</span>
      <span class="k">{{ t('sim.confirm.afterRain') }}</span><span class="v">{{ r.after_rain_mode ? afterText : '—' }}</span>
      <span class="k">{{ t('sim.confirm.baseflow', { mm: r.baseflow_min_dia_mm ?? '—' }) }}</span>
      <span class="v">{{ r.baseflow_pct != null ? num(r.baseflow_pct) + '%' : '—' }}</span>
    </div>
  </div>

  <div class="wiz-block hl">
    <div class="bh">
      <span class="ok"><AppIcon name="check" :size="13" /></span>{{ t('sim.lines.title') }}
      <button v-if="editable" class="sim-link" @click="$emit('edit', 2)">{{ t('sim.wizard.edit') }}</button>
    </div>
    <div class="wiz-kv">
      <template v-for="row in lineRows" :key="row.key">
        <span class="k">{{ t(`sim.line.${row.key}`) }}</span>
        <span v-if="row.items.length" class="v">{{ row.items.join(' / ') }}</span>
        <!-- 一项资源都没记的（历史任务）是「没记录」，不是「没纳入」 -->
        <span v-else class="v none">{{ picked.size || editable ? t('sim.confirm.notIncluded') : t('sim.confirm.notRecorded') }}</span>
      </template>
    </div>
  </div>
</template>
