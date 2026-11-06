import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 底部提示条。消息里允许少量 HTML（加粗、上标），调用方对外部文本自行 esc()。 */
export const useToastStore = defineStore('toast', () => {
  const message = ref('')
  const color = ref('var(--green)')
  const visible = ref(false)
  let timer = null

  function show(msg, c = 'var(--green)') {
    message.value = msg
    color.value = c
    visible.value = true
    clearTimeout(timer)
    timer = setTimeout(() => {
      visible.value = false
    }, 4200)
  }

  return { message, color, visible, show }
})
