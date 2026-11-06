<script setup>
/* 多态任务的「方案效果」（后端 sim_effect.py）：同一时刻，各态比基线（第一态，Mussafah 是 L1）
   少上地表多少水、少了几个溢水点。时刻档默认 2 h（三道防线共用时限 T）。
   溢水点清单是基线的，各态逐点按同一批格子取数，不另识别、不做匹配；某态没演算到这个时刻就写
   「未演算到」，不拿别的时刻顶替。点名一律是基线的点号，和当前态地图上的点号对不上，所以点名只飞坐标。 */
import { computed, ref, watch } from 'vue'

import { fetchSimulationStateEffect } from '../../api'
import { locale, t } from '../../i18n'
import { fmtInt, stateCode } from '../../utils/sim'

const props = defineProps({ runId: { type: Number, required: true } })
const emit = defineEmits(['locate'])

const data = ref(null)
const error = ref('')
const hour = ref(null)
const open = ref({})

async function load() {
  error.value = ''
  try {
    data.value = await fetchSimulationStateEffect(props.runId)
    if (hour.value == null || !data.value.checkpoints_h.includes(hour.value)) hour.value = data.value.default_h
  } catch (e) {
    data.value = null
    error.value = e.message
  }
}
watch([() => props.runId, locale], load, { immediate: true })

const idx = computed(() => data.value?.checkpoints_h.indexOf(hour.value) ?? -1)
const pts = computed(() => data.value?.points || [])
const byRank = computed(() => new Map(pts.value.map((p, i) => [p.rank, i])))
const baseCp = computed(() => data.value?.states[0].checkpoints[idx.value] || null)
const label = p => p.name || `#${p.rank} ${p.lat}, ${p.lon}`
const fmtV = v => fmtInt(Math.round(v))

const rows = computed(() => {
  if (!data.value || idx.value < 0) return []
  const b = baseCp.value
  return data.value.states.slice(1).map(s => {
    const cp = s.checkpoints[idx.value]
    const row = { key: s.key, label: stateCode(s.label), cp, reached: cp.reached && b.reached, endH: s.sim_end_minutes / 60 }
    if (!row.reached) return row
    const same = cp.volume_less_m3 === 0 && !cp.cleared.length && !cp.added.length
    const pick = rank => ({ ...pts.value[byRank.value.get(rank)], i: byRank.value.get(rank) })
    const still = pts.value.map((p, i) => ({ ...p, i })).filter(p => cp.active[p.i])
    return {
      ...row, same,
      cleared: cp.cleared.map(pick),
      added: cp.added.map(pick),
      still,
      outside: s.outside_points,
    }
  })
})
const toggle = k => { open.value = { ...open.value, [k]: !open.value[k] } }
const dv = (p, cp) => cp.volume_m3[p.i] - baseCp.value.volume_m3[p.i]
</script>

