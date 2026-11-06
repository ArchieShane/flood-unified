<script setup>
import { useToastStore } from '../stores/toast'

const toast = useToastStore()
</script>

<template>
  <!-- 消息里带少量 HTML（加粗、上标），外部文本由调用方 esc() 过 -->
  <div
    class="toast"
    :style="{ display: toast.visible ? 'block' : 'none', borderLeftColor: toast.color }"
    v-html="toast.message"
  />
</template>

<style>
/* 提示条两个页面共用，故不进主题样式表 */
.toast {
  position: fixed;
  left: 50%;
  bottom: 28px;
  transform: translateX(-50%);
  background: rgba(28, 28, 28, 0.96);
  outline: 1px solid rgba(255, 255, 255, 0.12);
  border-left: 3px solid #66c56b;
  color: #fff;
  font: 12px/17px "Helvetica Neue", -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  padding: 10px 14px;
  border-radius: 6px;
  z-index: 1000;
  display: none;
  max-width: 620px;
}
</style>
