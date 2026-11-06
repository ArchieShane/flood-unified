<script setup>
/* 向导 ③ 防线：两张卡（第一道 / 第二道）。「三道一起排」开关已从界面删除，一次只建一道防线的任务。
   第二道卡底部的「规划资源」开关决定它代表 line2_current 还是 line2_planned：打开后标题变「第二道防线+」，
   现状资源下面再列规划资源（规划 = 现状 + 规划工程视同建成）。协议上仍是三道，开关只是换 key。
   已包含（前几道）的卡整道全勾、锁定：复选框不禁用（保持蓝勾，与稿一致），点击被拦下。
   勾选规则见 stores/simWizard.js 的模块头。
   规划卡底下的「Review the N items」：库里目前只有分类计数（defence_resource.quantity），
   逐项清单还没入库，点开只如实说明，不编清单。 */
import { computed, ref, watch } from 'vue'

import { t } from '../../i18n'
import { useSimWizardStore } from '../../stores/simWizard'
import { fmtInt } from '../../utils/sim'

const W = useSimWizardStore()
const picked = computed(() => new Set(W.form.resource_ids))
const res = key => W.lines.find(l => l.key === key)?.resources || []
const total = key => res(key).reduce((a, r) => a + Number(r.quantity || 0), 0)

// 规划开关：进来时按已选防线还原
const plannedOn = ref(W.form.defence_line_key === 'line2_planned')
watch(() => W.form.defence_line_key, k => { if (k?.startsWith('line2')) plannedOn.value = k === 'line2_planned' })
const planned = computed(() => plannedOn.value)
function setPlanned(on) {
  plannedOn.value = on
  if (W.form.defence_line_key?.startsWith('line2')) W.selectLine(on ? 'line2_planned' : 'line2_current')
}

const cards = computed(() => {
  const l2 = planned.value ? 'line2_planned' : 'line2_current'
  return [
    { key: 'line1', name: t('sim.lines.name.line1'), sub: t('sim.lines.subtitle.line1'),
      groups: [{ key: 'line1', items: res('line1') }] },
    { key: l2, name: t(`sim.lines.name.${planned.value ? 'line2plus' : 'line2'}`), sub: t(`sim.lines.subtitle.${l2}`),
      groups: [{ key: 'line2_current', items: res('line2_current') },
               ...(planned.value ? [{ key: 'line2_planned', items: res('line2_planned'), cap: t('sim.lines.badge.planned') }] : [])] },
  ]
})

// 第二道+ 的现状资源属于 line2_current 那一组，勾选状态按组各自判（选 2+ 时现状是 included）
const editable = key => ['on', 'open'].includes(W.cardState(key))
const reviewOpen = ref(false)
</script>

<template>
  <section>
    <h2 class="wiz-h2">{{ t('sim.lines.title') }}</h2>
    <div class="wiz-lines">
      <div
        v-for="c in cards"
        :key="c.key"
        class="wiz-line"
        :class="W.cardState(c.key)"
        role="button"
        tabindex="0"
        @click="W.selectLine(c.key)"
        @keydown.enter.self="W.selectLine(c.key)"
      >
        <div class="name">{{ c.name }}</div>
        <div class="sub">{{ c.sub }}</div>
        <template v-for="g in c.groups" :key="g.key">
          <div class="sim-cap" :class="{ 'wiz-subcap': g.cap }">{{ g.cap || t('sim.lines.resourcesIn') }}</div>
          <label v-for="r in g.items" :key="r.id" class="wiz-res" @click.stop>
            <input
              type="checkbox"
              :checked="W.cardState(g.key) === 'included' || picked.has(r.id)"
              :disabled="W.cardState(g.key) === 'off'"
              :class="{ locked: W.cardState(g.key) === 'included' }"
              @click="!editable(g.key) && $event.preventDefault()"
              @change="W.toggleResource(r.id, g.key)"
            />
            {{ r.label }}
            <span class="q">{{ fmtInt(r.quantity) }}</span>
          </label>
          <template v-if="g.key === 'line2_planned' && g.items.length">
            <button class="wiz-review" @click.stop="reviewOpen = !reviewOpen">
              {{ t('sim.lines.review', { n: fmtInt(total(g.key)) }) }} ›
            </button>
            <p v-if="reviewOpen" class="wiz-review-note" @click.stop>{{ t('sim.lines.reviewPending') }}</p>
          </template>
        </template>
        <div v-if="c.key !== 'line1'" class="wiz-planned" @click.stop>
          <button class="wiz-toggle" :class="{ on: planned }" :aria-pressed="planned" @click="setPlanned(!plannedOn)" />
          {{ t('sim.lines.badge.planned') }}
        </div>
      </div>
    </div>
  </section>
</template>
