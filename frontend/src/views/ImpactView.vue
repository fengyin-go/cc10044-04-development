<template>
  <section class="page" data-testid="impact-view">
    <header class="page-head">
      <div>
        <h2>聚合重算影响</h2>
        <p class="page-desc">
          按计算批次归并发生变化的聚合分区，核对每轮输入和差额后，从变化项发起质量复核。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/data_quality_rule/reviews">前往质量复核台</RouterLink>
        <button class="btn" type="button" :disabled="loading" @click="reload">刷新影响视图</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>聚合配置</span>
        <select v-model.number="configId" data-testid="impact-config">
          <option v-for="config in configOptions" :key="config.id" :value="config.id">
            {{ config.配置名称 }}
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>地区</span>
        <select v-model="region" data-testid="impact-region">
          <option value="">全部地区</option>
          <option v-for="item in allRegions" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>分区日期</span>
        <select v-model="date" data-testid="impact-date">
          <option value="">全部日期</option>
          <option v-for="item in allDates" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn primary" data-testid="impact-query" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="message" class="success-text" data-testid="impact-message">{{ message }}</p>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">计算批次</span>
        <strong class="stat-value">{{ view?.summary.batch_count ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">变化聚合分区</span>
        <strong class="stat-value">{{ view?.summary.changed_partition_count ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">最新版本缺有效复核</span>
        <strong
          class="stat-value"
          :class="{ 'warn-value': (view?.summary.partitions_missing_valid_review ?? 0) > 0 }"
          data-testid="missing-valid-review"
        >
          {{ view?.summary.partitions_missing_valid_review ?? 0 }}
        </strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">历史版本项</span>
        <strong class="stat-value">{{ view?.summary.historical_item_count ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">累计差额</span>
        <strong class="stat-value">{{ view?.summary.total_delta ?? 0 }}</strong>
      </article>
    </div>

    <p v-if="view?.partitions_missing_valid_review.length" class="warning-banner" data-testid="missing-review-banner">
      以下聚合分区的最新计算版本还没有有效复核项：
      <span v-for="item in view.partitions_missing_valid_review" :key="item.impact_id" class="legend-item">
        {{ item.region }} / {{ item.partition_date }}（{{ item.compute_version }} 输出 {{ item.current_value }}）
      </span>
    </p>

    <article v-for="batch in view?.batches ?? []" :key="batch.batch_id" class="batch-card" :data-testid="`batch-${batch.batch_id}`">
      <header class="batch-head">
        <div>
          <h3>{{ batch.batch_id }} · {{ batch.compute_version }}</h3>
          <p class="page-desc">
            {{ batch.trigger }} · {{ batch.computed_at }} · 操作人 {{ batch.operator }}
          </p>
        </div>
        <span class="pill" :class="batch.review_status === '已复核' ? 'pill-ok' : 'pill-pending'">
          {{ batch.review_status }}
        </span>
      </header>

      <dl class="input-grid">
        <div><dt>输入批次</dt><dd>{{ batch.input.input_batch_id }}</dd></div>
        <div><dt>输入快照</dt><dd>{{ batch.input.source_snapshot }}</dd></div>
        <div><dt>输入行数</dt><dd>{{ batch.input.source_rows }}</dd></div>
        <div><dt>变化行数</dt><dd>{{ batch.input.changed_rows }}</dd></div>
        <div><dt>水位线</dt><dd>{{ batch.input.watermark }}</dd></div>
        <div><dt>变化分区</dt><dd>{{ batch.item_count }} 个 · 合计差额 {{ batch.total_delta }}</dd></div>
      </dl>

      <table class="data-table">
        <thead>
          <tr>
            <th>聚合分区</th>
            <th>上轮值</th>
            <th>本轮值</th>
            <th>差额</th>
            <th>变化行数</th>
            <th>版本状态</th>
            <th>适用质量规则</th>
            <th>复核项</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in batch.changed_partitions" :key="item.impact_id" :data-testid="`impact-${item.impact_id}`">
            <td>{{ item.partition }}</td>
            <td>{{ item.previous_value }}</td>
            <td>{{ item.current_value }}</td>
            <td>{{ item.delta }}（{{ item.delta_percent }}%）</td>
            <td>{{ item.changed_rows }}</td>
            <td>
              <span class="pill" :class="item.version_state.is_latest ? 'pill-ok' : 'pill-muted'">
                {{ item.version_state.validity }}
              </span>
            </td>
            <td>
              <span v-for="rule in item.applicable_rules" :key="rule.rule_id" class="pill pill-rule">
                {{ rule.rule_name }}
              </span>
              <span v-if="!item.applicable_rules.length" class="muted-text">无适用规则</span>
              <div v-for="rule in item.skipped_rules" :key="`skip-${rule.rule_id}`" class="skip-reason">
                跳过 {{ rule.rule_name }}：{{ rule.reason }}
              </div>
            </td>
            <td>
              <span v-if="item.review_state.has_valid_review" class="pill pill-ok">已有有效复核项</span>
              <span v-else-if="item.review_state.review_ids.length" class="pill pill-pending">仅历史版本复核项</span>
              <span v-else class="muted-text">未领取</span>
            </td>
            <td>
              <button
                class="link"
                type="button"
                :data-testid="`claim-${item.impact_id}`"
                :disabled="claiming === item.impact_id || !item.applicable_rules.length"
                @click="claim(item)"
              >
                {{ item.review_state.has_valid_review ? '领用已有复核项' : '发起质量复核' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>

    <p v-if="!loading && !view?.batches.length" class="empty-state">
      当前筛选条件下没有发生变化的聚合分区
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  claimFromImpact,
  listEntries,
  loadAggImpact,
  type ImpactItem,
  type ImpactView,
} from '@/api/backend-service'

const configId = ref(1)
const region = ref('')
const date = ref('')
const view = ref<ImpactView | null>(null)
const unfiltered = ref<ImpactView | null>(null)
const configOptions = ref<{ id: number; 配置名称: string }[]>([])
const loading = ref(false)
const claiming = ref('')
const errorMessage = ref('')
const message = ref('')

// 筛选选项取未过滤视图，避免选中一个地区后其他地区从下拉框消失。
const allRegions = computed(() => unfiltered.value?.regions ?? [])
const allDates = computed(() => unfiltered.value?.dates ?? [])

async function reload() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [filtered, full] = await Promise.all([
      loadAggImpact(configId.value, {
        region: region.value || undefined,
        date: date.value || undefined,
      }),
      region.value || date.value
        ? loadAggImpact(configId.value)
        : Promise.resolve(null),
    ])
    view.value = filtered
    if (!unfiltered.value || full) unfiltered.value = full ?? filtered
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重算影响读取失败'
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  region.value = ''
  date.value = ''
  void reload()
}

async function claim(item: ImpactItem) {
  claiming.value = item.impact_id
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await claimFromImpact({
      aggregate_config_id: configId.value,
      impact_ids: [item.impact_id],
      reviewer: '当前值班员',
    })
    if (!result.ok) throw new Error(result.message)
    const created = result.reviews.filter((review) => review.status !== '已复核')
    const target = created[0] ?? result.reviews[0]
    message.value = target
      ? `${result.message}：${target.review_id}（${target.validity}）。可到质量复核台提交核验值。`
      : result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领用复核项失败'
  } finally {
    claiming.value = ''
  }
}

onMounted(async () => {
  try {
    const payload = await listEntries('agg_builder', {})
    configOptions.value = payload.items.map((row) => ({
      id: Number(row.id),
      配置名称: String(row.配置名称 ?? `聚合配置 ${row.id}`),
    }))
    if (configOptions.value.length && !configOptions.value.some((item) => item.id === configId.value)) {
      configId.value = configOptions.value[0].id
    }
  } catch {
    configOptions.value = [{ id: 1, 配置名称: 'sales_by_region_daily' }]
  }
  await reload()
})
</script>
