<script setup>
/* 结果地图下方的「残留积水：为什么退不掉」（/layers/residual，算法见 backend sim_residual.py）。
   演算末路面上的水按洼地分块，每块归一类原因（class 是协议值，这里翻）；点一行 → 地图飞到该块（emit focus）。
   只有四态交付拆得出，别的交付后端回 422，ResultMap 不挂本面板。 */
import { computed, ref } from 'vue'

import { RESIDUAL_COLOR } from '../../constants'
import { t } from '../../i18n'
import { fmtInt } from '../../utils/sim'

const props = defineProps({
  data: { type: Object, required: true },
  active: { type: Number, default: null },
})
const emit = defineEmits(['focus'])

const CLASSES = ['no_inlet', 'below_rim', 'not_taking']
const PAGE = 20
const showAll = ref(false)
const rows = computed(() => (showAll.value ? props.data.ponds : props.data.ponds.slice(0, PAGE)))
const s = computed(() => props.data.summary)

const fmtClock = min => `T+${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, '0')}`
const vol = x => (x == null ? '—' : fmtInt(Math.round(x)))
const pct = (v, tot) => (tot ? Math.round((v / tot) * 100) : 0)
const ha = m2 => (m2 / 1e4).toFixed(2)
const trend = p => {
  if (p.volume_before_m3 == null) return '—'
  const d = Math.round(p.volume_m3 - p.volume_before_m3)
  return d > 0 ? `+${fmtInt(d)}` : d < 0 ? `−${fmtInt(-d)}` : '0'
}
const minRim = p => (p.inlets.length ? Math.min(...p.inlets.map(i => i.rim_m)) : null)
const inletTitle = p => p.inlets.map(i => t('sim.residual.inletTip', {
  id: i.id, rim: i.rim_m.toFixed(2), ret: fmtInt(Math.round(i.returned_m3)), ovf: i.overflowed ? t('sim.residual.overflowed') : '',
})).join('\n')
const storage = computed(() => Object.entries(s.value.storage_fill_pct || {}).filter(([, v]) => v != null))
const allUnder = computed(() => storage.value.length && storage.value.every(([, v]) => v < 95))
</script>

