<script setup>
/* 全市应对设备配置：L2 之后地表还积着的水（水深 ≥ 0.15 m 的部分），按 45 : 20 分给两档 ——
   · L2 sugg = 第一档，固定设施（泵站 / 压力管 / 排海管 / 调蓄塘），约占 69.2%；
   · L3 = 第二档，移动泵与罐车，约占 30.8%。
   数据由详情页取 `/equipment`（成果目录里的两份设备配置文件）：只有全市总量和数量，没有位置、也不是逐台设备；
   管长、扬程、效率是占位值。放在详情页右栏（420 px 宽），一个场景一张小卡，不画宽表。 */
import { computed, ref, watch } from 'vue'

import { t } from '../../i18n'
import CityBalance from './CityBalance.vue'
import { fmtInt } from '../../utils/sim'
import '../../styles/rda.css'

const props = defineProps({
  data: { type: Object, default: null },     // /equipment 的返回；没有就整块不出现
  tab: { type: String, default: 'fixed' },   // 'fixed' = L2 sugg 固定设施，'mobile' = L3 移动泵与罐车；由右上角的 L2 sugg / L3 按钮切
  rainLabel: { type: String, default: '' },
})

const n1 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 }))
const n2 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }))   // 单台流量（1.47 / 2.05）不能只留 1 位
const wan = v => (v == null ? '—' : n1(Number(v) / 1e4))
const pct = v => `${Math.round(Number(v) * 100)}%`

/* 基数是「L2 之后剩余积水」（水深 ≥ 阈值的部分），两档按 45 : 20 分它，占比用 两档水量 ÷ 剩余积水 算 */
const inp = computed(() => props.data?.fixed.inputs || {})
const share = v => `${n1(Number(v) / Number(inp.value.remaining_ponding_after_L2_m3) * 100)}%`
/* L1 不排水，L2 排掉约 35%（用户给的比例）：总积水量 = L2 后剩余积水 ÷ 65%（页面上不标反推） */
/* 水量账：这一档需要排的 → 可以排的 → 排完剩余。L2 sugg 承接 L2 排完剩下的，L3 承接 L2 sugg 排完剩下的 */
const balance = computed(() => {
  const i = inp.value
  const rem = i.remaining_ponding_after_L2_m3
  const afterFixed = rem - i.volume_fixed_m3
  const total = rem / 0.65   // L2 排掉 35%，剩 65%（用户给的比例），总积水量 = 剩余 ÷ 65%
  return props.tab === 'fixed'
    ? { need: rem, drain: i.volume_fixed_m3, left: afterFixed, total }
    : { need: afterFixed, drain: i.volume_mobile_m3, left: Math.max(0, afterFixed - i.volume_mobile_m3), total }
})

const asFixed = computed(() => props.data?.fixed.assumptions || {})
const EXTERNAL_SHARE = 0.3   // L2 sugg 只留外排 30% 这一组（用户定），文件里另有 100% / 50% / 0% 不显示
const fixedRows = computed(() => (props.data?.fixed.scenarios || [])
  .filter(s => s.external_share === EXTERNAL_SHARE)
  .slice().sort((a, b) => a.drain_time_h - b.drain_time_h || b.external_share - a.external_share))
const asMobile = computed(() => props.data?.mobile.assumptions || {})
const lp = s => s.loading_pumps.options   // 装水泵：中型 / 大型二选一
const mobileRows = computed(() => (props.data?.mobile.scenarios || [])
  .slice().sort((a, b) => b.drain_time_h - a.drain_time_h))

const outfallText = s => {
  const o = s.external_discharge.outfall_pipe
  return o.count ? t('sim.equip.outfall', { d: o.dn_mm, n: o.count, km: n1(o.total_length_km) }) : t('sim.equip.noOutfall')
}
const pondText = s => {
  const p = s.ponds
  return p.count ? t('sim.equip.ponds', { n: p.count, v: wan(p.total_volume_m3), a: n1(p.net_area_ha), g: n1(p.area_with_slopes_roads_ha) }) : t('sim.equip.noPonds')
}
/* 点一张场景卡展开它的设备清单：一行一种设备（数量 / 单台规格 / 排水能力或容积）。
   单台规格、数量、总量都取文件里的字段；「合计」只是 单台 × 数量 的乘法，没有别的推算。管长、扬程是文件里的占位值，照实标出。 */
