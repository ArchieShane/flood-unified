<script setup>
import { watch } from 'vue'
import { useRoute } from 'vue-router'

import ToastHost from './components/ToastHost.vue'
import { locale, t } from './i18n'

// 标题两个来源都会变：换路由（router 的 afterEach）与换语言（这里）
const route = useRoute()
watch(locale, () => {
  if (route.meta.titleKey) document.title = t(route.meta.titleKey)
})
</script>

<template>
  <RouterView />
  <ToastHost />
</template>
