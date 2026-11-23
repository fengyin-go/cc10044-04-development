import { createRouter, createWebHistory } from 'vue-router'

import ImpactView from '@/views/ImpactView.vue'
import Overview from '@/views/Overview.vue'
import QualityReviewView from '@/views/QualityReviewView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定子路径要排在 /:moduleKey 之前，避免被模块工作台吞掉。
    { path: '/agg_builder/impact', name: 'agg-impact', component: ImpactView },
    { path: '/data_quality_rule/reviews', name: 'quality-reviews', component: QualityReviewView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
