<script setup>
/* 全市某一档的水量账：需要排的总积水量 → 这一档可以排多少 → 排完还剩多少（水深 ≥ 15 cm 的部分）。
   L2 / L2 sugg / L3 右侧各放一块，上一档的「排完剩余」就是下一档「需要排的总积水量」。纯展示，数字由调用方给（m³）。 */
import { t } from '../../i18n'
import { fmtInt } from '../../utils/sim'

defineProps({
  title: { type: String, default: '' },
  need: { type: Number, required: true },    // 需要排的总积水量 m³
  drain: { type: Number, required: true },   // 这一档可以排 m³
  left: { type: Number, required: true },    // 排完剩余 m³
  total: { type: Number, required: true },   // 全市总积水量 m³（占比按它算）
})
/** 这一档占总积水量的百分比：可以排 ÷ 总积水量（35% / 45% / 20%，最多留 1 位小数） */
const pctText = v => `${Number((Number(v) * 100).toFixed(1))}%`

const m3 = v => fmtInt(Math.round(Number(v)))
</script>

<template>
  <section class="cb">
    <h4>{{ title }}</h4>
    <div class="cb-row">
      <div>
        <span>{{ t('sim.balance.need') }}</span>
        <b>{{ m3(need) }}</b>
        <small>m³</small>
      </div>
      <i>→</i>
      <div class="drain">
        <span>{{ t('sim.balance.drain') }}</span>
        <b>{{ m3(drain) }}</b>
        <small>m³</small>
      </div>
      <i>→</i>
      <div>
        <span>{{ t('sim.balance.left') }}</span>
        <b>{{ m3(left) }}</b>
        <small>m³</small>
      </div>
    </div>
    <p class="cb-f">{{ t('sim.balance.fDrain', { total: m3(total), pct: pctText(drain / total), drain: m3(drain) }) }}</p>
    <p class="cb-f">{{ t('sim.balance.fLeft', { need: m3(need), drain: m3(drain), left: m3(left) }) }}</p>
  </section>
</template>

<style scoped>
.cb { background: rgba(255, 255, 255, 0.03); border-radius: 8px; padding: 12px 14px; margin: 0 0 10px; }
.cb h4 { font-size: 13px; font-weight: 600; margin: 0 0 8px; color: var(--fg-2, #c4c7cc); }
.cb-row { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr; gap: 6px; align-items: center; }
.cb-row > div { display: flex; flex-direction: column; min-width: 0; }
.cb-row span { font-size: 11.5px; color: var(--muted, #9aa0a6); }
.cb-row b { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
.cb-row small { font-size: 11px; color: var(--muted, #9aa0a6); font-variant-numeric: tabular-nums; }
.cb-row .drain b { color: var(--blue-lt, #6aa6ff); }
.cb-f { margin: 6px 0 0; font-size: 11.5px; color: var(--muted, #9aa0a6); font-variant-numeric: tabular-nums; }
.cb-row i { font-style: normal; color: var(--muted, #9aa0a6); }
</style>
