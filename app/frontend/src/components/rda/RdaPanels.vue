<script setup>
/* RD-A 全套成果的明细面板：积水点（阈值可调重算）/ L3 派单 / 设施 / 判据 / L2+suggested 措施。
   `/rda` 页的右栏和仿真任务详情页（RDA 那条）共用这一份；数据与选中项都在 `useRda()` 里，传进来的 `rda`。
   点行时往外抛 `locate`，由外面的地图去飞 / 画高亮；本组件不碰地图。
   `overview` 插槽给 /rda 页放它自己的概览（详情页已有对比 / 效果分析，不放）。 */
import { computed, ref, useSlots } from 'vue'

import { ledgerId } from '../../composables/useRda'
import PondingTab from './PondingTab.vue'
import { tr, trj } from '../../i18n/rda'
import '../../styles/rda.css'

const props = defineProps({
  rda: { type: Object, required: true },
  tab: { type: String, default: 'ponding' },
  label: { type: String, default: '' },        // 当前态的名字（标题里用）
})
const emit = defineEmits(['update:tab', 'locate'])
const slots = useSlots()
const R = props.rda   // useRda() 返回的是一束 ref，模板里 .value 会自动解包

const f1 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 }))
const f2 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }))
const int = v => (v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'))
const musd = v => (v == null ? '—' : (Number(v) / 1e6).toFixed(2))
const dn = m => Math.round((m || 0) * 1000)
const pct = v => (v == null ? '—' : `${Number(v).toFixed(1)}%`)
/** 峰值出现时刻（从降雨开始，h）：表格里跟在数值下面 */
const at = h => (h == null ? '' : `@ ${Number(h).toFixed(2)} h`)

const KIND_COLOR = {
  ps_l1: '#9aa3ad', mobile: '#e0a44e', ps_planned: '#a07be0',
  tank: '#4786c9', gpipe: '#3fb6a8', conv: '#d9434b', sump: '#4dc3ee',
}
const LEVEL_COLOR = { 关键: '#d9434b', 高: '#e0702e', 中: '#e0a44e', 无暴露: '#7a838c' }
const VERDICT_COLOR = { 自然退干: '#66c56b', 'L3 按时退干': '#66c56b', 'L3 已派、超时': '#e0a44e', 只监测: '#7a838c' }
const tallyWord = k => tr(k === 'pass' ? '通过' : k === 'review' ? '需复核' : '缺数据')

const setTab = t => emit('update:tab', t)

function pickPoint(id) {
  R.selPoint.value = id
  const p = (R.ponding.value?.points || []).find(x => x.id === id)
  if (p) emit('locate', { lat: p.lat, lon: p.lon, kind: 'point', id })
}
async function pickMeasure(id) {
  setTab('suggested')
  await R.pickMeasure(id)
  const m = R.measureById.value[id]
  if (m) emit('locate', { lat: m.lat, lon: m.lon, kind: 'measure', id })
}
function pickFacilityById(id) {
  const f = (R.fac.value?.geojson.features || []).find(x => x.properties['编号'] === id)
  if (!f) return
  R.selFac.value = f.properties
  const lid = ledgerId(id)
  if (R.isSugg.value && R.measureById.value[lid]) pickMeasure(lid)
  else {
    setTab('facilities')
    const [lo, la] = f.geometry.type === 'Point' ? f.geometry.coordinates : [f.properties['经度'], f.properties['纬度']]
    if (la != null) emit('locate', { lat: la, lon: lo, kind: 'facility', id })
  }
}
defineExpose({ pickMeasure, pickFacilityById, pickPoint })

function measureDrain(m) {
  // 「怎么排水」：照台账字段拼成一句人话，不另编口径
  const bits = []
  if (m.type.includes('调蓄池') && !m.type.includes('泵站 →')) {
    bits.push(tr('截流井 {w} 的溢流重力进入地下池', { w: m.intercept_wells }))
    bits.push(tr('池容 {v} m³、有效水深 {d} m、池底 {f} m、开挖 {e} m', { v: int(m.tank_m3), d: f1(m.effective_depth_m), f: f2(m.invert_m), e: f1(m.excavation_m) }))
    bits.push(tr('雨后由排空泵自动排回管网'))
  }
  if (m.pumps) bits.push(tr('{n} 台泵 × {q} m³/s（设计流量 {d} m³/s）', { n: m.pumps, q: f2(m.pump_q_m3s), d: f2(m.design_q_m3s) }))
  if (m.pipe_len_m && m.pipe_parallel) {
    bits.push(tr('压力管 {l} m、DN{d}×{p}、峰值流速 {v} m/s', { l: int(m.pipe_len_m), d: dn(m.pipe_dia_m), p: m.pipe_parallel, v: f2(m.velocity_ms) }))
  } else if (m.pipe_len_m) {
    bits.push(tr('重力管（截流井→池）{l} m、DN{d}', { l: int(m.pipe_len_m), d: dn(m.pipe_dia_m) }))
  }
  if (m.outfall) bits.push(tr('排到新建排海口 {o}', { o: m.outfall }))
  return bits
}

/* ---- 规划设备清单：推荐方案新增的每一台设备（地下调蓄池 / 重力管 / 泵站 + 压力输水 / 地面取水口），逐台一行 ----
   数据 = 本态设施图层（编号 / 类型 / 位置 / 流量 / 池容）+ 措施台账（设计参数）+ 地面取水口表；关键参数一句话照台账字段拼，不另编口径。
   点一行 = 定位并打开所属措施的详情（设计参数 / 怎么排水 / 合规检查）。 */
const DEVICE_KINDS = ['tank', 'gpipe', 'conv', 'outfall', 'sump']
const devKind = ref('all')
// 排海口不在设施图层里（图层只画泵站 / 池 / 管），名称与颜色在这里补，与压力输水同级
const devKindName = k => (k === 'outfall' ? tr('新建排海口（设备）') : R.kindName(k))
const DEV_COLOR = { ...KIND_COLOR, outfall: '#8e5bd9' }
const intakeById = computed(() => Object.fromEntries((R.suggested.value?.intakes || []).map(r => [r['取水口'], r])))
function deviceDetail(kind, m, ik) {
  const b = []
  if (kind === 'tank' && m) {
    b.push(tr('池容 {v} m³、有效水深 {d} m、池底 {f} m、开挖 {e} m', { v: int(m.tank_m3), d: f1(m.effective_depth_m), f: f2(m.invert_m), e: f1(m.excavation_m) }))
    if (m.intercept_wells) b.push(`${tr('截流井')} ${m.intercept_wells}`)
  } else if (kind === 'gpipe' && m) {
    b.push(tr('重力管（截流井→池）{l} m、DN{d}', { l: int(m.pipe_len_m), d: dn(m.pipe_dia_m) }))
  } else if (kind === 'conv' && m) {
    if (m.pumps) b.push(tr('{n} 台泵 × {q} m³/s（设计流量 {d} m³/s）', { n: m.pumps, q: f2(m.pump_q_m3s), d: f2(m.design_q_m3s) }))
    if (m.pipe_len_m) b.push(tr('压力管 {l} m、DN{d}×{p}、峰值流速 {v} m/s', { l: int(m.pipe_len_m), d: dn(m.pipe_dia_m), p: m.pipe_parallel || 1, v: f2(m.velocity_ms) }))
    if (m.outfall) b.push(tr('排到新建排海口 {o}', { o: m.outfall }))
  } else if (kind === 'sump' && ik) {
    b.push(tr(ik['类别']), `${tr('坑底 m')} ${f2(ik['坑底高程 m'])}`, `${tr('取水 m³/s')} ${f2(ik['取水流量 m³/s'])}`, `${tr('泵站')} ${ik['所属地面泵站']}`)
  }
  return b.join(' · ')
}
function pickDevice(d) {
  // 排海口没有设施图层要素：直接按坐标定位；其余设备照旧开所属措施
  if (d.kind === 'outfall') emit('locate', { lat: d.lat, lon: d.lon, kind: 'facility', id: d.id })
  else pickFacilityById(d.id)
}
const devices = computed(() => {
  const feats = (R.fac.value?.geojson.features || []).map(f => f.properties).filter(p => DEVICE_KINDS.includes(p.kind))
  const rows = feats.map(p => {
    const lid = ledgerId(p['编号'])
    const m = R.measureById.value[lid]
    const ik = intakeById.value[String(p['编号'])]
    return { id: p['编号'], kind: p.kind, lid, hotspot: m?.hotspot ?? ik?.['服务热点'] ?? '—', flow: p['流量 L/s'], tank: p['池容 m³'], detail: deviceDetail(p.kind, m, ik) }
  })
  // 新建排海口：只算本态已有压力输水所接的（与上面的 conv 行同一批措施），一个排海口一行，可被多条输水共用
  const convLids = new Set(rows.filter(r => r.kind === 'conv').map(r => r.lid))
  const byOut = {}
  for (const m of R.suggested.value?.measures || []) {
    if (m.outfall && convLids.has(m.id)) (byOut[m.outfall] ||= []).push(m)
  }
  for (const [oid, ms] of Object.entries(byOut)) {
    const q = ms.reduce((s, m) => s + (m.design_q_m3s || 0), 0)
    rows.push({
      id: oid, kind: 'outfall', lid: ms.map(m => m.id).join('、'), hotspot: [...new Set(ms.map(m => m.hotspot))].join('、'), flow: null, tank: null,
      lat: ms[0].outfall_lat, lon: ms[0].outfall_lon,
      detail: [tr('经度 {lon}、纬度 {lat}', { lon: ms[0].outfall_lon, lat: ms[0].outfall_lat }), tr('{n} 条压力输水接入，设计流量合计 {q} m³/s', { n: ms.length, q: f2(q) })].join(' · '),
    })
  }
  return rows.sort((a, b) => DEVICE_KINDS.indexOf(a.kind) - DEVICE_KINDS.indexOf(b.kind) || String(a.lid).localeCompare(String(b.lid)) || String(a.id).localeCompare(String(b.id)))
})
const devCounts = computed(() => {
  const out = {}
  for (const d of devices.value) out[d.kind] = (out[d.kind] || 0) + 1
  return out
})
const shownDevices = computed(() => (devKind.value === 'all' ? devices.value : devices.value.filter(d => d.kind === devKind.value)))

const hasOverview = computed(() => !!slots.overview)
/** 本态设施图层里各类型的个数（逐级累加，含前面各态） */
const facCounts = computed(() => {
  const out = {}
  for (const f of R.fac.value?.geojson.features || []) out[f.properties.kind] = (out[f.properties.kind] || 0) + 1
  return out
})
</script>

<template>
  <div class="rda-ui">
    <nav class="tabs">
      <button v-if="hasOverview" :class="{ on: tab === 'overview' }" @click="setTab('overview')">{{ tr('概览') }}</button>
      <button :class="{ on: tab === 'ponding' }" @click="setTab('ponding')">{{ tr('积水点') }} {{ rda.ponding.value?.count ?? '' }}</button>
      <button :class="{ on: tab === 'l3' }" @click="setTab('l3')">{{ tr('L3 派单') }}</button>
      <button :class="{ on: tab === 'facilities' }" @click="setTab('facilities')">{{ tr('设施') }}</button>
      <button :class="{ on: tab === 'criteria' }" @click="setTab('criteria')">{{ tr('判据') }}</button>
      <button v-if="rda.isSugg.value" :class="{ on: tab === 'suggested', sugg: true }" @click="setTab('suggested')">
        {{ tr('Suggested 措施') }} {{ rda.suggested.value?.measures.length ?? '' }}
      </button>
    </nav>

    <!-- 概览（只有 /rda 页给） -->
    <div v-if="tab === 'overview' && hasOverview" class="pane"><slot name="overview" /></div>

    <!-- 积水点：与全市一二维任务共用同一个组件（内容与布局保持 RD-A 原样） -->
    <PondingTab v-else-if="tab === 'ponding'" :rda="rda" @locate="emit('locate', $event)" />

    <!-- L3 -->
    <div v-else-if="tab === 'l3'" class="pane">
      <template v-if="rda.l3.value">
        <h3>{{ tr('L3 承接与派单') }}</h3>
        <p class="muted small">{{ tr(rda.l3.value.basis?.points) }}</p>
        <div class="kpis">
          <div><b>{{ rda.l3.value.summary.n }}</b><span>{{ tr('进入 L3 的残余积水点') }}</span></div>
          <div><b>{{ rda.l3.value.summary.n_nat_ok }}</b><span>{{ tr('自然退干达标') }}</span></div>
          <div><b>{{ rda.l3.value.summary.n_l3 }}</b><span>{{ tr('需 L3 处置') }}</span></div>
          <div><b>{{ rda.l3.value.summary.n_beyond }}</b><span>{{ tr('超出 L3 单点能力（应由 L2 解决）') }}</span></div>
          <div><b>{{ rda.l3.value.summary.n_noexpo }}</b><span>{{ tr('无暴露，只监测') }}</span></div>
          <div><b>{{ int(rda.l3.value.summary.vol_beyond_m3) }}</b><span>{{ tr('超出能力的峰值积水量 m³') }}</span></div>
        </div>
        <h4>{{ tr('后果等级') }}</h4>
        <div class="levels">
          <span v-for="(n, lv) in rda.l3.value.summary.levels" :key="lv" class="pill" :style="{ background: LEVEL_COLOR[lv] }">{{ tr(lv) }} {{ n }}</span>
        </div>
        <h4>{{ tr('调度结果') }}</h4>
        <table class="tbl">
          <thead><tr><th>{{ tr('调度范围') }}</th><th>{{ tr('结果') }}</th><th>{{ tr('泵/车') }}</th><th>{{ tr('成本 AED') }}</th></tr></thead>
          <tbody>
            <tr v-for="d in [rda.l3.value.dispatch.island, rda.l3.value.dispatch.adm]" :key="d.pool">
              <td>{{ tr(d.pool_zh) }}</td>
              <td><span v-for="(n, k) in d.counts" :key="k" class="chip" :style="{ borderColor: VERDICT_COLOR[k] }">{{ tr(k) }} {{ n }}</span></td>
              <td>{{ tr('{p} 泵 / {t} 罐车', { p: d.pumps, t: d.tankers }) }}</td><td>{{ int(d.cost_aed) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted small">{{ tr('派单口径：') }}{{ tr(rda.l3.value.basis?.dispatch) }}。{{ tr(rda.l3.value.basis?.decide) }}。</p>
        <template v-if="rda.l3.value.not_in_l3?.length">
          <h4>{{ tr('未进入 L3 清单的积水点') }}</h4>
          <ul class="plain"><li v-for="r in rda.l3.value.not_in_l3" :key="r['积水点']"><b>{{ r['积水点'] }}</b>：{{ tr(r['原因']) }}</li></ul>
        </template>
      </template>
      <p v-else class="muted">{{ tr('这个状态没有 L3 承接汇总。') }}</p>
    </div>

    <!-- 设施 -->
    <div v-else-if="tab === 'facilities'" class="pane">
      <h3>{{ tr(label) }} · {{ tr('设施图层') }}</h3>
      <p class="muted small">{{ tr('逐级累加：每个状态包含前面各状态的全部设施。') }}</p>
      <table class="tbl">
        <thead><tr><th>{{ tr('类型') }}</th><th>{{ tr('数量') }}</th></tr></thead>
        <tbody>
          <tr v-for="(n, k) in facCounts" :key="k"><td><u class="sw" :style="{ background: KIND_COLOR[k] }" />{{ rda.kindName(k) }}</td><td>{{ n }}</td></tr>
        </tbody>
      </table>
      <div v-if="rda.selFac.value" class="card">
        <h4>{{ tr(rda.selFac.value['类型']) }} · {{ rda.selFac.value['编号'] }} <small class="muted">{{ tr(rda.selFac.value['防线层级']) }}</small></h4>
        <dl class="kv">
          <template v-if="rda.selFac.value['流量 L/s']"><dt>{{ tr('流量') }}</dt><dd>{{ int(rda.selFac.value['流量 L/s']) }} L/s</dd></template>
          <template v-if="rda.selFac.value['池容 m³']"><dt>{{ tr('池容') }}</dt><dd>{{ int(rda.selFac.value['池容 m³']) }} m³</dd></template>
          <template v-if="rda.selFac.value.p_depth"><dt>{{ tr('有效水深 / 池底') }}</dt><dd>{{ f1(rda.selFac.value.p_depth) }} m / {{ f2(rda.selFac.value.p_floor) }} m</dd></template>
          <template v-if="rda.selFac.value.p_n"><dt>{{ tr('泵台数') }}</dt><dd>{{ rda.selFac.value.p_n }}</dd></template>
          <template v-if="rda.selFac.value.p_path"><dt>{{ tr('管长') }}</dt><dd>{{ int(rda.selFac.value.p_path) }} m</dd></template>
          <template v-if="rda.selFac.value.p_size"><dt>{{ tr('泵规格') }}</dt><dd>{{ rda.selFac.value.p_size }}</dd></template>
          <template v-if="rda.selFac.value.p_on != null"><dt>{{ tr('开 / 停泵水位') }}</dt><dd>{{ f2(rda.selFac.value.p_on) }} / {{ f2(rda.selFac.value.p_off) }} m</dd></template>
          <template v-if="rda.selFac.value.p_hotspot"><dt>{{ tr('服务热点') }}</dt><dd>{{ rda.selFac.value.p_hotspot }}</dd></template>
          <dt>{{ tr('说明') }}</dt><dd>{{ tr(rda.selFac.value['说明']) }}</dd>
        </dl>
        <h5>{{ tr('本场模拟里的实际表现') }}</h5>
        <dl class="kv">
          <template v-if="rda.selFac.value['本场模拟_vol_m3'] != null"><dt>{{ tr('累计处理水量') }}</dt><dd>{{ int(rda.selFac.value['本场模拟_vol_m3']) }} m³</dd></template>
          <template v-if="rda.selFac.value['本场模拟_util_pct'] != null"><dt>{{ tr('利用率') }}</dt><dd>{{ f1(rda.selFac.value['本场模拟_util_pct']) }} %</dd></template>
          <template v-if="rda.selFac.value['本场模拟_max_m3'] != null">
            <dt>{{ tr('池内最大蓄水') }}</dt><dd>{{ int(rda.selFac.value['本场模拟_max_m3']) }} m³（{{ f1(rda.selFac.value['本场模拟_max_pct']) }} % {{ tr('满') }}）</dd>
          </template>
        </dl>
      </div>
      <h4>{{ tr('设施清单') }}</h4>
      <table class="tbl small-t">
        <thead><tr><th>{{ tr('层级') }}</th><th>{{ tr('编号') }}</th><th>{{ tr('类型') }}</th><th>{{ tr('流量 L/s') }}</th><th>{{ tr('池容 m³') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in rda.fac.value?.list" :key="r['编号']" @click="pickFacilityById(r['编号'])">
            <td>{{ tr(r['防线层级']) }}</td><td>{{ r['编号'] }}</td><td>{{ tr(r['类型']) }}</td>
            <td>{{ int(r['流量 L/s']) }}</td><td>{{ int(r['池容 m³']) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 判据 -->
    <div v-else-if="tab === 'criteria' && rda.crit.value" class="pane">
      <h3>{{ tr(label) }} · {{ tr('四项判据') }}</h3>
      <p class="muted small">{{ tr('热点 200 m 内，四项全过才算达标。') }}</p>
      <ul class="plain"><li v-for="(t, k) in rda.crit.value.rules" :key="k"><b>{{ k }}</b> {{ tr(t) }}</li></ul>
      <table class="tbl">
        <thead>
          <tr>
            <th>{{ tr('热点') }}</th><th>{{ tr('最大 m') }}</th><th>≥0.30 ha</th><th>≥0.15 ha·h</th><th>{{ tr('退到 0.10 m') }}</th><th>A</th><th>B</th><th>C</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in rda.crit.value.hotspots" :key="h.id">
            <td>{{ h.id }}</td><td>{{ f2(h.max_m) }}</td><td>{{ f2(h.ge030_ha) }}</td>
            <td>{{ f2(h.ge015_ha_h) }} <small class="muted">/ {{ f2(h.base_ge015_ha_h) }}</small></td>
            <td>{{ h.recede_h_after_rain == null ? tr('未退') : f1(h.recede_h_after_rain) + ' h' }}</td>
            <td v-for="k in ['A', 'B', 'C']" :key="k" :class="h.pass[k] ? 'ok' : 'bad'">{{ h.pass[k] ? '✓' : '✗' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="muted small">{{ tr('≥0.15 ha·h 一列斜杠后是 L2+planned 基准。') }}</p>
      <div class="kpis">
        <div><b :class="rda.crit.value.domain.pass_D ? 'ok' : 'bad'">{{ rda.crit.value.domain.pass_D ? tr('过') : tr('不过') }}</b><span>{{ tr('D 域级') }}</span></div>
        <div><b>{{ f2(rda.crit.value.domain.deeper_ge005_ha) }}</b><span>{{ tr('比 planned 加深 ha') }}</span></div>
        <div><b>{{ f2(rda.crit.value.domain.shallower_ge005_ha) }}</b><span>{{ tr('比 planned 变浅 ha') }}</span></div>
        <div><b>{{ int(rda.crit.value.domain.peak_vol_m3) }}</b><span>{{ tr('全域峰值积水量 m³') }}</span></div>
      </div>
    </div>

    <!-- Suggested 措施 -->
    <div v-else-if="tab === 'suggested' && rda.suggested.value" class="pane">
      <template v-if="rda.selectedMeasure.value">
        <button class="back" @click="rda.selMeasure.value = null">‹ {{ tr('全部措施') }}</button>
        <h3>{{ rda.selectedMeasure.value.id }} · {{ tr(rda.selectedMeasure.value.type) }}</h3>
        <p class="muted">
          {{ tr('服务热点') }} {{ rda.selectedMeasure.value.hotspot }} · {{ tr('占位造价') }} <b>{{ musd(rda.selectedMeasure.value.cost_usd) }}</b> {{ tr('百万美元') }} ·
          <span class="pill amber">{{ tr(rda.selectedMeasure.value.buildability) }}</span>
        </p>
        <h4>{{ tr('怎么排水') }}</h4>
        <ol class="steps"><li v-for="b in measureDrain(rda.selectedMeasure.value)" :key="b">{{ b }}</li></ol>
        <h4>{{ tr('设计参数') }}</h4>
        <dl class="kv">
          <dt>{{ tr('截流井') }}</dt><dd>{{ rda.selectedMeasure.value.intercept_wells == null ? '—' : tr(String(rda.selectedMeasure.value.intercept_wells)) }}</dd>
          <dt>{{ tr('截流井溢流量') }}</dt><dd>{{ int(rda.selectedMeasure.value.overflow_m3) }} m³</dd>
          <template v-if="rda.selectedMeasure.value.tank_m3">
            <dt>{{ tr('池容') }}</dt><dd>{{ int(rda.selectedMeasure.value.tank_m3) }} m³</dd>
            <dt>{{ tr('有效水深 / 池底') }}</dt><dd>{{ f1(rda.selectedMeasure.value.effective_depth_m) }} m / {{ f2(rda.selectedMeasure.value.invert_m) }} m</dd>
            <dt>{{ tr('开挖深度') }}</dt><dd>{{ f1(rda.selectedMeasure.value.excavation_m) }} m</dd>
          </template>
          <template v-if="rda.selectedMeasure.value.pumps">
            <dt>{{ tr('泵') }}</dt><dd>{{ rda.selectedMeasure.value.pumps }} {{ tr('台') }} × {{ f2(rda.selectedMeasure.value.pump_q_m3s) }} m³/s</dd>
            <dt>{{ tr('设计流量') }}</dt><dd>{{ f2(rda.selectedMeasure.value.design_q_m3s) }} m³/s</dd>
            <dt>{{ tr('泵站场地') }}</dt><dd>{{ int(rda.selectedMeasure.value.station_area_m2) }} m²</dd>
          </template>
          <template v-if="rda.selectedMeasure.value.pipe_len_m">
            <dt>{{ rda.selectedMeasure.value.pipe_parallel ? tr('压力管') : tr('重力管') }}</dt>
            <dd>
              {{ int(rda.selectedMeasure.value.pipe_len_m) }} m · DN{{ dn(rda.selectedMeasure.value.pipe_dia_m) }}
              <template v-if="rda.selectedMeasure.value.pipe_parallel">× {{ rda.selectedMeasure.value.pipe_parallel }} {{ tr('根') }} · {{ f2(rda.selectedMeasure.value.velocity_ms) }} m/s</template>
            </dd>
          </template>
          <template v-if="rda.selectedMeasure.value.outfall"><dt>{{ tr('排口') }}</dt><dd>{{ rda.selectedMeasure.value.outfall }}</dd></template>
          <dt>{{ tr('造价依据') }}</dt><dd>{{ tr(rda.selectedMeasure.value.cost_basis) }}</dd>
          <template v-if="rda.selectedMeasure.value.vs_current"><dt>{{ tr('与当前方案相比') }}</dt><dd>{{ tr(rda.selectedMeasure.value.vs_current) }}</dd></template>
        </dl>
        <h4>{{ tr('本场模拟里的实际表现') }}</h4>
        <ul class="plain">
          <li v-for="p in rda.measureSim.value" :key="p['编号']">
            {{ tr(p['类型']) }} {{ p['编号'] }}：
            <template v-if="p['本场模拟_max_m3'] != null">{{ tr('池内最大蓄水') }} {{ int(p['本场模拟_max_m3']) }} m³（{{ f1(p['本场模拟_max_pct']) }} % {{ tr('满') }}）</template>
            <template v-if="p['本场模拟_vol_m3'] != null">
              {{ tr('累计处理') }} {{ int(p['本场模拟_vol_m3']) }} m³<template v-if="p['本场模拟_util_pct'] != null">，{{ tr('利用率') }} {{ f1(p['本场模拟_util_pct']) }} %</template>
            </template>
          </li>
          <li v-if="!rda.measureSim.value.length" class="muted">{{ tr('成果里没有这项措施的逐场表现。') }}</li>
        </ul>
        <h4>{{ tr('需审批 / 许可') }}</h4>
        <p class="muted">{{ trj(rda.selectedMeasure.value.approvals) }}</p>
        <template v-if="rda.selectedMeasure.value.missing_data">
          <h4>{{ tr('缺数据判不了') }}</h4><p class="muted">{{ trj(rda.selectedMeasure.value.missing_data) }}</p>
        </template>
        <template v-if="rda.compliance.value">
          <h4>
            {{ tr('WA-726 与空间约束检查') }}
            <small class="muted">
              {{ tr('共 {n} 项：', { n: rda.compliance.value.checks.length }) }}
              <template v-for="(n, k) in rda.compliance.value.tally" :key="k">{{ tallyWord(k) }} {{ n }}　</template>
            </small>
          </h4>
          <label class="small"><input v-model="rda.showAllChecks.value" type="checkbox" /> {{ tr('显示全部（默认只列需复核 / 缺数据）') }}</label>
          <ul class="plain checks">
            <li v-for="c in rda.shownChecks.value" :key="c.check" :class="c.result">
              <b>{{ tallyWord(c.result) }}</b> {{ tr(c.name) }}
              <small v-if="c.detail" class="muted"> — {{ tr(c.detail) }}</small>
              <small v-if="c.refs.length" class="muted"> [{{ c.refs.join(', ') }}]</small>
            </li>
          </ul>
        </template>
      </template>

      <template v-else>
        <h3>{{ tr('L2+suggested 推荐方案') }} · {{ tr('{n} 项措施', { n: rda.suggested.value.measures.length }) }}</h3>
        <p class="muted small">{{ tr(rda.suggested.value.note) }}</p>
        <div v-if="rda.costTotal.value" class="kpis">
          <div><b>{{ rda.costTotal.value['数量'] }}</b><span>{{ tr('新增措施（项）') }}</span></div>
          <div><b>{{ f1(rda.costTotal.value['占位造价 百万美元']) }}</b><span>{{ tr('占位造价 百万美元') }}</span></div>
          <div><b>{{ rda.overview.value?.new_outfalls }}</b><span>{{ tr('新建排海口') }}</span></div>
          <div v-for="s in rda.suggested.value.npv?.schemes.slice(1)" :key="s['方案']"><b>{{ f1(s['NPV 5% M USD']) }}</b><span>{{ tr('全寿命 NPV 5% 百万美元') }}</span></div>
        </div>
        <h4>{{ tr('按类型') }}</h4>
        <table class="tbl">
          <thead><tr><th>{{ tr('类型') }}</th><th>{{ tr('数量') }}</th><th>{{ tr('百万美元') }}</th><th>{{ tr('占比') }}</th></tr></thead>
          <tbody>
            <tr v-for="r in rda.measureTypes.value" :key="r['设施类型']" :title="tr(r['造价依据'])">
              <td>{{ tr(r['设施类型']) }}<div class="muted small">{{ tr(r['造价依据']) }}</div></td>
              <td>{{ r['数量'] }}</td><td>{{ f2(r['占位造价 百万美元']) }}</td><td>{{ f1(r['占比 %']) }}%</td>
            </tr>
          </tbody>
        </table>
        <h4>{{ tr('规划设备清单（{n} 台）', { n: devices.length }) }}</h4>
        <p class="muted small">{{ tr('点一行定位，并看所属措施的设计参数、怎么排水、合规检查。') }}</p>
        <span class="seg">
          <button :class="{ on: devKind === 'all' }" @click="devKind = 'all'">{{ tr('全部') }} {{ devices.length }}</button>
          <button v-for="k in DEVICE_KINDS" :key="k" :class="{ on: devKind === k }" @click="devKind = k">{{ devKindName(k) }} {{ devCounts[k] || 0 }}</button>
        </span>
        <div class="dev-scroll">
          <table class="tbl small-t dev-tbl">
            <thead>
              <tr>
                <th>{{ tr('编号') }}</th><th>{{ tr('设备类型') }}</th><th>{{ tr('所属措施') }}</th><th>{{ tr('热点') }}</th><th>{{ tr('关键参数') }}</th>
                <th>{{ tr('流量 L/s') }}</th><th>{{ tr('池容 m³') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in shownDevices" :key="`${d.kind}|${d.id}`" @click="pickDevice(d)">
                <td>{{ d.id }}</td><td><u class="sw" :style="{ background: DEV_COLOR[d.kind] }" />{{ devKindName(d.kind) }}</td><td>{{ d.lid }}</td><td>{{ d.hotspot }}</td>
                <td class="dev-detail">{{ d.detail }}</td><td>{{ int(d.flow) }}</td><td>{{ int(d.tank) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <template v-if="rda.suggested.value.compare?.length">
          <h4>{{ tr('方案对比（客户热点四项全过 / 全域 ≥0.30 m 积水面积）') }}</h4>
          <table class="tbl small-t">
            <thead><tr><th>{{ tr('降雨') }}</th><th>{{ tr('方案') }}</th><th>{{ tr('四项全过 / 11') }}</th><th>≥0.30 m ha</th><th>{{ tr('未过的热点') }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in rda.suggested.value.compare" :key="i">
                <td>{{ tr(r['降雨']) }}</td><td>{{ tr(r['方案']) }}</td><td>{{ r['客户热点四项全过（/11）'] }}</td><td>{{ f2(r['全域 ≥0.30 m 积水面积 ha']) }}</td>
                <td class="small">{{ r['未过的热点'] || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <template v-if="rda.suggested.value.diff?.length">
          <h4>{{ tr('与当前方案（按 96 mm 优化）的差异') }}</h4>
          <table class="tbl small-t">
            <thead><tr><th>{{ tr('变化') }}</th><th>{{ tr('编号') }}</th><th>{{ tr('类型') }}</th><th>{{ tr('热点') }}</th><th>{{ tr('造价 USD') }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in rda.suggested.value.diff" :key="i">
                <td>{{ tr(r['变化']) }}</td><td>{{ r['编号'] }}</td><td>{{ tr(r['类型']) }}</td><td>{{ r['服务热点'] }}</td><td>{{ r['造价 USD'] }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <h4 v-if="rda.suggested.value.npv">{{ tr('全寿命成本（NPV）') }}</h4>
        <table v-if="rda.suggested.value.npv" class="tbl small-t">
          <thead><tr><th>{{ tr('方案') }}</th><th>{{ tr('建设') }}</th><th>{{ tr('运维/年') }}</th><th>NPV 3%</th><th>5%</th><th>8%</th></tr></thead>
          <tbody>
            <tr v-for="s in rda.suggested.value.npv.schemes" :key="s['方案']">
              <td>{{ tr(s['方案']) }}</td><td>{{ f1(s['建设费 M USD']) }}</td><td>{{ f2(s['每年运维 M USD']) }}</td>
              <td>{{ f1(s['NPV 3% M USD']) }}</td><td>{{ f1(s['NPV 5% M USD']) }}</td><td>{{ f1(s['NPV 8% M USD']) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="rda.suggested.value.npv" class="muted small">{{ tr('单位：百万美元。造价分项：') }}
          <span v-for="b in rda.suggested.value.npv?.breakdown" :key="b.item" class="chip">{{ tr(b.item) }} {{ b.m_usd }}（{{ b.pct }}%）</span>
        </p>
        <h4>{{ tr('逐项措施（点一行定位并看设计参数 / 怎么排水 / 合规）') }}</h4>
        <table class="tbl small-t">
          <thead><tr><th>{{ tr('编号') }}</th><th>{{ tr('类型') }}</th><th>{{ tr('热点') }}</th><th>{{ tr('池容 m³') }}</th><th>{{ tr('流量 m³/s') }}</th><th>{{ tr('百万美元') }}</th></tr></thead>
          <tbody>
            <tr v-for="m in rda.suggested.value.measures" :key="m.id" @click="pickMeasure(m.id)">
              <td>{{ m.id }}</td><td>{{ tr(m.type) }}</td><td>{{ m.hotspot }}</td>
              <td>{{ int(m.tank_m3) }}</td><td>{{ m.design_q_m3s == null ? '—' : f2(m.design_q_m3s) }}</td><td>{{ musd(m.cost_usd) }}</td>
            </tr>
          </tbody>
        </table>
        <h4>{{ tr('地面取水口（{n}）', { n: rda.suggested.value.intakes.length }) }}</h4>
        <table class="tbl small-t">
          <thead><tr><th>{{ tr('取水口') }}</th><th>{{ tr('泵站') }}</th><th>{{ tr('热点') }}</th><th>{{ tr('类别') }}</th><th>{{ tr('坑底 m') }}</th><th>{{ tr('取水 m³/s') }}</th><th>{{ tr('到泵站 m') }}</th></tr></thead>
          <tbody>
            <tr v-for="r in rda.suggested.value.intakes" :key="r['取水口']" :title="tr(r['说明'])">
              <td>{{ r['取水口'] }}</td><td>{{ r['所属地面泵站'] }}</td><td>{{ r['服务热点'] }}</td><td>{{ tr(r['类别']) }}</td>
              <td>{{ f2(r['坑底高程 m']) }}</td><td>{{ f2(r['取水流量 m³/s']) }}</td><td>{{ int(r['到泵站重力管 m']) }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>
  </div>
</template>
