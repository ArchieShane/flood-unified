<script setup>
/* New simulation 向导（Figma：FM demo / 00a New simulation / step 1–4）。
   /simulations/new 新建；/simulations/:id/edit 续填草稿。存草稿与运行都在第 4 步。 */
import { computed, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'

import AppIcon from '../components/AppIcon.vue'
import SimShell from '../components/sim/SimShell.vue'
import StepConfirm from '../components/sim/StepConfirm.vue'
import StepExtent from '../components/sim/StepExtent.vue'
import StepLines from '../components/sim/StepLines.vue'
import StepRain from '../components/sim/StepRain.vue'
import { t } from '../i18n'
import { useSimWizardStore } from '../stores/simWizard'
import { useToastStore } from '../stores/toast'

const props = defineProps({ id: { type: String, default: null } })

const W = useSimWizardStore()
const router = useRouter()
const toast = useToastStore()

watch(
  () => props.id,
  id => {
    // 新建后存草稿会把地址换成 /:id/edit —— 那是同一份表单，不重载、不跳回第一步
    if (id && W.draftId === Number(id) && W.options) return
    W.init(id ? Number(id) : null).catch(e => toast.show(t('sim.wizard.loadFailed', { msg: e.message }), 'var(--red)'))
  },
  { immediate: true },
)

// 离开向导就作废这份表单，下次进来一律从库里重载（store 是全局的，不清会带着未存的改动回来）
onUnmounted(() => {
  W.options = null
})

const STEPS = [StepRain, StepExtent, StepLines, StepConfirm]
const steps = computed(() => t('sim.wizard.steps'))
const ready = computed(() => !!W.options && !!W.form.defence_line_key)

function go(i) {
  // 只能回到已完成的步骤，或前进到下一步（当前步通过校验时）
  if (i < W.step || (i === W.step + 1 && W.stepValid[W.step])) W.step = i
}

function next() {
  if (!W.stepValid[W.step]) {
    toast.show(t(W.form.rain_source === 'observed_replay' ? 'sim.wizard.needReplay' : 'sim.wizard.needStorm'), 'var(--amber)')
    return
  }
  W.step += 1
}

async function save(run) {
  try {
    const r = await W.save({ run })
    if (run) {
      toast.show(t('sim.wizard.queued', { n: (r.queued_ids || [r.id]).length }))
      router.push('/simulations')
    } else {
      toast.show(t('sim.wizard.saved', { id: r.id }))
      if (!props.id) router.replace(`/simulations/${r.id}/edit`)
    }
  } catch (e) {
    toast.show(e.message, 'var(--red)')
  }
}
</script>

<template>
  <SimShell :users="W.options?.users || []">
    <div class="wiz-head">
      <button class="wiz-back" :title="t('sim.title')" @click="router.push('/simulations')"><AppIcon name="back" :size="16" /></button>
      <h1 class="sim-h1">{{ id ? t('sim.wizard.editTitle', { id }) : t('sim.wizard.title') }}</h1>
      <span class="sim-tag draft">{{ t('sim.wizard.draft') }}</span>
    </div>

    <div v-if="ready" class="wiz-body">
      <div class="wiz-steps">
        <template v-for="(name, i) in steps" :key="i">
          <span v-if="i" class="wiz-rule" />
          <button class="wiz-step" :class="{ on: i === W.step, done: i < W.step }" @click="go(i)">
            <span class="n"><AppIcon v-if="i < W.step" name="check" :size="13" /><template v-else>{{ i + 1 }}</template></span>
            {{ name }}
          </button>
        </template>
      </div>

      <component :is="STEPS[W.step]">
        <template #foot>
          <div class="wiz-foot">
            <button class="sim-btn" @click="W.step -= 1">{{ t('sim.wizard.back') }}</button>
            <div class="r">
              <button class="sim-btn" :disabled="W.busy" @click="save(false)">{{ t('sim.wizard.saveDraft') }}</button>
              <button class="sim-btn primary" :disabled="W.busy" @click="save(true)">{{ t('sim.wizard.run') }}</button>
            </div>
          </div>
        </template>
      </component>

      <div v-if="W.step < 3" class="wiz-foot">
        <button v-if="W.step > 0" class="sim-btn" @click="W.step -= 1">{{ t('sim.wizard.back') }}</button>
        <div class="r"><button class="sim-btn primary" @click="next">{{ t('sim.wizard.next') }}</button></div>
      </div>
    </div>
  </SimShell>
</template>
