<script setup>
/* 任务队列甘特：横轴按阿布扎比当地日期分列，竖线是此刻。
   起止时间由后端 summary.queue 排好（同一对 H100 串行）；没有预计耗时的任务只画一小段虚框。 */
import { computed } from 'vue'

import { t } from '../../i18n'
import { fmtDay, localMidnight, stormLabel } from '../../utils/sim'

const props = defineProps({
  queue: { type: Array, default: () => [] },
  now: { type: Number, default: () => Date.now() },
})

const DAY = 86400e3
const ROW = 40
const TOP = 26

const span = computed(() => {
  const ends = props.queue.flatMap(q => [Date.parse(q.start), q.end ? Date.parse(q.end) : Date.parse(q.start) + 3 * 3600e3])
  const from = localMidnight(Math.min(props.now, ...ends))
  // 至少画 4 天，与设计稿一致；排得更远就放宽
  const to = Math.max(from + 4 * DAY, localMidnight(Math.max(...ends, props.now)) + DAY)
  return { from, to }
})
const pct = ms => ((ms - span.value.from) / (span.value.to - span.value.from)) * 100
const days = computed(() => {
  const out = []
  for (let d = span.value.from; d < span.value.to; d += DAY) out.push({ at: d, left: pct(d) })
  return out
})
const jobs = computed(() =>
  props.queue.map((q, i) => {
    const s = Date.parse(q.start)
    const e = q.end ? Date.parse(q.end) : s + 3 * 3600e3
    return {
      ...q,
      open: !q.end,
      style: { left: pct(s) + '%', width: Math.max(pct(e) - pct(s), 6) + '%', top: TOP + i * ROW + 'px' },
    }
  }),
)
const height = computed(() => TOP + Math.max(props.queue.length, 4) * ROW)
</script>

<template>
  <section class="sim-card sim-panel">
    <div class="head"><span class="sim-cap" style="text-transform: none; letter-spacing: 0">{{ t('sim.queue.title') }}</span></div>
    <div v-if="queue.length" class="sim-gantt" :style="{ height: height + 'px' }">
      <span v-for="d in days" :key="d.at" class="day" :style="{ left: d.left + '%' }">{{ fmtDay(d.at) }}</span>
      <span class="now" :style="{ left: pct(now) + '%' }" />
      <span
        v-for="j in jobs"
        :key="j.id"
        class="job"
        :class="{ running: j.status === 'running', open: j.open }"
        :style="j.style"
        :title="j.open ? t('sim.queue.noEstimate') : ''"
      >{{ stormLabel(j) }}<template v-if="j.status === 'running'"> ｜ {{ Math.round(j.progress_pct || 0) }}%</template></span>
    </div>
    <p v-else class="sim-empty">{{ t('sim.queue.empty') }}</p>
  </section>
</template>
