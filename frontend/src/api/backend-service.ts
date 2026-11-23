import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 聚合重算影响与质量复核 ----

export type ImpactRule = {
  rule_id: number
  rule_name: string
  dimension: string
  threshold: string
  coverage: string
  enabled: boolean
  reason?: string
}

export type ImpactItem = {
  impact_id: string
  batch_id: string
  compute_version: string
  computed_at: string
  trigger: string
  region: string
  partition_date: string
  partition: string
  previous_value: number
  current_value: number
  delta: number
  delta_percent: number
  changed_rows: number
  input: {
    input_batch_id: string
    source_snapshot: string
    source_rows: number
    changed_rows: number
    watermark: string
  }
  applicable_rules: ImpactRule[]
  skipped_rules: ImpactRule[]
  review_state: {
    review_ids: string[]
    valid_review_ids: string[]
    has_valid_review: boolean
  }
  version_state: {
    is_latest: boolean
    validity: string
    latest_version: string
    latest_value: number
    superseded_by_version: string | null
  }
}

export type ImpactBatch = {
  batch_id: string
  compute_version: string
  computed_at: string
  trigger: string
  operator: string
  input: ImpactItem['input']
  region_scope: string[]
  changed_partitions: ImpactItem[]
  item_count: number
  total_delta: number
  review_status: string
}

export type ImpactView = {
  ok: boolean
  aggregate_config_id: number
  aggregate_config_name: string | null
  aggregate_table: string
  output_field: string
  summary: Record<string, number>
  partitions_missing_valid_review: {
    region: string
    partition_date: string
    impact_id: string
    compute_version: string
    current_value: number
  }[]
  regions: string[]
  dates: string[]
  batches: ImpactBatch[]
  items: ImpactItem[]
}

export async function loadAggImpact(
  configId: number,
  filters: { region?: string; date?: string } = {},
): Promise<ImpactView> {
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value) as [string, string][],
  ).toString()
  const response = await request(`/api/agg_builder/${configId}/impact?${query}`)
  if (!response.ok) throw new Error('重算影响读取失败')
  return await response.json()
}

export type SubmitPolicy = {
  can_submit: boolean
  mode: 'current' | 'historical' | 'read_only'
  label: string
  reason: string
  computed_value: number
  latest_version: string
  latest_value: number
  latest_review_id: string | null
  allowed_submissions: { kind: string; label: string; hint?: string; options?: string[] }[]
}

export type Review = {
  review_id: string
  impact_id: string
  batch_id: string
  compute_version: string
  partition: string
  region: string
  partition_date: string
  rule_id: number
  rule_name: string
  computed_value: number
  previous_value: number
  delta: number
  input: ImpactItem['input']
  status: string
  claimed_by: string | null
  actual_value: number | string | null
  verdict: string | null
  remark: string | null
  submitted_at: string | null
  submit_mode: string | null
  conclusion: string | null
  valid: boolean
  validity: string
  latest_version: string
  latest_value: number
  superseded_by: string | null
  submit_policy: SubmitPolicy
  version_history: VersionEntry[]
}

export type VersionEntry = {
  compute_version: string
  batch_id: string
  computed_at: string
  trigger: string
  input: ImpactItem['input']
  output_value: number
  previous_value: number
  delta: number
  is_latest: boolean
  reviews: {
    review_id: string
    rule_id: number
    rule_name: string
    status: string
    valid: boolean
    actual_value: number | string | null
    verdict: string | null
    conclusion: string | null
    submitted_at: string | null
    submit_mode: string | null
  }[]
}

export type ReviewList = {
  ok: boolean
  items: Review[]
  total: number
  summary: Record<string, number>
}

export async function loadReviews(
  filters: { region?: string; date?: string; status?: string; valid?: string } = {},
): Promise<ReviewList> {
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, value]) => value !== '') as [string, string][],
  ).toString()
  const response = await request(`/api/data_quality_rule/reviews?${query}`)
  if (!response.ok) throw new Error('复核项读取失败')
  return await response.json()
}

export async function claimImpact(
  configId: number,
  payload: { impact_ids?: string[]; batch_ids?: string[]; rule_ids?: number[]; reviewer?: string },
): Promise<{ ok: boolean; message: string; reviews: Review[]; skipped_rules: ImpactRule[] }> {
  const response = await request(`/api/agg_builder/${configId}/impact/claim`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function claimReview(reviewId: string, reviewer?: string): Promise<{ ok: boolean; message: string; review: Review | null }> {
  const response = await request(`/api/data_quality_rule/reviews/${reviewId}/claim`, {
    method: 'POST',
    body: JSON.stringify({ reviewer }),
  })
  return await response.json()
}

/** 数据质量模块从影响视图领用分区和计算版本，为适用规则生成复核项。 */
export async function claimFromImpact(
  payload: { aggregate_config_id?: number; impact_ids?: string[]; rule_ids?: number[]; reviewer?: string },
): Promise<{ ok: boolean; message: string; reviews: Review[]; skipped_rules: ImpactRule[] }> {
  const response = await request('/api/data_quality_rule/reviews/claim', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function submitReview(
  reviewId: string,
  payload: { actual_value: string | number; verdict?: string; remark?: string; reviewer?: string },
): Promise<{ ok: boolean; message: string; review: Review | null }> {
  const response = await request(`/api/data_quality_rule/reviews/${reviewId}/submit`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

