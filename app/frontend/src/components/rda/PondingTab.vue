<script setup>
/* 「积水点」标签：RD-A（`RdaPanels.vue`）与全市一二维任务（`PondingPointsPanel.vue`）共用这一份，内容和布局与 RD-A 原来的完全一致：
   积水点阈值 → 覆盖的客户热点（热点覆盖率 / 各状态并排 / 口径 / 圆内积水量与占比 / 热点表）→ 积水点列表与详情卡片。
   两边的数据层名字一致（`useRda()` / `usePondingPoints()`），传进来的 `rda`；全市多出的只有：网格说明、不在计算域内的热点、
   热点和积水点很多时的搜索 / 分页。点行时往外抛 `locate`，由外面的地图去飞；本组件不碰地图。 */
import { computed, ref, unref, watch } from 'vue'

import { DEPTH_CHOICES } from '../../composables/useRda'
import { tr, trj } from '../../i18n/rda'
import '../../styles/rda.css'

const props = defineProps({ rda: { type: Object, required: true } })
const emit = defineEmits(['locate'])
const R = props.rda   // useRda() / usePondingPoints() 返回的是一束 ref，模板里 .value 会自动解包

const f1 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 1 }))
const f2 = v => (v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }))
const int = v => (v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'))
const pct = v => (v == null ? '—' : `${Number(v).toFixed(1)}%`)
const at = h => (h == null ? '' : `@ ${Number(h).toFixed(2)} h`)

const LEVEL_COLOR = { 关键: '#d9434b', 高: '#e0702e', 中: '#e0a44e', 无暴露: '#7a838c' }
const outside = computed(() => unref(R.outsideDomain) || [])
const gridNote = computed(() => unref(R.gridNote) || '')
/** 积水点列表：按最大积水量（峰值积水量）降序，默认只出前 10 条，点「更多」展开其余。
    全市一份清单上千片，一次全展开页面会卡，所以超过 300 条时「更多」每次再出 100 条；300 条以内一次出完。 */
const PAGE = 10
const BIG = 300
const BIG_STEP = 100
const shown = ref(PAGE)
const sortedPoints = computed(() => [...R.visiblePoints.value].sort((a, b) => (b.vol_peak_m3 ?? -Infinity) - (a.vol_peak_m3 ?? -Infinity)))
const listPoints = computed(() => sortedPoints.value.slice(0, shown.value))
const restN = computed(() => Math.max(0, sortedPoints.value.length - shown.value))
function morePoints() {
  const total = sortedPoints.value.length
  shown.value = total <= BIG ? total : Math.min(total, shown.value + BIG_STEP)
}
// 换态 / 换阈值 / 换筛选后清单是新数组：回到前 10 条
watch(() => R.visiblePoints.value, () => { shown.value = PAGE })
// 在地图上点了排在后面的点：展开到它为止，详情卡片和列表里的高亮对得上
watch(() => R.selPoint.value, id => {
  const i = id ? sortedPoints.value.findIndex(p => p.id === id) : -1
  if (i >= shown.value) shown.value = i + 1
})

/** 热点表：RD-A 只有 11 行，全部列出；全市 470 行，加搜索并分批显示 */
const hotQ = ref('')
const hotN = ref(50)
const manyHot = computed(() => (R.hotspotCoverage.value?.rows.length || 0) > 30)
const hotRows = computed(() => {
  const rows = R.hotspotCoverage.value?.rows || []
  if (!manyHot.value) return rows
  const q = hotQ.value.trim().toLowerCase()
  return (q ? rows.filter(r => `${r.id} ${r.area || ''} ${r.location || ''}`.toLowerCase().includes(q)) : rows).slice(0, hotN.value)
})

/** 热点 200 m 圆内的地表峰值积水量 + 圆重叠（数据层的 `buf`，见 hotspotCoverage.js / 后端 hotspot_buffer.py） */
const bufData = computed(() => R.buf?.data.value || null)
const bufOf = id => R.buf?.byId.value[id] || null
const pairN = ref(10)
const pairRows = computed(() => (bufData.value?.pairs || []).slice(0, pairN.value))
const dupPct = computed(() => (bufData.value?.sum_vol_peak_m3 ? (bufData.value.double_count_m3 / bufData.value.sum_vol_peak_m3) * 100 : null))
const OVERLAP_CHIPS = 3

function pickPoint(id) {
  R.selPoint.value = id
  const p = (R.ponding.value?.points || []).find(x => x.id === id)
  if (p) emit('locate', { lat: p.lat, lon: p.lon, kind: 'point', id })
}
function pickHotspot(h) {
  // RD-A 的热点行没有坐标：照样抛出去（外面弹圆内积水趋势），地图只在有坐标时才飞
  emit('locate', { lat: h.lat, lon: h.lon, kind: 'hotspot', id: h.id })
}
defineExpose({ pickPoint })
</script>

<template>
    <div class="pane">
      <p v-if="gridNote" class="muted small">{{ tr(gridNote) }}</p>
      <div class="card thr">
        <h4>{{ tr('积水点阈值') }}</h4>
        <div class="thr-row">
          <label>{{ tr('水深 ≥') }}
            <select v-model.number="rda.pf.value.depth">
              <option v-for="d in DEPTH_CHOICES" :key="d" :value="d">{{ d.toFixed(2) }} m</option>
            </select>
          </label>
          <label>{{ tr('连片 ≥') }} <input v-model.number="rda.pf.value.area" type="number" min="0" step="50" /> m²</label>
          <label>{{ tr('持续 ≥') }} <input v-model.number="rda.pf.value.dur" type="number" min="0" step="0.25" /> h</label>
          <button class="go" :disabled="!rda.formDirty.value" @click="rda.applyThreshold()">{{ tr('重算') }}</button>
        </div>
        <p class="muted small">
          <template v-if="rda.ponding.value?.custom">{{ tr(rda.hasDelivered === false ? (rda.ponding.value.note || '已按所给阈值从二维结果重算。') : '已按所给阈值从二维结果重算：三角网共边连片，不扣地表塘占地，没有 L3 承接与评估剔除标记。') }}</template>
          <template v-else>{{ tr('现在是成果自带的清单（0.15 / 0.30 m，100 m²，0.5 h；含 L3 承接与剔除标记）。改任一阈值再点「重算」会从二维结果现算。') }}</template>
          <a v-if="!rda.isDelivered.value" href="#" @click.prevent="rda.presetDepth(0.15)">{{ rda.hasDelivered === false ? tr('回到默认') : tr('回到成果自带') }}</a>
        </p>
      </div>
      <div v-if="rda.hotspotCoverage.value" class="card">
        <h4>{{ tr('覆盖的客户热点') }} {{ rda.hotspotCoverage.value.covered }} / {{ rda.hotspotCoverage.value.total }}</h4>
        <div class="kpis">
          <div><b>{{ pct(rda.hotspotCoverage.value.rate) }}</b><span>{{ tr('热点覆盖率（被覆盖热点数 / {n}）', { n: rda.hotspotCoverage.value.total }) }}</span></div>
          <div v-if="bufData && bufData.union_pct != null">
            <b>{{ pct(bufData.union_pct) }}</b>
            <span>{{ tr('关注热点圆内积水量占全域（{a} m³ ÷ {b} m³；水深 ≥ {d} m）', {
              a: int(bufData.union.vol_peak_m3), b: int(bufData.domain.vol_peak_m3), d: Number(bufData.depth_m).toFixed(2) }) }}</span>
          </div>
          <div v-if="bufData && bufData.union_all_pct != null">
            <b>{{ pct(bufData.union_all_pct) }}</b>
            <span>{{ tr('不设阈值：{a} m³ ÷ 总积水量 {b} m³', { a: int(bufData.union_all.vol_peak_m3), b: int(bufData.domain_all.vol_peak_m3) }) }}</span>
          </div>
        </div>
        <table v-if="rda.coverageAll.value" class="tbl">
          <thead><tr><th>{{ tr('状态') }}</th><th>{{ tr('覆盖热点') }}</th><th>{{ tr('热点覆盖率') }}</th></tr></thead>
          <tbody>
            <tr v-for="c in rda.coverageAll.value" :key="c.key" :class="{ cur: c.key === rda.stateKey.value }">
              <td>{{ c.key }}</td>
              <td>{{ c.covered == null ? '—' : `${c.covered} / ${c.total}` }}</td>
              <td>{{ pct(c.rate) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted small">{{ tr('按当前状态、当前阈值算出的积水点，200 m 内有积水点即算覆盖该热点；未覆盖 = 这一态下热点周边已没有达标的积水点。点编号可定位。') }}</p>
        <p v-if="rda.hotspotCoverage.value.nExcluded" class="muted small">
          {{ tr('已不计入 {n} 片：一半以上面积落在评估剔除区内（列表里标「剔」），与客户重合分析文档同口径。', { n: rda.hotspotCoverage.value.nExcluded }) }}
        </p>
        <p v-if="outside.length" class="muted small">
          {{ tr('热点共 {n} 个（台账 ADM 全部）；其中 {m} 个不在二维计算域内，没有仿真结果，一律算未覆盖。', { n: rda.hotspotCoverage.value.total, m: outside.length }) }}
        </p>
        <details class="logic">
          <summary>{{ tr('覆盖是怎么算的（口径）') }}</summary>
          <ol>
            <li v-if="rda.hasDelivered !== false"><b>{{ tr('覆盖率') }}</b>：{{ tr('热点覆盖率 = 被覆盖的热点数 ÷ 热点总数（11），不含被剔除的片。上面的小表是四个状态在同一套阈值下的并排对比，当前状态高亮。') }}</li>
            <li v-else><b>{{ tr('覆盖率') }}</b>：{{ tr('热点覆盖率 = 被覆盖的热点数 ÷ 热点总数。上面的小表是各状态在同一套阈值下的并排对比，当前状态高亮。') }}</li>
            <li><b>{{ tr('积水点') }}</b>：{{ tr('当前状态的二维结果里，全程最大水深 ≥ 阈值的单元按三角网共边连成片，去掉面积不足连片下限、持续不足时长下限的片；阈值就是上面「积水点阈值」那一栏（成果自带 0.15 / 0.30 m、100 m²、0.5 h）。') }}</li>
            <li v-if="rda.hasDelivered !== false"><b>{{ tr('覆盖') }}</b>：{{ tr('客户热点（RD-A 交付的 11 个，只有坐标）到某个积水点的距离 ≤ 200 m，就算这个积水点覆盖了该热点；距离取热点到片内最近单元形心。同一热点可被几个点覆盖，表里按距离由近到远列出。') }}</li>
            <li v-else><b>{{ tr('覆盖') }}</b>：{{ tr('客户热点（交付的热点，只有坐标）到某个积水点的距离 ≤ 200 m，就算这个积水点覆盖了该热点；距离按客户文档量：热点到最近积水片边界的平面距离，热点落在积水片里为 0 m。同一热点可被几个点覆盖，表里按距离由近到远列出。') }}</li>
            <li><b>{{ tr('未覆盖') }}</b>：{{ tr('这一状态下热点 200 m 内已经没有达标的积水点，意思是该处不再积水或积得不够深、不够久，不是「没有算到」。') }}</li>
            <li v-if="rda.hasDelivered !== false"><b>{{ tr('不计入的片') }}</b>：{{ tr('一半以上面积落在评估剔除区内的积水片不计入覆盖（与客户重合分析文档同口径）。剔除区有两处：ADM-80 岸边约 9.7 ha 的 DTM 高程恰为 0.00 m（判为缺测填零），ADM-161 东侧私人地块的深坑。') }}</li>
            <li><b>{{ tr('不是什么') }}</b>：{{ tr('这只是空间位置的比对，不是模型验证：热点没有水深、范围和发生日期，模型也没有率定。「覆盖 x / 11」与「判据」页的 A / B / C 达标不是一回事，也不等于积水面积的削减比例。') }}</li>
          </ol>
        </details>
        <div v-if="rda.buf" class="buf thr">
          <h5>{{ tr('热点 200 m 圆内的地表峰值积水量') }}</h5>
          <p class="muted small">{{ tr('客户热点只有一个坐标、没有积水面，所以把以它为圆心、半径 200 m 的圆当作它的积水面，圆内按水深阈值取水。这一栏的阈值只管下面的圆内积水量，不影响上面的积水点和覆盖率。') }}</p>
          <div class="thr-row">
            <label>{{ tr('水深 ≥') }} <input v-model.number="rda.buf.bf.value" type="number" min="0.05" max="3" step="0.05" /> m</label>
            <button class="go" :disabled="!rda.buf.dirty.value" @click="rda.buf.apply()">{{ tr('重算') }}</button>
            <a v-if="rda.buf.bp.value !== 0.15" href="#" @click.prevent="rda.buf.reset()">{{ tr('回到默认') }}</a>
            <span v-if="rda.buf.loading.value" class="muted small">{{ tr('计算中…') }}</span>
          </div>
          <div class="formula">
            <b>{{ tr('积水量怎么算') }}</b>
            <code>V(t) = Σ<sub>i</sub> h<sub>i</sub>(t) × A<sub>i</sub>　　{{ tr('（只加 h ≥ 阈值 的单元 i）') }}</code>
            <code>{{ tr('热点的地表峰值积水量') }} = max<sub>t</sub> V(t)</code>
            <ol>
              <li>{{ tr('圆内单元：单元形心到热点 ≤ 200 m。圆面积 π × 200² ≈ {a} m²，当作这个热点的积水面。', { a: int(bufData?.circle_area_m2 ?? 125664) }) }}</li>
              <li>{{ tr('h(t) 是单元在第 t 帧的水深（m），A 是单元面积（m²）；水深低于阈值（当前 {d} m）的单元不加，避免薄水膜把量撑大。', { d: Number(rda.buf.bp.value).toFixed(2) }) }}</li>
              <li>{{ tr('V(t) 逐帧都算，取全部帧里最大的一个，所以每个热点的峰值时刻不同，是量级参考，不是某一时刻的水量。') }}</li>
              <li><b>{{ tr('重叠') }}</b>：{{ tr('两个热点相距小于 400 m 时圆相交，重叠区里的单元会被两个圆都数到，所以各热点的圆内积水量不能直接相加。总量取「所有圆的并集」：每个单元只算一次，再按同样的公式算。') }}</li>
              <li v-if="rda.hasExclusion !== false">{{ tr('落在评估剔除区内的单元不算（与积水点覆盖剔除多半在剔除区的片同口径）。') }}</li>
              <li>{{ tr('这是「圆内」的量，不是积水点的峰值积水量，两者不能比；圆内也可能有与热点无关的水（河道、洼地）。圆按单元形心取，边缘有取舍误差，网格越粗越明显。') }}</li>
            </ol>
          </div>
          <div v-if="bufData" class="kpis three">
            <div><b>{{ int(bufData.union.vol_peak_m3) }} m³</b><span>{{ tr('并集峰值积水量（每个单元只算一次）') }}<em> {{ at(bufData.union.t_peak_h) }}</em></span></div>
            <div><b>{{ int(bufData.sum_vol_peak_m3) }} m³</b><span>{{ tr('各热点圆内峰值直接相加') }}</span></div>
            <div><b>{{ int(bufData.double_count_m3) }} m³</b><span>{{ tr('重复算的量（相加比并集多出的，占相加的 {p}%）', { p: f1(dupPct) }) }}</span></div>
          </div>
          <template v-if="bufData && bufData.union_pct != null">
            <h5>{{ tr('关注热点的积水量占全域积水量的比例') }}</h5>
            <table class="tbl">
              <thead>
                <tr><th>{{ tr('口径') }}</th><th>{{ tr('分子：热点 200 m 圆内（并集）') }}</th><th>{{ tr('分母：全域') }}</th><th>{{ tr('占比') }}</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>{{ tr('水深 ≥ {d} m（跟着上面的阈值）', { d: Number(bufData.depth_m).toFixed(2) }) }}<small v-if="rda.hasExclusion !== false" class="tm">{{ tr('分子分母都扣评估剔除区') }}</small></td>
                  <td>{{ int(bufData.union.vol_peak_m3) }} m³<small class="tm">{{ at(bufData.union.t_peak_h) }} · {{ tr('{n} 个单元', { n: bufData.union.n_cells }) }}</small></td>
                  <td>{{ int(bufData.domain.vol_peak_m3) }} m³<small class="tm">{{ at(bufData.domain.t_peak_h) }} · {{ tr('{n} 个单元', { n: bufData.domain.n_cells }) }}</small></td>
                  <td><b>{{ pct(bufData.union_pct) }}</b></td>
                </tr>
                <tr>
                  <td>{{ tr('不设阈值（所有水深都加）') }}<small class="tm">{{ tr('不扣剔除区，分母 = 右侧总览的「总积水量」') }}</small></td>
                  <td>{{ int(bufData.union_all.vol_peak_m3) }} m³<small class="tm">{{ at(bufData.union_all.t_peak_h) }}</small></td>
                  <td>{{ int(bufData.domain_all.vol_peak_m3) }} m³<small class="tm">{{ at(bufData.domain_all.t_peak_h) }} · {{ tr('{n} 个单元', { n: bufData.domain_all.n_cells }) }}</small></td>
                  <td><b>{{ pct(bufData.union_all_pct) }}</b></td>
                </tr>
              </tbody>
            </table>
            <div class="formula">
              <code>{{ tr('分母') }} = max<sub>t</sub> Σ<sub>{{ tr('全域所有单元') }}</sub> h(t) × A　　{{ tr('（有阈值时只加 h ≥ 阈值 的单元）') }}</code>
              <code>{{ tr('分子') }} = max<sub>t</sub> Σ<sub>{{ tr('所有热点圆的并集') }}</sub> h(t) × A　　{{ tr('（同一口径，重叠的单元只算一次）') }}</code>
              <span>{{ tr('也就是说：分母是把整个计算域里每个单元每一帧的「水深 × 面积」加起来，取全部帧里最大的那一帧（出现在上表的时刻）；分子是同样的算法，只是范围缩到热点圆里。两者各取自己的峰值时刻。') }}</span>
            </div>
          </template>
          <p v-if="bufData" class="small">
            {{ tr('{a} / {n} 个热点的圆内有水（水深 ≥ {d} m）；{b} 个热点的圆与别的热点的圆重叠，共 {k} 对。', {
              a: bufData.n_with_water, n: bufData.n_hotspots + outside.length, d: Number(bufData.depth_m).toFixed(2), b: bufData.n_overlap_hotspots, k: bufData.n_pairs }) }}
            <template v-if="bufData.n_excluded_cells">{{ tr('评估剔除区内 {n} 个单元不计。', { n: bufData.n_excluded_cells }) }}</template>
          </p>
          <template v-if="bufData && bufData.pairs.length">
            <h5>{{ tr('哪些热点的 200 m 圆有重叠（按重叠面积从大到小）') }}</h5>
            <table class="tbl pairs">
              <thead>
                <tr>
                  <th>{{ tr('热点') }} A</th><th>{{ tr('热点') }} B</th><th>{{ tr('圆心距 m') }}</th><th>{{ tr('圆重叠面积 m²（占圆）') }}</th>
                  <th>{{ tr('重叠区单元面积 m²') }}</th><th>{{ tr('重叠区峰值积水量 m³') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in pairRows" :key="p.a + p.b">
                  <td>{{ p.a }}</td><td>{{ p.b }}</td><td>{{ int(p.dist_m) }}</td>
                  <td>{{ int(p.lens_area_m2) }}<small class="tm">{{ f1(p.lens_pct) }}%</small></td>
                  <td>{{ int(p.shared_area_m2) }}<small class="tm">{{ tr('{n} 个单元', { n: p.shared_cells }) }}</small></td>
                  <td>{{ int(p.shared_vol_peak_m3) }}<small v-if="p.shared_t_peak_h != null" class="tm">{{ at(p.shared_t_peak_h) }}</small></td>
                </tr>
              </tbody>
            </table>
            <p v-if="bufData.pairs.length > 10" class="small">
              <a href="#" @click.prevent="pairN = pairN > 10 ? 10 : bufData.pairs.length">
                {{ pairN > 10 ? tr('只看前 {n} 对', { n: 10 }) : tr('显示全部 {n} 对', { n: bufData.n_pairs }) }}
              </a>
            </p>
            <p class="muted small">{{ tr('圆重叠面积是两个圆的几何交集（解析式）；重叠区单元面积是落在两圆里的单元面积之和（按形心取，所以与几何面积略有出入）；重叠区峰值积水量 = 重叠区单元按上面公式算出的峰值，每对单独算，不能把各对相加。') }}</p>
          </template>
        </div>
        <input v-if="manyHot" v-model="hotQ" type="search" class="hot-q" :placeholder="tr('搜编号 / 片区 / 位置')" />
        <table class="tbl">
          <thead>
            <tr>
              <th>{{ tr('热点') }}</th><th>{{ tr('覆盖') }}</th><th>{{ tr('覆盖它的积水点（距离）') }}</th>
              <template v-if="rda.buf">
                <th>{{ tr('圆内峰值积水量 m³（时刻）') }}</th><th>{{ tr('圆内最大水深 m') }}</th><th>{{ tr('圆重叠') }}</th>
              </template>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in hotRows" :key="h.id" @click="pickHotspot(h)">
              <td :title="h.location">{{ h.id }}<span v-if="h.area" class="muted small"> · {{ h.area }}</span></td>
              <td><b :class="h.covered ? 'bad' : 'ok'">{{ h.covered ? tr('覆盖') : tr('未覆盖') }}</b></td>
              <td>
                <a v-for="x in h.points" :key="x.id" href="#" class="chip" @click.prevent="pickPoint(x.id)">{{ x.id.replace('SIM-', '') }} {{ x.dist_m }} m</a>
                <span v-if="!h.points.length" class="muted">—</span>
              </td>
              <template v-if="rda.buf">
                <td>
                  <template v-if="bufOf(h.id)">
                    {{ int(bufOf(h.id).vol_peak_m3) }}<small v-if="bufOf(h.id).vol_peak_m3 > 0" class="tm">{{ at(bufOf(h.id).t_peak_h) }}</small>
                    <small v-if="bufOf(h.id).excluded_area_m2" class="tm">{{ tr('剔除区 {n} m² 不计', { n: int(bufOf(h.id).excluded_area_m2) }) }}</small>
                  </template>
                  <span v-else class="muted">—</span>
                </td>
                <td>{{ bufOf(h.id) ? f2(bufOf(h.id).hmax_m) : '—' }}</td>
                <td>
                  <template v-if="bufOf(h.id)?.overlap_with.length">
                    <a v-for="o in bufOf(h.id).overlap_with.slice(0, OVERLAP_CHIPS)" :key="o" href="#" class="chip" @click.prevent="manyHot ? (hotQ = o) : null">{{ o }}</a>
                    <span v-if="bufOf(h.id).overlap_with.length > OVERLAP_CHIPS" class="muted small">+{{ bufOf(h.id).overlap_with.length - OVERLAP_CHIPS }}</span>
                    <small class="tm">{{ tr('圈内 {p}% 被别的圈盖住；只属于它自己的峰值 {v} m³', { p: f1(bufOf(h.id).shared_pct), v: int(bufOf(h.id).vol_exclusive_peak_m3) }) }}</small>
                  </template>
                  <span v-else class="muted">—</span>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="row">
        <span class="seg">
          <button :class="{ on: rda.pointFilter.value === 'all' }" @click="rda.pointFilter.value = 'all'">{{ tr('全部') }}</button>
          <button :class="{ on: rda.pointFilter.value === 'notRecede' }" @click="rda.pointFilter.value = 'notRecede'">{{ tr('未退') }}</button>
          <button v-if="!rda.ponding.value?.custom" :class="{ on: rda.pointFilter.value === 'l3' }" @click="rda.pointFilter.value = 'l3'">{{ tr('要 L3') }}</button>
          <button v-if="!rda.ponding.value?.custom" :class="{ on: rda.pointFilter.value === 'excluded' }" @click="rda.pointFilter.value = 'excluded'">{{ tr('剔除区') }}</button>
        </span>
        <span class="muted">
          {{ tr('{n} 片 · 峰值合计 {v} m³ · 未退 {k}', { n: rda.visiblePoints.value.length, v: int(rda.ponding.value?.peak_vol_sum_m3), k: rda.ponding.value?.not_receded }) }}
        </span>
      </div>
      <label class="small"><input v-model="rda.nearOnly.value" type="checkbox" /> {{ tr('只看客户热点 200 m 内') }}</label>
      <p class="muted small">{{ tr('编号 SIM-态-序号是模型算出的积水点，不是客户热点；列表按最大积水量降序，默认先看前 {n} 条，只在同一状态、同一阈值内有效。', { n: PAGE }) }}</p>
      <div v-if="rda.selectedPoint.value" class="card">
        <h4>
          {{ rda.selectedPoint.value.id }}
          <span v-if="rda.selectedPoint.value.l3" class="pill" :style="{ background: LEVEL_COLOR[rda.selectedPoint.value.l3.level] }">{{ tr(rda.selectedPoint.value.l3.level) }}</span>
          <span v-if="rda.selectedPoint.value.excluded" class="pill gray">{{ tr('评估剔除区') }}</span>
          <span v-else-if="rda.selectedPoint.value.mostly_excluded" class="pill gray">{{ tr('多半在剔除区') }}</span>
        </h4>
        <dl class="kv">
          <dt>{{ tr('最大水深') }}</dt><dd>{{ f2(rda.selectedPoint.value.hmax_m) }} m（{{ tr('地面高程') }} {{ f2(rda.selectedPoint.value.ground_z_m) }} m）<template v-if="rda.selectedPoint.value.t_hmax_h != null">，{{ tr('出现在') }} {{ f2(rda.selectedPoint.value.t_hmax_h) }} h</template></dd>
          <dt>{{ tr('面积') }}</dt><dd>{{ int(rda.selectedPoint.value.area_m2) }} m²（{{ tr('其中') }} ≥0.30 m：{{ int(rda.selectedPoint.value.area_ge030_m2) }} m²）</dd>
          <template v-if="rda.selectedPoint.value.area_peak_m2 != null">
            <dt>{{ tr('最大积水面积') }}</dt><dd>{{ int(rda.selectedPoint.value.area_peak_m2) }} m²，{{ tr('出现在') }} {{ f2(rda.selectedPoint.value.t_area_peak_h) }} h</dd>
          </template>
          <dt>{{ tr('峰值积水量') }}</dt><dd>{{ int(rda.selectedPoint.value.vol_peak_m3) }} m³，{{ tr('出现在') }} {{ f2(rda.selectedPoint.value.t_peak_h) }} h</dd>
          <dt>{{ tr('持续') }}</dt><dd>{{ f2(rda.selectedPoint.value.duration_h) }} h</dd>
          <dt>{{ tr('退水') }}</dt>
          <dd>
            <b :class="rda.selectedPoint.value.receded ? 'ok' : 'bad'">{{ rda.selectedPoint.value.receded ? tr('已退') : tr('未退') }}</b>
            <template v-if="rda.selectedPoint.value.receded"> · {{ tr('峰值后') }} {{ f2(rda.selectedPoint.value.recede_h) }} h</template>
            <template v-else> · {{ tr('到 {t} h 仍有 {v} m³', { t: rda.selectedPoint.value.t_end_h, v: int(rda.selectedPoint.value.vol_end_m3) }) }}</template>
          </dd>
          <dt>{{ tr('最近客户热点') }}</dt>
          <dd>{{ rda.selectedPoint.value.nearest_client_hotspot }}（{{ int(rda.selectedPoint.value.nearest_client_hotspot_dist_m) }} m）</dd>
          <dt>{{ tr('200 m 内热点') }}</dt>
          <dd>
            <template v-if="rda.selectedPoint.value.near_hotspots.length">
              <span v-for="h in rda.selectedPoint.value.near_hotspots" :key="h.hotspot" class="chip">{{ h.hotspot }} {{ h.dist_m }} m</span>
            </template>
            <template v-else>{{ tr('无') }}</template>
          </dd>
          <template v-if="rda.selectedPoint.value.excluded">
            <dt>{{ tr('剔除说明') }}</dt><dd>{{ trj(rda.selectedPoint.value.exclusion_note) }}（{{ tr('扣除后') }} {{ int(rda.selectedPoint.value.area_after_exclusion_m2) }} m²）</dd>
          </template>
          <dt>{{ tr('连片方式') }}</dt><dd>{{ rda.selectedPoint.value.adjacency === 'mesh' ? tr('三角网共边') : tr('单元近邻近似') }}</dd>
        </dl>
        <template v-if="rda.selectedPoint.value.l3">
          <h5>{{ tr('L3 承接') }}</h5>
          <dl class="kv">
            <dt>{{ tr('结论') }}</dt><dd>{{ tr(rda.selectedPoint.value.l3.verdict) }}</dd>
            <dt>{{ tr('定级依据') }}</dt><dd>{{ trj(rda.selectedPoint.value.l3.basis) }}</dd>
            <dt>{{ tr('L3 手段') }}</dt><dd>{{ tr(rda.selectedPoint.value.l3.measure) }}</dd>
            <dt>{{ tr('退干期限') }}</dt><dd>{{ f1(rda.selectedPoint.value.l3.deadline_h) }} h（{{ tr('自然退干') }} {{ rda.selectedPoint.value.l3.natural_drain_h ?? '—' }} h）</dd>
            <dt>{{ tr('300 m 内单位') }}</dt><dd>{{ rda.selectedPoint.value.l3.units_300m || '—' }}</dd>
            <dt>Island A13</dt>
            <dd>{{ tr(rda.selectedPoint.value.l3.island.result) }} · {{ rda.selectedPoint.value.l3.island.units ?? 0 }} {{ tr('台/辆') }} · {{ tr('到位') }} {{ rda.selectedPoint.value.l3.island.arrive_h ?? '—' }} h · {{ tr('完成') }} {{ rda.selectedPoint.value.l3.island.done_h ?? '—' }} h</dd>
            <dt>{{ tr('ADM 全驻地') }}</dt>
            <dd>{{ tr(rda.selectedPoint.value.l3.adm.result) }} · {{ rda.selectedPoint.value.l3.adm.units ?? 0 }} {{ tr('台/辆') }} · {{ tr('到位') }} {{ rda.selectedPoint.value.l3.adm.arrive_h ?? '—' }} h · {{ tr('完成') }} {{ rda.selectedPoint.value.l3.adm.done_h ?? '—' }} h</dd>
            <template v-if="rda.selectedPoint.value.l3.note"><dt>{{ tr('备注') }}</dt><dd>{{ tr(rda.selectedPoint.value.l3.note) }}</dd></template>
          </dl>
        </template>
      </div>
      <table class="tbl">
        <thead>
          <tr>
            <th>{{ tr('编号') }}</th><th>{{ tr('最大水深 m（时刻）') }}</th><th>{{ tr('最大面积 m²（时刻）') }}</th><th>{{ tr('最大积水量 m³（时刻）') }}</th><th>{{ tr('退水') }}</th>
            <th v-if="!rda.ponding.value?.custom">{{ tr('等级') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in listPoints" :key="p.id" :class="{ cur: p.id === rda.selPoint.value }" @click="pickPoint(p.id)">
            <td>{{ p.id.replace('SIM-', '') }}<i v-if="p.excluded || p.mostly_excluded" class="pill gray" :title="p.mostly_excluded ? tr('一半以上面积落在评估剔除区内，不计入热点覆盖') : ''">{{ tr('剔') }}</i></td>
            <td>{{ f2(p.hmax_m) }}<small class="tm">{{ at(p.t_hmax_h) }}</small></td>
            <td>{{ p.area_peak_m2 == null ? '—' : int(p.area_peak_m2) }}<small class="tm">{{ at(p.t_area_peak_h) }}</small></td>
            <td>{{ int(p.vol_peak_m3) }}<small class="tm">{{ at(p.t_peak_h) }}</small></td>
            <td :class="p.receded ? '' : 'bad'">{{ p.receded ? f1(p.recede_h) + ' h' : tr('未退') }}</td>
            <td v-if="!rda.ponding.value?.custom">
              <span v-if="p.l3" class="pill" :style="{ background: LEVEL_COLOR[p.l3.level] }">{{ tr(p.l3.level) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="restN > 0 || shown > PAGE" class="small more">
        <button v-if="restN > 0" class="go" @click="morePoints">{{ tr('更多（还有 {n} 条）', { n: restN }) }}</button>
        <a v-if="shown > PAGE" href="#" @click.prevent="shown = PAGE">{{ tr('收起，只看前 {n} 条', { n: PAGE }) }}</a>
      </p>
    </div>
</template>
