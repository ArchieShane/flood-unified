import { createRouter, createWebHistory } from 'vue-router'

import { t } from '../i18n'

// 只保留仿真模块：/simulations 起是仿真任务（列表 + 新建向导），/rda 是 RD-A 全套成果。
const routes = [
  { path: '/', redirect: '/simulations' },
  {
    path: '/simulations',
    name: 'simulations',
    component: () => import('../views/SimulationHome.vue'),
    meta: { titleKey: 'sim.title' },
  },
  {
    path: '/simulations/new',
    name: 'simulation-new',
    component: () => import('../views/SimulationWizard.vue'),
    meta: { titleKey: 'sim.wizard.title' },
  },
  {
    path: '/simulations/:id(\\d+)/edit',
    name: 'simulation-edit',
    component: () => import('../views/SimulationWizard.vue'),
    props: true,
    meta: { titleKey: 'sim.wizard.title' },
  },
  {
    path: '/simulations/:id(\\d+)',
    name: 'simulation-detail',
    component: () => import('../views/SimulationDetail.vue'),
    props: true,
    meta: { titleKey: 'sim.title' },
  },
  {
    path: '/rda',
    name: 'rda',
    component: () => import('../views/RdaView.vue'),
    meta: { title: 'RD-A 全套成果' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/simulations' },
]

const router = createRouter({ history: createWebHistory(), routes })

// 标题跟着语言走：切换时 SimShell 会再设一次
router.afterEach(to => {
  if (to.meta.titleKey) document.title = t(to.meta.titleKey)
  else if (to.meta.title) document.title = to.meta.title
})

export default router
