<script setup>
/* 图标（沿用原型的 20×20 线性图标口径）。 */
const ICONS = {
  calc: '<rect x="4" y="2.5" width="12" height="15" rx="1.5"/><line x1="6.5" y1="6" x2="13.5" y2="6"/><circle cx="7" cy="10" r=".6"/><circle cx="10" cy="10" r=".6"/><circle cx="13" cy="10" r=".6"/><circle cx="7" cy="13.5" r=".6"/><circle cx="10" cy="13.5" r=".6"/><circle cx="13" cy="13.5" r=".6"/>',
  orb: '<circle cx="10" cy="10" r="7"/><path d="M3 10h14M10 3c2 2 3 4.5 3 7s-1 5-3 7c-2-2-3-4.5-3-7s1-5 3-7z"/>',
  plus: '<path d="M10 4v12M4 10h12"/>',
  minus: '<path d="M4 10h12"/>',
  locate: '<circle cx="10" cy="10" r="5"/><path d="M10 2v3M10 15v3M2 10h3M15 10h3"/>',
  layers: '<path d="M10 3 17 7l-7 4-7-4z"/><path d="M3 11l7 4 7-4M3 15l7 4 7-4"/>',
  lock: '<rect x="4" y="9" width="12" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>',
  play: '<path d="M6 4l10 6-10 6z"/>',
  exp: '<path d="M10 3v10M6 9l4 4 4-4M4 16h12"/>',
  close: '<path d="M5 5l10 10M15 5L5 15"/>',
  info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v5M10 6.5v.5"/>',
  pipe: '<path d="M2 14h5l3-4h8"/><path d="M10 10V4"/><path d="M7 14v3"/><circle cx="10" cy="10" r="1.3"/><circle cx="7" cy="14" r="1.3"/>',
  // 仿真页左侧导航与向导
  grid: '<rect x="3" y="3" width="6" height="6" rx="1.2"/><rect x="11" y="3" width="6" height="6" rx="1.2"/><rect x="3" y="11" width="6" height="6" rx="1.2"/><rect x="11" y="11" width="6" height="6" rx="1.2"/>',
  sliders: '<path d="M3 5.5h14M3 10h14M3 14.5h14"/><circle cx="7" cy="5.5" r="1.6"/><circle cx="13" cy="10" r="1.6"/><circle cx="8.5" cy="14.5" r="1.6"/>',
  truck: '<path d="M2.5 5.5h9v7.5h-9zM11.5 8.5h3l2.5 2.5v2h-5.5z"/><circle cx="5.5" cy="14.5" r="1.5"/><circle cx="14" cy="14.5" r="1.5"/>',
  pulse: '<path d="M2 10h4l2-5 4 10 2-5h4"/>',
  sun: '<circle cx="10" cy="10" r="3"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/>',
  user: '<circle cx="10" cy="7" r="3.2"/><path d="M4 17c.8-3.2 3.1-4.8 6-4.8s5.2 1.6 6 4.8"/>',
  check: '<path d="M5.5 10.5l3 3 6-6.5"/>',
  back: '<path d="M12 5l-5 5 5 5"/>',
  compare: '<path d="M4 7h11M12 4l3 3-3 3M16 13H5M8 10l-3 3 3 3"/>',
  calendar: '<rect x="3.5" y="4.5" width="13" height="12" rx="1.5"/><path d="M3.5 8.5h13M7 3v3M13 3v3"/>',
  clock: '<circle cx="10" cy="10" r="7"/><path d="M10 6v4l2.5 2"/>',
  plusCircle: '<circle cx="10" cy="10" r="7"/><path d="M10 6.5v7M6.5 10h7"/>',
  chat: '<path d="M3 5.5A1.5 1.5 0 0 1 4.5 4h11A1.5 1.5 0 0 1 17 5.5v7A1.5 1.5 0 0 1 15.5 14H8l-4 3v-3H4.5A1.5 1.5 0 0 1 3 12.5z"/>',
}

const props = defineProps({
  name: { type: String, required: true },
  size: { type: Number, default: 14 },
  opacity: { type: Number, default: 1 },
})
</script>

<template>
  <i class="ic" :style="{ width: props.size + 'px', height: props.size + 'px', opacity: props.opacity }">
    <svg viewBox="0 0 20 20" v-html="ICONS[props.name]" />
  </i>
</template>
