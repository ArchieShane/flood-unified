import { onBeforeUnmount } from 'vue'

/** 给 <body> 挂上页面主题类。两套样式表都以它收口，互不串味。
 *
 *  ⚠ 必须在 setup 阶段同步加 —— 不能等 onMounted：子组件的 onMounted 比父组件早，
 *  Leaflet 初始化时会读 #map 的 computed position，读到 static 就自己写死一个内联
 *  `position:relative`，之后样式表里的 `position:fixed` 再也盖不过它，地图高度塌成 0。 */
export function useBodyTheme(cls) {
  document.body.classList.add(cls)
  onBeforeUnmount(() => document.body.classList.remove(cls))
}