<template>
  <section class="se">
    <div class="se-head">
      <h3>{{ t('sim.effect.title') }}</h3>
      <div v-if="data" class="se-seg" role="tablist">
        <button v-for="h in data.checkpoints_h" :key="h" role="tab" :class="{ on: h === hour }" :aria-selected="h === hour"
                @click="hour = h">{{ h }} h</button>
      </div>
    </div>
    <p v-if="error" class="se-err">{{ error }}</p>
    <p v-else-if="!data" class="se-note">{{ t('sim.panel.loading') }}</p>
    <template v-else>
      <p class="se-base">
        <template v-if="baseCp?.reached">
          {{ t('sim.effect.base', { base: data.base, h: hour, v: fmtV(baseCp.surface_volume_m3), n: baseCp.active.filter(Boolean).length, total: pts.length, d: data.active_depth_m }) }}
        </template>
        <template v-else>{{ t('sim.effect.baseNotReached', { base: data.base, h: hour }) }}</template>
      </p>

      <div v-for="r in rows" :key="r.key" class="se-row">
        <div class="se-top">
          <span class="se-name" :title="r.label">{{ r.label }}</span>
          <button v-if="r.reached && !r.same" class="se-more" @click="toggle(r.key)">
            {{ open[r.key] ? t('sim.effect.less') : t('sim.effect.more') }}
          </button>
        </div>
        <p v-if="!r.reached" class="se-na">{{ t('sim.effect.notReached', { h: hour, end: r.endH }) }}</p>
        <p v-else-if="r.same" class="se-na">{{ t('sim.effect.same', { base: data.base }) }}</p>
        <div v-else class="se-kpis">
          <div>
            <div class="big" :class="{ good: r.cp.volume_less_m3 > 0, bad: r.cp.volume_less_m3 < 0 }">
              {{ r.cp.volume_less_m3 >= 0 ? t('sim.effect.lessWater') : t('sim.effect.moreWater') }}
              {{ fmtV(Math.abs(r.cp.volume_less_m3)) }}<small>m³</small>
            </div>
            <div class="cap">{{ t('sim.effect.volCap', { v: fmtV(r.cp.surface_volume_m3), pct: r.cp.volume_less_pct == null ? '—' : r.cp.volume_less_pct.toFixed(1), base: data.base }) }}</div>
          </div>
          <div>
            <div class="big" :class="{ good: r.cleared.length > r.added.length }">
              {{ r.cp.base_active_count }} → {{ r.cp.active_count }}
            </div>
            <div class="cap">{{ t('sim.effect.ptsCap', { c: r.cleared.length, a: r.added.length }) }}</div>
          </div>
        </div>

        <div v-if="r.reached && !r.same && open[r.key]" class="se-detail">
          <h4 v-if="r.cleared.length">{{ t('sim.effect.cleared', { n: r.cleared.length, d: data.active_depth_m }) }}</h4>
          <button v-for="p in r.cleared" :key="'c' + p.rank" class="se-pt good" :title="label(p)" @click="emit('locate', p)">
            <span class="nm">{{ label(p) }}</span>
            <span class="d">{{ fmtV(baseCp.volume_m3[p.i]) }} → {{ fmtV(r.cp.volume_m3[p.i]) }} m³</span>
          </button>
          <h4 v-if="r.added.length">{{ t('sim.effect.added', { n: r.added.length }) }}</h4>
          <button v-for="p in r.added" :key="'a' + p.rank" class="se-pt bad" :title="label(p)" @click="emit('locate', p)">
            <span class="nm">{{ label(p) }}</span>
            <span class="d">{{ fmtV(baseCp.volume_m3[p.i]) }} → {{ fmtV(r.cp.volume_m3[p.i]) }} m³</span>
          </button>
          <h4 v-if="r.still.length">{{ t('sim.effect.still', { n: r.still.length }) }}</h4>
          <button v-for="p in r.still" :key="'s' + p.rank" class="se-pt" :title="label(p)" @click="emit('locate', p)">
            <span class="nm">{{ label(p) }}</span>
            <span class="d">{{ fmtV(r.cp.volume_m3[p.i]) }} m³
              <em v-if="Math.round(dv(p, r.cp))" :class="dv(p, r.cp) < 0 ? 'good' : 'bad'">
                {{ dv(p, r.cp) < 0 ? '−' : '+' }}{{ fmtV(Math.abs(dv(p, r.cp))) }}
              </em>
            </span>
          </button>
          <p v-if="r.outside.length" class="se-warn">{{ t('sim.effect.outside', { n: r.outside.length, base: data.base }) }}</p>
        </div>
      </div>
      <p class="se-note">{{ t('sim.effect.rule', { base: data.base, d: data.active_depth_m }) }}</p>
    </template>
  </section>
</template>

<style scoped>
.se { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line-2); }
.se-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.se-head h3 { font-size: 15px; font-weight: 700; margin: 0; }
.se-seg { display: inline-flex; background: rgba(255, 255, 255, 0.05); border-radius: 6px; padding: 2px; }
.se-seg button { border: 0; background: none; color: var(--fg-2); font: inherit; font-size: 12px; padding: 4px 10px;
  border-radius: 4px; cursor: pointer; font-variant-numeric: tabular-nums; }
.se-seg button.on { background: var(--blue-bg); color: var(--blue-lt); }
.se-base { color: var(--fg-2); font-size: 12.5px; margin: 10px 0 4px; line-height: 1.5; }
.se-row { background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 12px 14px; margin-top: 8px; }
.se-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.se-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.se-more { border: 0; background: none; color: var(--blue-lt); font: inherit; font-size: 12px; cursor: pointer; padding: 0; }
.se-na { color: var(--faint); font-size: 12px; margin: 6px 0 0; }
.se-kpis { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 8px; }
.se-kpis .big { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.se-kpis .big small { font-size: 12px; font-weight: 400; color: var(--fg-2); margin-left: 4px; }
.se-kpis .cap { font-size: 11.5px; color: var(--muted); margin-top: 2px; line-height: 1.4; }
.good { color: var(--green); }
.bad { color: var(--red); }
.se-detail { margin-top: 10px; border-top: 1px solid var(--line-2); padding-top: 6px; }
.se-detail h4 { font-size: 12px; font-weight: 600; color: var(--fg-2); margin: 8px 0 2px; }
.se-pt { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; width: 100%; border: 0; background: none;
  padding: 0 4px; line-height: 28px; font: inherit; font-size: 12px; text-align: left; cursor: pointer; border-radius: 4px; color: var(--fg-2); }
.se-pt:hover { background: rgba(255, 255, 255, 0.04); }
.se-pt.good .nm { color: var(--green); }
.se-pt.bad .nm { color: var(--red); }
.se-pt .nm { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.se-pt .d { font-variant-numeric: tabular-nums; white-space: nowrap; }
.se-pt em { font-style: normal; font-size: 11px; margin-left: 4px; }
.se-note { color: var(--muted); font-size: 11.5px; margin: 10px 0 0; line-height: 1.5; }
.se-warn { color: var(--amber); font-size: 11.5px; margin: 8px 0 0; line-height: 1.5; }
.se-err { color: var(--red); font-size: 12px; margin: 8px 0 0; }
</style>
