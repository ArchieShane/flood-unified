<script setup>
/* New results：本周完成、还没人打开过的任务。结果指标没回写的卡片只写「尚未回写」，不编数。 */
import { t } from '../../i18n'
import { analysisLabel, avatarClass, fmtInt, fmtWhen, stormLabel } from '../../utils/sim'

defineProps({ items: { type: Array, default: () => [] } })
defineEmits(['open'])

const isValidation = r => r.result?.validation_hit_rate_pct != null
</script>

<template>
  <section class="sim-card sim-panel">
    <div class="head"><span class="sim-cap">{{ t('sim.newList.title') }}</span></div>
    <div v-if="items.length" class="sim-results">
      <article v-for="r in items" :key="r.id" class="sim-result">
        <div class="t">
          <template v-if="isValidation(r)">{{ r.result.validation_event || stormLabel(r) }}</template>
          <template v-else>
            {{ stormLabel(r) }}<span class="sep">｜</span>{{ t(`sim.lineShort.${r.defence_line_key}`) }}
          </template>
        </div>
        <div class="meta">
          {{ fmtWhen(r.started_at) }}
          <span class="sim-avatar" :class="avatarClass(r.created_by)">{{ r.created_by }}</span>
        </div>
        <div class="v">
          <template v-if="isValidation(r)">{{ t('sim.newList.hitRate', { n: fmtInt(r.result.validation_hit_rate_pct) }) }}</template>
          <template v-else-if="r.result?.still_pond_count != null">{{ t('sim.newList.stillPond', { n: fmtInt(r.result.still_pond_count) }) }}</template>
          <template v-else>{{ t('sim.newList.noResult') }}</template>
        </div>
        <button class="sim-link" style="justify-self: end" @click="$emit('open', r)">{{ t('sim.history.open') }}</button>
        <div class="sub">
          <template v-if="r.baseline_still_pond != null">
            {{ t(r.defence_line_key === 'line2_current' ? 'sim.newList.fromLine1' : 'sim.newList.fromLine2',
                 { n: fmtInt(r.baseline_still_pond) }) }}
          </template>
        </div>
        <div class="tags">
          <span v-for="a in r.analyses" :key="a.kind + a.gap_hours" class="sim-tag" :class="a.kind">{{ analysisLabel(a) }}</span>
        </div>
      </article>
    </div>
    <p v-else class="sim-empty">{{ t('sim.newList.empty') }}</p>
  </section>
</template>