const open = ref(null)
const toggle = id => { open.value = open.value === id ? null : id }
watch(() => props.tab, () => { open.value = null })

const fixedDevices = s => {
  const st = s.pump_stations, pp = s.pressure_pipe, ed = s.external_discharge, op = ed.outfall_pipe, pd = s.ponds
  const rows = [
    { k: 'station', type: t('sim.equip.dev.station'), n: t('sim.equip.dev.nStation', { n: st.count }),
      spec: t('sim.equip.dev.stationSpec', { p: st.duty_pumps_per_station, b: st.standby_pumps_per_station, q: n2(st.station_flow_m3s) }), cap: t('sim.equip.dev.stationCap', { q: n1(st.count * st.station_flow_m3s) }) },
    { k: 'pump', type: t('sim.equip.dev.pump'), n: t('sim.equip.dev.pumpN', { n: st.pumps_total, d: st.duty_pumps_total, b: st.standby_pumps_total }),
      spec: t('sim.equip.dev.pumpSpec', { q: n2(st.pump_unit_flow_m3s), h: asFixed.value.pump_head_m }),
      cap: t('sim.equip.dev.pumpCap', { q: n1(st.duty_pumps_total * st.pump_unit_flow_m3s), mw: n1(st.power_total_mw_assumed) }) },
    { k: 'generator', type: t('sim.equip.dev.generator'), n: t('sim.equip.dev.nSet', { n: st.mobile_generator_junction_boxes }),
      spec: t('sim.equip.dev.generatorSpec'), cap: t('sim.equip.dev.generatorCap') },
    { k: 'pressure', type: t('sim.equip.dev.pressure'), n: t('sim.equip.dev.nPipe', { n: pp.count }),
      spec: t('sim.equip.dev.pressureSpec', { d: pp.dn_mm, l: pp.length_each_m_assumed, v: pp.design_velocity_ms, m: pp.emergency_max_velocity_ms }),
      cap: t('sim.equip.dev.pressureCap', { q: n1(st.station_flow_m3s), km: n1(pp.total_length_km) }) },
  ]
  if (op.count) {
    rows.push({ k: 'outfall', type: t('sim.equip.dev.outfall'), n: t('sim.equip.dev.nPipe', { n: op.count }),
      spec: t('sim.equip.dev.pipeSpec', { d: op.dn_mm, l: op.length_each_m_assumed, v: asFixed.value.outfall_pipe.velocity_ms }),
      cap: t('sim.equip.dev.outfallCap', { q: n1(op.unit_flow_m3s), tq: n1(op.count * op.unit_flow_m3s), km: n1(op.total_length_km) }) })
    rows.push({ k: 'outfallFit', type: t('sim.equip.dev.outfallFit'), n: t('sim.equip.dev.nSet', { n: ed.non_return_valve_chambers }),
      spec: t('sim.equip.dev.outfallFitSpec', { l: ed.tailwater_design_level_m_NADD }),
      cap: t('sim.equip.dev.outfallFitCap', { a: ed.non_return_valve_chambers, b: ed.trash_screens, c: ed.safety_grills }) })
  }
  if (pd.count) {
    rows.push({ k: 'pond', type: t('sim.equip.dev.pond'), n: t('sim.equip.dev.nPond', { n: pd.count }),
      spec: t('sim.equip.dev.pondSpec', { v: wan(pd.volume_each_m3), d: pd.depth_m, a: n1(pd.area_each_ha) }),
      cap: t('sim.equip.dev.pondCap', { tv: wan(pd.total_volume_m3), na: n1(pd.net_area_ha), g: n1(pd.area_with_slopes_roads_ha) }) })
    rows.push({ k: 'spillway', type: t('sim.equip.dev.spillway'), n: t('sim.equip.dev.nSet', { n: pd.emergency_spillways }),
      spec: t('sim.equip.dev.spillwaySpec'), cap: t('sim.equip.dev.spillwayCap') })
    rows.push({ k: 'emptying', type: t('sim.equip.dev.emptying'), n: t('sim.equip.dev.nPump', { n: pd.emptying.pumps }),
      spec: t('sim.equip.dev.emptyingSpec', { q: n2(pd.emptying.pump_unit_flow_m3s_assumed) }),
      cap: t('sim.equip.dev.emptyingCap', { tq: n1(pd.emptying.flow_m3s), d: pd.emptying.within_days }) })
  }
  return rows
}
const mobileDevices = s => {
  const rows = []
  const tk = s.tankers
  rows.push({ k: 'tanker', type: t('sim.equip.dev.tanker'), n: t('sim.equip.dev.nTanker', { n: fmtInt(tk.count) }),
    spec: t('sim.equip.dev.tankerSpec', { c: tk.capacity_m3_each, r: tk.round_trip_h, t: tk.trips_each }),
    cap: t('sim.equip.dev.tankerCap', { v: fmtInt(tk.capacity_m3_each * tk.trips_each), tv: wan(s.volume_by_tankers_m3) }) })
  for (const [key, label] of [['mobile_median', 'median'], ['mobile_large', 'large']]) {
    const x = lp(s)[key]
    rows.push({ k: label, type: t(`sim.equip.dev.${label}`), n: t('sim.equip.dev.nPump', { n: fmtInt(x.count_needed) }),
      spec: t('sim.equip.dev.mobileSpec', { q: n2(x.unit_flow_m3s), h: asMobile.value.pump_head_m }),
      cap: t('sim.equip.dev.mobileCap', { tq: n1(x.count_needed * x.unit_flow_m3s), e: x.existing_available,
        mw: n1(x.power_total_mw_assumed) }),
    })
  }
  if (s.cranes?.count) {
    rows.push({ k: 'crane', type: t('sim.equip.dev.crane'), n: t('sim.equip.dev.nPump', { n: s.cranes.count }),
      spec: t('sim.equip.dev.craneSpec', { n: asMobile.value.crane_per_loading_pumps }), cap: t('sim.equip.dev.craneCap') })
  }
  return rows
}

