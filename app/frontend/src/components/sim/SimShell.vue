<script setup>
/* 仿真页外框：左侧 66px 图标栏 + 主区。图标栏的顺序照 Figma：
   首页（指挥中心）/ 仿真 / 车队 / 单点快算，底部是语言切换与当前操作人。 */
import AppIcon from '../AppIcon.vue'
import { useBodyTheme } from '../../composables/useBodyTheme'
import { setSimUser, useSimUser } from '../../composables/useSimUser'
import { LOCALES, locale, setLocale, t } from '../../i18n'
import '../../styles/simulation.css'

const props = defineProps({
  /** 可切换的操作人缩写（来自 /api/simulations/options 的 users） */
  users: { type: Array, default: () => [] },
})

useBodyTheme('theme-sim')
const { user } = useSimUser()

function nextLocale() {
  const i = LOCALES.findIndex(l => l.key === locale.value)
  setLocale(LOCALES[(i + 1) % LOCALES.length].key)
}

function nextUser() {
  const list = props.users.map(u => u.initials)
  if (!list.length) return
  setSimUser(list[(list.indexOf(user.value) + 1) % list.length])
}
</script>

<template>
  <div class="sim-shell">
    <nav class="sim-rail">
      <RouterLink class="logo" to="/simulations">CC</RouterLink>
      <RouterLink class="nav on" to="/simulations" :title="t('sim.nav.simulation')">
        <AppIcon name="sliders" :size="20" />
      </RouterLink>
      <RouterLink class="nav" to="/rda" title="RD-A 全套成果"><AppIcon name="pulse" :size="20" /></RouterLink>
      <a class="nav" href="/viewer/" title="推演成果（逐帧图）"><AppIcon name="grid" :size="20" /></a>
      <span class="nav off" :title="t('sim.nav.fleet')"><AppIcon name="truck" :size="20" /></span>
      <div class="spacer" />
      <button class="nav lang" :title="t('sim.nav.language')" @click="nextLocale">
        {{ LOCALES.find(l => l.key === locale).label }}
      </button>
      <button class="me" :title="t('sim.nav.user') + ' · ' + user" @click="nextUser">
        <AppIcon v-if="!users.length" name="user" :size="18" />
        <span v-else class="sim-avatar" :class="{ c1: user === 'AL' }">{{ user }}</span>
      </button>
    </nav>
    <main class="sim-main"><slot /></main>
  </div>
</template>
