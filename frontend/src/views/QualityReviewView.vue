<template>
  <section class="page" data-testid="quality-review">
    <header class="page-head">
      <div>
        <h2>质量复核台</h2>
        <p class="page-desc">
          领用聚合重算影响视图中的分区和计算版本，提交复核时保留实际核验值；
          历史版本可回查、可补录，但不会覆盖当前有效复核项。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/agg_builder/impact">查看重算影响</RouterLink>
        <button class="btn" type="button" :disabled="loading" @click="reload">刷新复核项</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>地区</span>
        <select v-model="region" data-testid="review-region">
          <option value="">全部地区</option>
          <option v-for="item in regionOptions" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>分区日期</span>
        <input v-model="date" data-testid="review-date" placeholder="如 2026-11-20" />
      </label>
      <label class="filter-item">
        <span>复核状态</span>
        <select v-model="status" data-testid="review-status">
          <option value="">全部</option>
          <option value="待复核">待复核</option>
          <option value="复核中">复核中</option>
          <option value="已复核">已复核</option>
        </select>
      </label>
      <label class="filter-item">
        <span>版本有效性</span>
        <select v-model="valid" data-testid="review-valid">
          <option value="">全部</option>
          <option value="true">有效复核项</option>
          <option value="false">历史版本</option>
        </select>
      </label>
      <button class="btn primary" data-testid="review-query" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="message" class="success-text" data-testid="review-message">{{ message }}</p>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">复核项</span>
        <strong class="stat-value">{{ data?.summary.total ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">有效复核项</span>
        <strong class="stat-value" data-testid="valid-count">{{ data?.summary.valid ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">历史版本复核项</span>
        <strong class="stat-value">{{ data?.summary.historical ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待提交</span>
        <strong class="stat-value">
          {{ (data?.summary.pending ?? 0) + (data?.summary.reviewing ?? 0) }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已复核</span>
        <strong class="stat-value">{{ data?.summary.submitted ?? 0 }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>复核项</th>
          <th>聚合分区</th>
          <th>批次 / 版本</th>
          <th>批次输出</th>
          <th>触发</th>
          <th>状态</th>
          <th>版本有效性</th>
          <th>可提交内容</th>
          <th>实际核验值</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="review in data?.items ?? []" :key="review.review_id" :data-testid="`review-${review.review_id}`">
          <td>{{ review.review_id }}<div class="muted-text">{{ review.rule_name }}</div></td>
          <td>{{ review.partition }}</td>
          <td>{{ review.batch_id }}<div class="muted-text">{{ review.compute_version }}</div></td>
          <td>{{ review.computed_value }}</td>
          <td>{{ review.input.input_batch_id }}</td>
          <td>
            <span class="pill" :class="review.status === '已复核' ? 'pill-ok' : 'pill-pending'">
              {{ review.status }}
            </span>
          </td>
          <td>
            <span class="pill" :class="review.valid ? 'pill-ok' : 'pill-muted'">{{ review.validity }}</span>
            <div v-if="!review.valid" class="muted-text">
              最新 {{ review.latest_version }} 输出 {{ review.latest_value }}
            </div>
          </td>
          <td>
            <span class="muted-text">{{ review.submit_policy.label }}</span>
          </td>
          <td>
            <span v-if="review.actual_value !== null">{{ review.actual_value }}</span>
            <span v-else class="muted-text">未提交</span>
            <div v-if="review.verdict" class="muted-text">{{ review.verdict }}</div>
          </td>
          <td class="row-actions">
            <button
              v-if="review.status !== '已复核' && review.status === '待复核'"
              class="link"
              type="button"
              :data-testid="`claim-${review.review_id}`"
              @click="claim(review)"
            >
              领用
            </button>
            <button
              class="link"
              type="button"
              :data-testid="`open-${review.review_id}`"
              @click="openReview(review)"
            >
              {{ review.status === '已复核' ? '回查版本历史' : '提交复核' }}
            </button>
          </td>
        </tr>
        <tr v-if="!(data?.items.length)">
          <td colspan="10" class="empty-state">
            暂无复核项，请先在聚合重算影响视图中领用变化分区
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="active" class="modal-backdrop" data-testid="review-modal">
      <div class="modal">
        <h3>{{ active.review_id }}</h3>
        <p class="page-desc">{{ active.submit_policy.label }}</p>
        <p class="policy-reason" data-testid="review-policy">{{ active.submit_policy.reason }}</p>
        <ul v-if="active.submit_policy.allowed_submissions.length" class="policy-list" data-testid="allowed-submissions">
          <li v-for="entry in active.submit_policy.allowed_submissions" :key="entry.kind">
            {{ entry.label }}<span v-if="entry.hint" class="muted-text">：{{ entry.hint }}</span>
          </li>
        </ul>

        <dl class="input-grid">
          <div><dt>聚合分区</dt><dd>{{ active.partition }}</dd></div>
          <div><dt>计算版本</dt><dd>{{ active.compute_version }}（{{ active.batch_id }}）</dd></div>
          <div><dt>批次输出</dt><dd>{{ active.computed_value }}</dd></div>
          <div><dt>输入批次</dt><dd>{{ active.input.input_batch_id }}</dd></div>
          <div><dt>输入快照</dt><dd>{{ active.input.source_snapshot }}</dd></div>
          <div><dt>最新版本</dt><dd>{{ active.latest_version }} / {{ active.latest_value }}</dd></div>
        </dl>

        <form v-if="active.submit_policy.can_submit" @submit.prevent="submit">
          <label class="create-field">
            <span>实际核验值</span>
            <input v-model="actualValue" data-testid="actual-value" required />
          </label>
          <label class="create-field">
            <span>复核结论</span>
            <select v-model="verdict" data-testid="verdict">
              <option v-for="option in verdictOptions" :key="option" :value="option">{{ option }}</option>
            </select>
          </label>
          <label class="create-field">
            <span>复核说明</span>
            <input v-model="remark" data-testid="remark" placeholder="选填" />
          </label>
          <div class="page-actions">
            <button class="btn" type="button" @click="active = null">取消</button>
            <button class="btn primary" data-testid="submit-review" type="submit" :disabled="submitting">
              {{ submitting ? '提交中…' : '提交复核结果' }}
            </button>
          </div>
        </form>

        <section class="history-block">
          <h4>同分区各计算版本</h4>
          <table class="data-table">
            <thead>
              <tr>
                <th>版本</th><th>输入批次</th><th>上轮值</th><th>输出值</th><th>差额</th><th>质检结论</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="version in active.version_history" :key="version.batch_id">
                <td>
                  {{ version.compute_version }}
                  <span v-if="version.is_latest" class="pill pill-ok">当前</span>
                </td>
                <td>{{ version.input.input_batch_id }}</td>
                <td>{{ version.previous_value }}</td>
                <td>{{ version.output_value }}</td>
                <td>{{ version.delta }}</td>
                <td>
                  <div v-for="item in version.reviews" :key="item.review_id">
                    {{ item.status }} · 实际 {{ item.actual_value ?? '—' }} · {{ item.verdict ?? '—' }}
                    <div class="muted-text">{{ item.conclusion }}</div>
                  </div>
                  <span v-if="!version.reviews.length" class="muted-text">未生成复核项</span>
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <div class="page-actions">
          <button class="btn" type="button" @click="active = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  claimReview,
  loadReviews,
  submitReview,
  type Review,
  type ReviewList,
} from '@/api/backend-service'

const region = ref('')
const date = ref('')
const status = ref('')
const valid = ref('')
const data = ref<ReviewList | null>(null)
// 地区下拉用未过滤清单，避免选中某地区后其他地区消失。
const allReviews = ref<Review[]>([])
const loading = ref(false)
const submitting = ref(false)
const errorMessage = ref('')
const message = ref('')
const active = ref<Review | null>(null)
const actualValue = ref('')
const verdict = ref('一致')
const remark = ref('')

const verdictOptions = computed(
  () => active.value?.submit_policy.allowed_submissions
    .find((item) => item.kind === 'verdict')?.options ?? ['一致', '偏差已确认', '数据异常', '已修复'],
)

const regionOptions = computed(() => {
  const seen = new Set<string>()
  for (const review of allReviews.value) seen.add(review.region)
  return [...seen].sort()
})

async function reload() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [filtered, full] = await Promise.all([
      loadReviews({
        region: region.value || undefined,
        date: date.value || undefined,
        status: status.value || undefined,
        valid: valid.value || undefined,
      }),
      region.value || date.value || status.value || valid.value
        ? loadReviews()
        : Promise.resolve(null),
    ])
    data.value = filtered
    if (!allReviews.value.length || full) allReviews.value = (full ?? filtered).items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核项读取失败'
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  region.value = ''
  date.value = ''
  status.value = ''
  valid.value = ''
  void reload()
}

async function claim(review: Review) {
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await claimReview(review.review_id, '当前值班员')
    if (!result.ok) throw new Error(result.message)
    message.value = `${review.review_id} ${result.message}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领用失败'
  }
}

function openReview(review: Review) {
  active.value = review
  actualValue.value = String(review.actual_value ?? review.computed_value)
  verdict.value = review.verdict ?? '一致'
  remark.value = review.remark ?? ''
  message.value = ''
}

async function submit() {
  if (!active.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    const result = await submitReview(active.value.review_id, {
      actual_value: actualValue.value,
      verdict: verdict.value,
      remark: remark.value,
      reviewer: '当前值班员',
    })
    if (!result.ok) throw new Error(result.message)
    message.value = `${result.message}：${result.review?.conclusion ?? ''}`
    active.value = result.review
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核提交失败'
  } finally {
    submitting.value = false
  }
}

onMounted(reload)
</script>