/** 「装水用中型泵（1.47 m³/s）需 46 台」 */
const pumpText = (key, x, q) => t(`sim.equip.${key}`, { q, n: fmtInt(x.count_needed) })
</script>

<template>
  <section v-if="data" class="sim-card ro sd-equip">
    <div class="rda-ui">
      <h3>{{ tab === 'fixed' ? t('sim.equip.tabFixed', { p: share(inp.volume_fixed_m3) }) : t('sim.equip.tabMobile', { p: share(inp.volume_mobile_m3) }) }}</h3>
      <p class="muted small">{{ t('sim.equip.sub', { rain: rainLabel, th: inp.threshold_depth_m, pf: share(inp.volume_fixed_m3), pm: share(inp.volume_mobile_m3) }) }}</p>
      <p class="warn small">{{ t('sim.equip.caveat') }}</p>

      <CityBalance :title="t(`sim.balance.${tab}`)" :need="balance.need" :drain="balance.drain" :left="balance.left" :total="balance.total" />

      <template v-if="tab === 'fixed'">
        <p class="muted small">{{ t('sim.equip.fixedNote', { n: asFixed.fixed_pump.pumps_per_station, b: fixedRows[0].pump_stations.standby_pumps_per_station, q: asFixed.fixed_pump.unit_flow_m3s }) }}</p>
        <p class="muted small">{{ t('sim.equip.dev.hint') }}</p>
        <div class="eq-list">
          <div v-for="s in fixedRows" :key="s.id" class="eq-item" :class="{ open: open === s.id }" role="button" tabindex="0" @click="toggle(s.id)" @keydown.enter="toggle(s.id)">
            <div class="eq-h"><b>{{ t('sim.equip.headFixed', { h: s.drain_time_h, e: pct(s.external_share) }) }}</b><em>{{ t('sim.equip.flow', { q: n1(s.total_flow_required_m3s) }) }}</em></div>
            <div>{{ t('sim.equip.stations', { n: s.pump_stations.count, p: s.pump_stations.duty_pumps_total, b: s.pump_stations.standby_pumps_total }) }} · {{ t('sim.equip.pipe', { d: s.pressure_pipe.dn_mm, n: s.pressure_pipe.count, km: n1(s.pressure_pipe.total_length_km) }) }}</div>
            <div>{{ outfallText(s) }}</div>
            <div>{{ pondText(s) }}</div>
            <table v-if="open === s.id" class="tbl eq-dev">
              <thead><tr><th>{{ t('sim.equip.dev.type') }}</th><th>{{ t('sim.equip.dev.count') }}</th><th>{{ t('sim.equip.dev.spec') }}</th><th>{{ t('sim.equip.dev.cap') }}</th></tr></thead>
              <tbody><tr v-for="r in fixedDevices(s)" :key="r.k"><td>{{ r.type }}</td><td>{{ r.n }}</td><td>{{ r.spec }}</td><td>{{ r.cap }}</td></tr></tbody>
            </table>
          </div>
        </div>
        <p class="muted small">{{ t('sim.equip.manual') }}</p>
        <p class="muted small">{{ t('sim.equip.fixedAssume', {
          od: asFixed.outfall_pipe.dn_mm, ov: asFixed.outfall_pipe.velocity_ms, pv: asFixed.pressure_pipe_velocity_ms,
          vol: wan(asFixed.pond.volume_m3), d: asFixed.pond.depth_m, f: asFixed.pond.freeboard_silt_factor, fp: asFixed.pond.footprint_factor,
          pl: asFixed.pressure_pipe_length_each_m, ol: asFixed.outfall_pipe.length_each_m, h: asFixed.pump_head_m, e: asFixed.pump_efficiency }) }}</p>
      </template>

      <template v-else>
        <p class="muted small">{{ t('sim.equip.mobileNote', { n: asMobile.existing_movable_pumps.count, q: n1(asMobile.existing_movable_pumps.total_flow_m3s) }) }}</p>
        <p class="muted small">{{ t('sim.equip.dev.hint') }}</p>
        <div class="eq-list">
          <div v-for="s in mobileRows" :key="s.id" class="eq-item" :class="{ open: open === s.id }" role="button" tabindex="0" @click="toggle(s.id)" @keydown.enter="toggle(s.id)">
            <div class="eq-h"><b>{{ t('sim.equip.headMobile', { h: s.drain_time_h }) }}</b><em>{{ t('sim.equip.flowPump', { q: n1(s.loading_pumps.flow_required_m3s) }) }}</em></div>
            <div>{{ t('sim.equip.volumes', { b: wan(s.volume_by_tankers_m3) }) }}</div>
            <div>{{ t('sim.equip.tankers', { n: fmtInt(s.tankers.count), c: s.tankers.capacity_m3_each, t: s.tankers.trips_each }) }}</div>
            <div>{{ pumpText('median', lp(s).mobile_median, asMobile.loading_pump_units_m3s.mobile_median) }}</div>
            <div>{{ pumpText('large', lp(s).mobile_large, asMobile.loading_pump_units_m3s.mobile_large) }}</div>
            <template v-if="open === s.id">
              <table class="tbl eq-dev">
                <thead><tr><th>{{ t('sim.equip.dev.type') }}</th><th>{{ t('sim.equip.dev.count') }}</th><th>{{ t('sim.equip.dev.spec') }}</th><th>{{ t('sim.equip.dev.cap') }}</th></tr></thead>
                <tbody><tr v-for="r in mobileDevices(s)" :key="r.k"><td>{{ r.type }}</td><td>{{ r.n }}</td><td>{{ r.spec }}</td><td>{{ r.cap }}</td></tr></tbody>
              </table>
              <p class="muted small">{{ t('sim.equip.dev.altNote') }}</p>
            </template>
          </div>
        </div>
        <p class="muted small">{{ t('sim.equip.mobileAssume', {
          c: asMobile.tanker.capacity_m3, r: asMobile.tanker.round_trip_h, h: asMobile.pump_head_m, e: asMobile.pump_efficiency }) }}</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* 详情页右栏：和 LevelPanel 同一列（.sd-grid > .ro 已给 sticky / 上边距），内容多就在栏内滚 */
.sd-equip { padding: 4px 6px 10px; max-height: calc(100vh - 40px); overflow-y: auto; }
.rda-ui .kpis b { font-size: 17px; }
.rda-ui .kpis > .cur { outline: 1px solid var(--blue); }
.eq-list { display: grid; gap: 8px; margin: 8px 0; }
.eq-item { background: var(--card-2); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; line-height: 1.5; cursor: pointer; }
.eq-item:hover { background: var(--blue-bg); }
.eq-item.open { outline: 1px solid var(--blue); }
.eq-dev { margin: 8px 0 2px; font-size: 12px; cursor: default; }
.eq-dev td, .eq-dev th { vertical-align: top; }
.eq-h { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 2px; }
.eq-h em { font-style: normal; color: var(--muted); }
</style>
