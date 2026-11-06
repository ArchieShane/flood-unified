<script setup>
/* Run history：筛选标签 + 表格 + 分页。标签与后端 tab 一一对应；「草稿」设计稿里没有，
   有草稿时才出现 —— 否则存下的草稿在页面上无处可找。
   行数据（雨型、防线、状态、分析、时间）固定英文，不跟界面语言；表头、标签、按钮仍随界面。
   data 由父组件按 lang=en 取回，这里的格式化也一律传 ROW_LANG。 */
import { computed } from 'vue'

import { t, tIn } from '../../i18n'
import { analysisLabel, avatarClass, fmtWhen, stateCode, statusLabel, stormLabel } from '../../utils/sim'

const props = defineProps({
  tab: { type: String, required: true },
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  page: { type: Number, default: 0 },
  pageSize: { type: Number, default: 10 },
})
defineEmits(['tab', 'open', 'new', 'delete', 'page'])

const ROW_LANG = 'en'
const pages = computed(() => Math.max(1, Math.ceil((props.data?.total || 0) / props.pageSize)))
const range = computed(() => {
  const total = props.data?.total || 0
  const from = total ? props.page * props.pageSize + 1 : 0
  return { from, to: Math.min(total, props.page * props.pageSize + (props.data?.items.length || 0)), total }
})

const TABS = ['all', 'running', 'done', 'queued', 'replays', 'mine']
const tabs = computed(() => (props.data?.counts?.drafts || props.tab === 'drafts' ? [...TABS, 'drafts'] : TABS))
const num = x => String(Number(x))
// 与后端 simulation.DELETABLE 一致：排队 / 在算的要先取消才能删
const DELETABLE = ['draft', 'done', 'failed', 'cancelled']
// 与后端 simulation.PROTECTED_OPERATORS 一致：AR 名下是交付成果，不给删
const PROTECTED_OPERATORS = ['AR']
const deletable = r => DELETABLE.includes(r.status) && !PROTECTED_OPERATORS.includes(r.created_by)
</script>

<template>
  <section class="sim-history">
    <div class="top">
      <div>
        <div class="sim-cap">{{ t('sim.history.title') }}</div>
        <div class="sim-chips">
          <button v-for="k in tabs" :key="k" class="sim-chip" :class="{ on: tab === k }" @click="$emit('tab', k)">
            {{ t(`sim.history.tabs.${k}`) }}<span v-if="k === 'drafts'" class="n">{{ data?.counts?.drafts }}</span>
          </button>
        </div>
      </div>
      <button class="sim-btn primary" @click="$emit('new')">＋ {{ t('sim.history.newSim') }}</button>
    </div>
    <table class="sim-table">
      <thead>
        <tr>
          <th v-for="c in ['rain', 'basis', 'extent', 'grid', 'status', 'analyses', 'start', 'end', 'by', 'actions']" :key="c">
            {{ t(`sim.history.cols.${c}`) }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in data?.items || []" :key="r.id">
          <td class="rain">{{ stormLabel(r, ROW_LANG) }}</td>
          <td>{{ r.defence_basis }}<div v-if="!r.states && r.variant_label" class="variant">{{ stateCode(r.variant_label) }}</div></td>
          <td>{{ r.extent }}</td>
          <td>{{ num(r.grid_m) }} m</td>
          <td :class="'st-' + r.status"><span class="sim-dot" />{{ statusLabel(r, ROW_LANG) }}</td>
          <td>
            <div v-if="r.analyses.length" class="tags">
              <span v-for="a in r.analyses" :key="a.kind + a.gap_hours" class="sim-tag" :class="a.kind">{{ analysisLabel(a, ROW_LANG) }}</span>
            </div>
            <span v-else>—</span>
          </td>
          <td>{{ fmtWhen(r.started_at, ROW_LANG) }}</td>
          <td>{{ fmtWhen(r.ended_at, ROW_LANG) }}<template v-if="r.ended_at && r.ended_is_estimate">{{ tIn(ROW_LANG, 'sim.history.est') }}</template></td>
          <td><span v-if="r.created_by" class="sim-avatar" :class="avatarClass(r.created_by)">{{ r.created_by }}</span></td>
          <td class="acts">
            <button class="sim-link" @click="$emit('open', r)">
              {{ r.status === 'draft' ? t('sim.history.edit') : t('sim.history.open') }}
            </button>
            <button v-if="deletable(r)" class="sim-link danger" @click="$emit('delete', r)">
              {{ t('sim.history.delete') }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="!loading && data && !data.items.length" class="sim-empty">{{ t('sim.history.empty') }}</p>
    <div v-if="data?.total" class="sim-pager">
      <span>{{ t('sim.history.pageInfo', range) }}</span>
      <button class="sim-link" :disabled="page <= 0" @click="$emit('page', page - 1)">{{ t('sim.history.prev') }}</button>
      <span class="n">{{ page + 1 }} / {{ pages }}</span>
      <button class="sim-link" :disabled="page >= pages - 1" @click="$emit('page', page + 1)">{{ t('sim.history.next') }}</button>
    </div>
  </section>
</template>