<template>
  <div class="rp">
    <div class="rp-head">
      <div class="bh">{{ t('sim.residual.title') }}</div>
      <span class="rp-sum">{{ t('sim.residual.summary', {
        t: fmtClock(s.end_minutes), vol: vol(s.surface_volume_m3), n: s.pond_count, pv: vol(s.pond_volume_m3) }) }}</span>
    </div>
    <p class="rp-line">{{ t('sim.residual.balance', {
      up: vol(s.overflow_m3), back: vol(s.return_m3), nr: s.returning_node_count, nn: s.node_count, out: vol(s.boundary_out_m3) }) }}</p>
    <p v-if="storage.length" class="rp-line">
      {{ t('sim.residual.storage') }}
      <span v-for="[k, v] in storage" :key="k" class="rp-sto">{{ k }} {{ Math.round(v) }}%</span>
      <b v-if="allUnder" class="rp-ok">{{ t('sim.residual.storageNote') }}</b>
    </p>

    <div class="rp-cls">
      <div v-for="c in CLASSES" :key="c" class="rp-c">
        <div class="rp-c-top">
          <span class="sw" :style="{ background: RESIDUAL_COLOR[c] }" />
          <b>{{ t(`sim.residual.cls.${c}`) }}</b>
          <span class="num">{{ s.by_class[c].count }} · {{ vol(s.by_class[c].volume_m3) }} m³ · {{ pct(s.by_class[c].volume_m3, s.surface_volume_m3) }}%</span>
        </div>
        <div class="bar"><i :style="{ width: `${pct(s.by_class[c].volume_m3, s.surface_volume_m3)}%`, background: RESIDUAL_COLOR[c] }" /></div>
        <div class="hint">{{ t(`sim.residual.clsHint.${c}`) }}</div>
      </div>
    </div>
    <p class="rp-rule">{{ t('sim.residual.rule', { m: Math.round(data.params.rim_margin_m * 100) }) }}</p>

    <div class="rp-table-wrap">
      <table class="rp-table">
        <thead>
          <tr>
            <th>#</th>
            <th>{{ t('sim.residual.cols.cls') }}</th>
            <th class="n">{{ t('sim.residual.cols.vol') }}</th>
            <th class="n">{{ t('sim.residual.cols.trend') }}</th>
            <th class="n">{{ t('sim.residual.cols.area') }}</th>
            <th class="n">{{ t('sim.residual.cols.depth') }}</th>
            <th class="n">{{ t('sim.residual.cols.level') }}</th>
            <th>{{ t('sim.residual.cols.near') }}</th>
            <th>{{ t('sim.residual.cols.took') }}</th>
            <th>{{ t('sim.residual.cols.sto') }}</th>
            <th>{{ t('sim.residual.cols.poi') }}</th>
            <th>{{ t('sim.residual.cols.loc') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in rows" :key="p.rank" :class="{ on: p.rank === active }" @click="emit('focus', p.rank)">
            <td>{{ p.rank }}</td>
            <td><span class="sw" :style="{ background: RESIDUAL_COLOR[p.class] }" />{{ t(`sim.residual.cls.${p.class}`) }}</td>
            <td class="n">{{ vol(p.volume_m3) }}</td>
            <td class="n">{{ trend(p) }}</td>
            <td class="n">{{ ha(p.area_m2) }}</td>
            <td class="n">{{ p.max_depth_m.toFixed(2) }}</td>
            <td class="n" :title="inletTitle(p)">
              {{ p.surface_m.toFixed(2) }} / {{ minRim(p) == null ? t('sim.residual.noInlet') : minRim(p).toFixed(2) }}
            </td>
            <td>{{ p.nearest_inlet ? `${p.nearest_inlet.id} ${p.nearest_inlet.distance_m} m` : '—' }}</td>
            <td>{{ p.nearest_returning_inlet ? `${p.nearest_returning_inlet.id} ${p.nearest_returning_inlet.distance_m} m` : '—' }}</td>
            <td>{{ p.nearest_storage ? `${p.nearest_storage.id} ${p.nearest_storage.distance_m} m` : '—' }}</td>
            <td class="name" :title="p.poi?.name || ''">{{ p.poi ? `${p.poi.name} ${p.poi.distance_m} m` : '—' }}</td>
            <td class="loc">{{ p.lat }}, {{ p.lon }}</td>
          </tr>
        </tbody>
      </table>
      <button v-if="data.ponds.length > PAGE" class="sim-btn rp-more" @click="showAll = !showAll">
        {{ showAll ? t('sim.residual.less') : t('sim.residual.more', { n: data.ponds.length }) }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.rp { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 14px; }
.rp-head { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.rp-head .bh { font-weight: 600; font-size: 15px; }
.rp-sum { color: var(--fg); font-size: 13px; }
.rp-line { color: var(--fg-2); font-size: 12.5px; margin: 6px 0 0; }
.rp-sto { margin-right: 10px; font-variant-numeric: tabular-nums; }
.rp-ok { color: #8fd79a; font-weight: 500; }
.rp-cls { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.rp-c { background: var(--field); border: 1px solid var(--line-2); border-radius: 6px; padding: 8px 10px; }
.rp-c-top { display: flex; align-items: center; gap: 6px; font-size: 13px; flex-wrap: wrap; }
.rp-c-top .num { margin-left: auto; color: var(--fg-2); font-variant-numeric: tabular-nums; }
.rp-c .bar { height: 4px; background: var(--line); border-radius: 2px; margin: 6px 0; overflow: hidden; }
.rp-c .bar i { display: block; height: 100%; }
.rp-c .hint { color: var(--muted); font-size: 12px; line-height: 1.45; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.rp-rule { color: var(--muted); font-size: 12px; margin: 8px 0 0; }
.rp-table-wrap { margin-top: 12px; overflow-x: auto; }
.rp-table { width: 100%; border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.rp-table th { text-align: left; font-weight: 500; color: var(--muted); padding: 6px 8px; border-bottom: 1px solid var(--line-2);
  white-space: nowrap; }
.rp-table td { padding: 6px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
.rp-table .n { text-align: right; }
.rp-table .loc { color: var(--fg-2); }
.rp-table .name { max-width: 240px; overflow: hidden; text-overflow: ellipsis; }
.rp-table tbody tr { cursor: pointer; }
.rp-table tbody tr:hover { background: rgba(255, 255, 255, 0.04); }
.rp-table tbody tr.on { background: var(--blue-bg); }
.rp-more { margin-top: 10px; }
@media (max-width: 900px) { .rp-cls { grid-template-columns: 1fr; } }
</style>
