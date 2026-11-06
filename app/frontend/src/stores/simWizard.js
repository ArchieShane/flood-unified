import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  createSimulation,
  estimateSimulation,
  fetchSimulation,
  fetchSimulationOptions,
  submitSimulation,
  updateSimulation,
} from '../api'
import { useSimUser } from '../composables/useSimUser'
import { LINE_ORDER } from '../utils/sim'

/* New simulation 向导的状态。四步共用一份表单，字段名与后端 SimulationForm 一一对应；
   只有模型起算时刻在界面上拆成日期 + 时间两格（UTC），提交时再拼回 model_start_utc。

   第 3 步防线的勾选规则（三张卡：line1 / line2_current / line2_planned，与协议一一对应）：
   - 选第 k 道：前 k−1 道整道全勾、锁定（included）；第 k 道默认全勾、可逐项取消；后面的卡灰掉。
     第二道规划 = 现状 + 规划工程视同建成，所以选它时第一道、第二道现状都算 included。
   - 「三道一起排」（queue_all_lines）已从界面删除：表单里恒为 false，旧草稿打开时也归零。 */
const FORM_KEYS = [
  'rain_source', 'storm_preset_key', 'replay_event_ref', 'depth_mm', 'duration_h',
  'temporal_pattern', 'peak_position_pct', 'spatial_distribution',
  'extent', 'grid_m', 'terrain', 'tide_level_m', 'tide_datum', 'tide_label', 'gate_close_above_m',
  'after_rain_mode', 'after_rain_max_h', 'baseflow_pct', 'baseflow_min_dia_mm', 'baseflow_note',
  'defence_line_key', 'queue_all_lines', 'resource_ids', 'compute_target',
]

/** 下一个 UTC 零点，作为新任务的默认起算时刻 */
function nextUtcMidnight() {
  const d = new Date()
  d.setUTCHours(24, 0, 0, 0)
  return d.toISOString()
}

const splitUtc = iso => {
  const s = new Date(iso).toISOString()
  return { date: s.slice(0, 10), time: s.slice(11, 16) }
}

export const useSimWizardStore = defineStore('simWizard', () => {
  const options = ref(null)
  const form = ref({})
  const startDate = ref('')
  const startTime = ref('00:00')
  const custom = ref(false)
  const step = ref(0)
  const draftId = ref(null)
  const estimate = ref(null)
  const busy = ref(false)

  const lines = computed(() => options.value?.defence_lines || [])
  const idsOf = key => (lines.value.find(l => l.key === key)?.resources || []).map(r => r.id)
  const upto = key => LINE_ORDER.slice(0, LINE_ORDER.indexOf(key) + 1)
  const preset = computed(() => options.value?.storm_presets.find(p => p.key === form.value.storm_preset_key))

  async function init(id) {
    busy.value = true
    try {
      options.value = await fetchSimulationOptions()
      step.value = 0
      estimate.value = null
      if (id) {
        const run = await fetchSimulation(id)
        draftId.value = run.id
        form.value = Object.fromEntries(FORM_KEYS.map(k => [k, run[k]]))
        form.value.resource_ids = run.resource_ids
        custom.value = run.rain_source === 'design_storm' && !run.storm_preset_key
        const s = splitUtc(run.model_start_utc || nextUtcMidnight())
        startDate.value = s.date
        startTime.value = s.time
      } else {
        draftId.value = null
        form.value = { ...options.value.defaults, replay_event_ref: null, depth_mm: null, duration_h: null,
                       compute_target: null }
        custom.value = false
        if (form.value.storm_preset_key) pickPreset(form.value.storm_preset_key)
        const s = splitUtc(nextUtcMidnight())
        startDate.value = s.date
        startTime.value = s.time
      }
      form.value.queue_all_lines = false
      numberise()
    } finally {
      busy.value = false
    }
  }

  /** 后端 numeric 列回来是数字或字符串两可，表单里统一成数字 */
  function numberise() {
    for (const k of ['depth_mm', 'duration_h', 'peak_position_pct', 'grid_m', 'tide_level_m', 'gate_close_above_m',
                     'after_rain_max_h', 'baseflow_pct']) {
      if (form.value[k] !== null && form.value[k] !== undefined) form.value[k] = Number(form.value[k])
    }
  }

  // ---- ① 降雨 ----
  function setSource(src) {
    form.value.rain_source = src
  }
  function pickPreset(key) {
    const p = options.value.storm_presets.find(x => x.key === key)
    custom.value = false
    form.value.storm_preset_key = key
    form.value.depth_mm = Number(p.exact_depth_mm ?? p.depth_mm)
    form.value.duration_h = Number(p.duration_h)
  }
  function pickCustom() {
    custom.value = true
    form.value.storm_preset_key = null
  }

  // ---- ③ 防线 ----
  function selectLine(key) {
    form.value.defence_line_key = key
    if (form.value.queue_all_lines) return
    form.value.resource_ids = upto(key).flatMap(idsOf)
  }
  function cardState(key) {
    const sel = form.value.defence_line_key
    if (key === sel) return 'on'
    if (form.value.queue_all_lines) return 'open'
    return LINE_ORDER.indexOf(key) < LINE_ORDER.indexOf(sel) ? 'included' : 'off'
  }
  function toggleResource(id, lineKey) {
    const st = cardState(lineKey)
    if (st !== 'on' && st !== 'open') return
    const set = new Set(form.value.resource_ids)
    set.has(id) ? set.delete(id) : set.add(id)
    form.value.resource_ids = [...set]
  }
  const runCount = computed(() => (form.value.queue_all_lines ? LINE_ORDER.length : 1))

  // ---- 校验与提交 ----
  const stepValid = computed(() => [
    form.value.rain_source === 'observed_replay'
      ? !!form.value.replay_event_ref?.trim()
      : form.value.rain_source === 'design_storm' &&
        (!!form.value.storm_preset_key || (form.value.depth_mm > 0 && form.value.duration_h > 0)),
    !!form.value.extent && form.value.grid_m > 0,
    !!form.value.defence_line_key,
    true,
  ])

  function payload() {
    const { user } = useSimUser()
    return {
      ...form.value,
      model_start_utc: startDate.value ? `${startDate.value}T${startTime.value || '00:00'}:00Z` : null,
      created_by: user.value,
    }
  }

  async function refreshEstimate() {
    estimate.value = await estimateSimulation(payload())
  }

  /** 存草稿，或存完直接运行。返回后端的任务详情（运行时带 queued_ids）。 */
  async function save({ run = false } = {}) {
    busy.value = true
    try {
      if (!draftId.value) {
        const r = await createSimulation(payload(), { submit: run })
        draftId.value = r.id
        return r
      }
      await updateSimulation(draftId.value, payload())
      return run ? await submitSimulation(draftId.value) : { id: draftId.value }
    } finally {
      busy.value = false
    }
  }

  return {
    options, form, startDate, startTime, custom, step, draftId, estimate, busy, lines, preset, runCount, stepValid,
    init, setSource, pickPreset, pickCustom, selectLine, cardState, toggleResource, refreshEstimate, save,
  }
})
