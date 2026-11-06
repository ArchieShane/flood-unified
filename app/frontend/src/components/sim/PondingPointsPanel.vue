<script setup>
/* 全市一二维任务（#1068 ADM 100 m 等）详情页底部的「积水点」明细：与 RD-A（`RdaPanels.vue`）同一个布局——
   标签栏 + 「积水点」标签（`PondingTab.vue`，两边共用同一个组件），只是全市没有 L3 / 设施 / 判据 / 措施，标签栏只有「积水点」一个。
   数据与选中项在 `usePondingPoints()`（对外的名字与 `useRda()` 一致）。点行时往外抛 `locate`，由外面的地图去飞。 */
import PondingTab from '../rda/PondingTab.vue'
import { tr } from '../../i18n/rda'
import '../../styles/rda.css'

const props = defineProps({
  pts: { type: Object, required: true },       // usePondingPoints() 返回的一束 ref
})
const emit = defineEmits(['locate'])
const R = props.pts
</script>

<template>
  <div class="rda-ui">
    <nav class="tabs">
      <button class="on">{{ tr('积水点') }} {{ R.ponding.value?.count ?? '' }}</button>
    </nav>
    <PondingTab :rda="pts" @locate="emit('locate', $event)" />
  </div>
</template>
