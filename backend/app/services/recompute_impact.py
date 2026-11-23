"""聚合重算影响与质量复核的共享领域状态。

聚合表构建器每次重算都会产生一个计算批次；同一地区、同一日期可能因为
迟到数据补写出现多个批次。本模块负责：

* 按计算批次归并发生变化的聚合分区，并保留每轮输入与差额；
* 按地区/日期给出最新版本，同时保留历史版本供回查；
* 根据质量规则的启停状态和覆盖地区判断规则是否适用；
* 保存质量复核项，区分“当前版本”与“历史版本”的提交能力。

路由层只做参数解析，真正的状态流转都留在这里和模块服务里。
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any

from app.store import store

AGG_TABLE = "dwd_orders_clean_agg_sum"
AGG_CONFIG_ID = 1
REGION_CODES = {"华北": "HB", "华东": "HD", "华南": "HN"}

# 计算批次是重算影响的归并单位：一个批次共享一份输入快照，
# 批次下面挂多个发生变化的聚合分区。
BATCH_DEFS: list[dict[str, Any]] = [
    {
        "batch_id": "CB-20261119-ALL-01",
        "compute_version": "v1",
        "aggregate_table": AGG_TABLE,
        "aggregate_config_id": AGG_CONFIG_ID,
        "computed_at": "2026-11-19T02:10:00Z",
        "trigger": "每日增量重算",
        "operator": "调度器",
        "input": {
            "input_batch_id": "SRC-20261119-0200",
            "source_snapshot": "ods_orders@2026-11-19T02:00:00Z",
            "source_rows": 1512000,
            "changed_rows": 1410000,
            "source_partitions": ["dt=2026-11-19"],
            "watermark": "2026-11-19T01:59:59Z",
        },
        "partitions": [
            {
                "impact_id": "IMP-20261119-ALL01-HB",
                "region": "华北",
                "partition_date": "2026-11-19",
                "previous_value": 104,
                "current_value": 112,
                "changed_rows": 470000,
                "output_field": "sales_amount",
            },
            {
                "impact_id": "IMP-20261119-ALL01-HD",
                "region": "华东",
                "partition_date": "2026-11-19",
                "previous_value": 76,
                "current_value": 80,
                "changed_rows": 420000,
                "output_field": "sales_amount",
            },
        ],
    },
    {
        "batch_id": "CB-20261120-ALL-01",
        "compute_version": "v1",
        "aggregate_table": AGG_TABLE,
        "aggregate_config_id": AGG_CONFIG_ID,
        "computed_at": "2026-11-20T02:10:00Z",
        "trigger": "每日增量重算",
        "operator": "调度器",
        "input": {
            "input_batch_id": "SRC-20261120-0200",
            "source_snapshot": "ods_orders@2026-11-20T02:00:00Z",
            "source_rows": 1520000,
            "changed_rows": 1450000,
            "source_partitions": ["dt=2026-11-20"],
            "watermark": "2026-11-20T01:59:59Z",
        },
        "partitions": [
            {
                "impact_id": "IMP-20261120-ALL01-HB",
                "region": "华北",
                "partition_date": "2026-11-20",
                "previous_value": 112,
                "current_value": 120,
                "changed_rows": 480000,
                "output_field": "sales_amount",
            },
            {
                "impact_id": "IMP-20261120-ALL01-HD",
                "region": "华东",
                "partition_date": "2026-11-20",
                "previous_value": 80,
                "current_value": 88,
                "changed_rows": 430000,
                "output_field": "sales_amount",
            },
            {
                "impact_id": "IMP-20261120-ALL01-HN",
                "region": "华南",
                "partition_date": "2026-11-20",
                "previous_value": 90,
                "current_value": 96,
                "changed_rows": 540000,
                "output_field": "sales_amount",
            },
        ],
    },
    {
        "batch_id": "CB-20261120-HB-02",
        "compute_version": "v2",
        "aggregate_table": AGG_TABLE,
        "aggregate_config_id": AGG_CONFIG_ID,
        "computed_at": "2026-11-20T14:20:00Z",
        "trigger": "迟到数据补写",
        "operator": "值班员-王敏",
        "input": {
            "input_batch_id": "SRC-20261120-1400",
            "source_snapshot": "ods_orders@2026-11-20T14:00:00Z",
            "source_rows": 1525000,
            "changed_rows": 12000,
            "source_partitions": ["dt=2026-11-20"],
            "watermark": "2026-11-20T13:59:59Z",
        },
        "partitions": [
            {
                "impact_id": "IMP-20261120-HB02-HB",
                "region": "华北",
                "partition_date": "2026-11-20",
                "previous_value": 120,
                "current_value": 130,
                "changed_rows": 12000,
                "output_field": "sales_amount",
            },
        ],
    },
]

IMPACT_ITEMS: list[dict[str, Any]] = []
BATCHES: list[dict[str, Any]] = []
REVIEWS: list[dict[str, Any]] = []


def _partition_name(region: str, partition_date: str) -> str:
    return f"region={region}/dt={partition_date}"


def _delta_percent(previous: Any, current: Any) -> float:
    try:
        previous_value = float(previous)
        if previous_value == 0:
            return 0.0
        return round((float(current) - previous_value) / abs(previous_value) * 100, 2)
    except (TypeError, ValueError):
        return 0.0


def _build_catalog() -> None:
    """把批次定义展开成可查询的批次、影响项两级结构。"""
    IMPACT_ITEMS.clear()
    BATCHES.clear()
    for definition in BATCH_DEFS:
        batch = {
            "batch_id": definition["batch_id"],
            "compute_version": definition["compute_version"],
            "aggregate_table": definition["aggregate_table"],
            "aggregate_config_id": definition["aggregate_config_id"],
            "computed_at": definition["computed_at"],
            "trigger": definition["trigger"],
            "operator": definition["operator"],
            "region_scope": [],
            "input": copy.deepcopy(definition["input"]),
            "changed_partitions": [],
        }
        for partition in definition["partitions"]:
            item = {
                "impact_id": partition["impact_id"],
                "batch_id": definition["batch_id"],
                "compute_version": definition["compute_version"],
                "aggregate_table": definition["aggregate_table"],
                "aggregate_config_id": definition["aggregate_config_id"],
                "computed_at": definition["computed_at"],
                "trigger": definition["trigger"],
                "region": partition["region"],
                "partition_date": partition["partition_date"],
                "partition": _partition_name(partition["region"], partition["partition_date"]),
                "output_field": partition["output_field"],
                "previous_value": partition["previous_value"],
                "current_value": partition["current_value"],
                "delta": round(float(partition["current_value"]) - float(partition["previous_value"]), 2),
                "delta_percent": _delta_percent(
                    partition["previous_value"], partition["current_value"]),
                "changed_rows": partition["changed_rows"],
                "input": copy.deepcopy(definition["input"]),
            }
            IMPACT_ITEMS.append(item)
            batch["changed_partitions"].append(item)
            if partition["region"] not in batch["region_scope"]:
                batch["region_scope"].append(partition["region"])
        BATCHES.append(batch)


_build_catalog()


def _split_regions(value: str | None) -> list[str]:
    return [part for part in re.split(r"[,，、\s]+", str(value or "")) if part]


def _rule_enabled(rule: dict[str, Any]) -> bool:
    return (
        rule.get("启用状态", "已启用") not in ("已停用", "停用")
        and rule.get("status") not in ("已停用", "停用", "已忽略")
    )


def evaluate_rules_for_region(region: str) -> dict[str, Any]:
    """判断聚合表的质量规则是否适用于某个地区。

    停用规则、以及覆盖地区不包含该地区的规则都不会触发。返回的
    ``skipped_rules`` 会带上可读原因，方便影响视图直接展示。
    """
    applicable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for rule in store.rows("data_quality_rule"):
        if rule.get("目标表") != AGG_TABLE:
            continue
        coverage = rule.get("覆盖地区") or rule.get("适用地区") or ""
        regions = _split_regions(coverage)
        summary = {
            "rule_id": rule["id"],
            "rule_name": rule.get("规则名称"),
            "dimension": rule.get("校验维度"),
            "threshold": rule.get("阈值"),
            "coverage": coverage or "全部地区",
            "enabled": _rule_enabled(rule),
        }
        if not _rule_enabled(rule):
            summary["reason"] = "规则已停用"
            skipped.append(summary)
            continue
        if regions and region not in regions and "全部" not in regions and "*" not in regions:
            summary["reason"] = f"规则仅覆盖{coverage}，不适用于{region}"
            skipped.append(summary)
            continue
        applicable.append(summary)
    return {"region": region, "applicable_rules": applicable, "skipped_rules": skipped}


def _impact_by_id(impact_id: str) -> dict[str, Any] | None:
    return next((item for item in IMPACT_ITEMS if item["impact_id"] == impact_id), None)


def _items_for_partition(region: str, partition_date: str) -> list[dict[str, Any]]:
    return sorted(
        (item for item in IMPACT_ITEMS
         if item["region"] == region and item["partition_date"] == partition_date),
        key=lambda item: item["computed_at"],
    )


def _latest_for_partition(region: str, partition_date: str) -> dict[str, Any] | None:
    items = _items_for_partition(region, partition_date)
    return items[-1] if items else None


def _review_by_id(review_id: str) -> dict[str, Any] | None:
    return next((item for item in REVIEWS if item["review_id"] == review_id), None)


def _reviews_for_impact(impact_id: str) -> list[dict[str, Any]]:
    return [review for review in REVIEWS if review["impact_id"] == impact_id]


def _reviews_for_partition(region: str, partition_date: str, rule_id: int | None = None) -> list[dict[str, Any]]:
    result = [
        review for review in REVIEWS
        if review["region"] == region
        and review["partition_date"] == partition_date
        and (rule_id is None or int(review["rule_id"]) == int(rule_id))
    ]
    return sorted(result, key=lambda review: (review["compute_version"], int(review["rule_id"])))


def _review_valid(review: dict[str, Any]) -> bool:
    """只有分区最新计算版本的复核项才是有效复核项。"""
    latest = _latest_for_partition(review["region"], review["partition_date"])
    return bool(latest and latest["batch_id"] == review["batch_id"] and review["status"] != "已失效")


def _latest_review_for(review: dict[str, Any]) -> dict[str, Any] | None:
    latest = _latest_for_partition(review["region"], review["partition_date"])
    if latest is None:
        return None
    return next(
        (candidate for candidate in _reviews_for_impact(latest["impact_id"])
         if int(candidate["rule_id"]) == int(review["rule_id"])),
        None,
    )


def _version_history(region: str, partition_date: str, rule_id: int | None = None) -> list[dict[str, Any]]:
    """同一地区、同一日期的所有计算版本，含输出值和质检结论。"""
    history: list[dict[str, Any]] = []
    for item in _items_for_partition(region, partition_date):
        related = [
            review for review in _reviews_for_impact(item["impact_id"])
            if rule_id is None or int(review["rule_id"]) == int(rule_id)
        ]
        latest = _latest_for_partition(region, partition_date)
        history.append({
            "compute_version": item["compute_version"],
            "batch_id": item["batch_id"],
            "computed_at": item["computed_at"],
            "trigger": item["trigger"],
            "input": copy.deepcopy(item["input"]),
            "output_value": item["current_value"],
            "previous_value": item["previous_value"],
            "delta": item["delta"],
            "is_latest": bool(latest and latest["impact_id"] == item["impact_id"]),
            "reviews": [
                {
                    "review_id": review["review_id"],
                    "rule_id": review["rule_id"],
                    "rule_name": review["rule_name"],
                    "status": review["status"],
                    "valid": _review_valid(review),
                    "actual_value": review.get("actual_value"),
                    "verdict": review.get("verdict"),
                    "conclusion": review.get("conclusion"),
                    "submitted_at": review.get("submitted_at"),
                    "submit_mode": review.get("submit_mode"),
                }
                for review in related
            ],
        })
    return history


def _submit_policy(review: dict[str, Any]) -> dict[str, Any]:
    latest = _latest_for_partition(review["region"], review["partition_date"])
    latest_value = latest["current_value"] if latest else review["computed_value"]
    latest_version = latest["compute_version"] if latest else review["compute_version"]
    latest_review = _latest_review_for(review)
    base = {
        "review_id": review["review_id"],
        "computed_value": review["computed_value"],
        "latest_version": latest_version,
        "latest_value": latest_value,
        "latest_review_id": latest_review["review_id"] if latest_review else None,
        "allowed_fields": ["actual_value", "verdict", "remark"],
        "verdict_options": ["一致", "偏差已确认", "数据异常", "已修复"],
    }
    if review["status"] == "已复核":
        return {
            **base,
            "can_submit": False,
            "mode": "read_only",
            "label": "复核已提交，可回查",
            "reason": "该复核项已经提交，输出值与质检结论保留在版本历史中",
            "allowed_submissions": [],
        }
    if _review_valid(review):
        return {
            **base,
            "can_submit": True,
            "mode": "current",
            "label": "提交当前版本复核结果",
            "reason": "该复核项对应分区最新计算版本",
            "allowed_submissions": [
                {"kind": "actual_value", "label": "实际核验值",
                 "hint": f"默认按批次输出 {review['computed_value']} 填写，现场核验为其他值时可如实填写"},
                {"kind": "verdict", "label": "复核结论", "options": base["verdict_options"]},
                {"kind": "remark", "label": "复核说明"},
            ],
        }
    return {
        **base,
        "can_submit": True,
        "mode": "historical",
        "label": "按历史版本补录复核结果",
        "reason": (
            f"分区已出现更新的计算版本 {latest_version}（输出 {latest_value}）；"
            f"仍可按历史版本 {review['compute_version']}（输出 {review['computed_value']}）提交，"
            "提交后标记为历史复核，不会覆盖当前版本"
        ),
        "allowed_submissions": [
            {"kind": "actual_value", "label": "该历史批次的实际核验值",
             "hint": f"按历史批次输出 {review['computed_value']} 补录，或填写现场实际核验值"},
            {"kind": "verdict", "label": "历史版本复核结论", "options": base["verdict_options"]},
            {"kind": "remark", "label": "补录说明"},
        ],
    }


def _review_view(review: dict[str, Any]) -> dict[str, Any]:
    view = copy.deepcopy(review)
    valid = _review_valid(review)
    latest = _latest_for_partition(review["region"], review["partition_date"])
    latest_review = _latest_review_for(review)
    view["valid"] = valid
    view["validity"] = "有效" if valid else "历史版本"
    view["latest_version"] = latest["compute_version"] if latest else review["compute_version"]
    view["latest_value"] = latest["current_value"] if latest else review["computed_value"]
    view["superseded_by"] = (
        latest_review["review_id"]
        if not valid and latest_review and latest_review["review_id"] != review["review_id"]
        else None
    )
    view["submit_policy"] = _submit_policy(review)
    view["version_history"] = _version_history(
        review["region"], review["partition_date"], int(review["rule_id"]))
    return view


def _review_state(impact: dict[str, Any]) -> dict[str, Any]:
    reviews = _reviews_for_impact(impact["impact_id"])
    valid_reviews = [review for review in reviews if _review_valid(review)]
    return {
        "review_ids": [review["review_id"] for review in reviews],
        "valid_review_ids": [review["review_id"] for review in valid_reviews],
        "review_statuses": [review["status"] for review in reviews],
        "has_valid_review": len(valid_reviews) > 0,
        "has_any_review": len(reviews) > 0,
    }


def _impact_view(impact: dict[str, Any]) -> dict[str, Any]:
    view = copy.deepcopy(impact)
    rule_evaluation = evaluate_rules_for_region(impact["region"])
    latest = _latest_for_partition(impact["region"], impact["partition_date"])
    is_latest = bool(latest and latest["impact_id"] == impact["impact_id"])
    view["batch"] = {
        "batch_id": impact["batch_id"],
        "compute_version": impact["compute_version"],
        "computed_at": impact["computed_at"],
        "trigger": impact["trigger"],
    }
    view["rule_evaluation"] = rule_evaluation
    view["applicable_rules"] = rule_evaluation["applicable_rules"]
    view["skipped_rules"] = rule_evaluation["skipped_rules"]
    view["review_state"] = _review_state(impact)
    view["version_state"] = {
        "is_latest": is_latest,
        "validity": "当前版本" if is_latest else "历史版本",
        "latest_version": latest["compute_version"] if latest else impact["compute_version"],
        "latest_value": latest["current_value"] if latest else impact["current_value"],
        "superseded_by_version": (
            latest["compute_version"] if not is_latest and latest else None
        ),
    }
    return view


def _batch_view(batch: dict[str, Any]) -> dict[str, Any]:
    items = [_impact_view(item) for item in batch["changed_partitions"]]
    reviews = [review for item in items for review in _reviews_for_impact(item["impact_id"])]
    statuses = {review["status"] for review in reviews}
    if not reviews:
        review_status = "待复核"
    elif statuses <= {"已复核"}:
        review_status = "已复核"
    elif "复核中" in statuses:
        review_status = "复核中"
    else:
        review_status = "待复核"
    return {
        "batch_id": batch["batch_id"],
        "compute_version": batch["compute_version"],
        "aggregate_table": batch["aggregate_table"],
        "aggregate_config_id": batch["aggregate_config_id"],
        "computed_at": batch["computed_at"],
        "trigger": batch["trigger"],
        "operator": batch["operator"],
        "input": copy.deepcopy(batch["input"]),
        "region_scope": list(batch["region_scope"]),
        "changed_partitions": items,
        "item_count": len(items),
        "total_delta": round(sum(float(item["delta"]) for item in items), 2),
        "review_status": review_status,
    }


def get_impact_view(
    *,
    aggregate_config_id: int | None = None,
    region: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """按批次归并的变化项视图，并附上地区/日期分组。"""
    selected_batches: list[dict[str, Any]] = []
    selected_items: list[dict[str, Any]] = []
    for batch in BATCHES:
        if aggregate_config_id is not None and int(batch["aggregate_config_id"]) != int(aggregate_config_id):
            continue
        matching = [
            item for item in batch["changed_partitions"]
            if (not region or item["region"] == region)
            and (not date or item["partition_date"] == date)
        ]
        if not matching:
            continue
        filtered_batch = copy.deepcopy(batch)
        filtered_batch["changed_partitions"] = matching
        selected_batches.append(filtered_batch)
        selected_items.extend(matching)

    selected_batches.sort(key=lambda batch: batch["computed_at"])
    selected_items.sort(key=lambda item: (item["partition_date"], item["computed_at"], item["region"]))
    items = [_impact_view(item) for item in selected_items]
    batches = [_batch_view(batch) for batch in selected_batches]

    all_reviews = [review for item in items for review in _reviews_for_impact(item["impact_id"])]
    region_counts: dict[str, int] = {}
    date_counts: dict[str, int] = {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        region_counts[item["region"]] = region_counts.get(item["region"], 0) + 1
        date_counts[item["partition_date"]] = date_counts.get(item["partition_date"], 0) + 1
        key = (item["region"], item["partition_date"])
        group = groups.setdefault(key, {
            "region": item["region"],
            "partition_date": item["partition_date"],
            "partition": item["partition"],
            "batch_ids": [],
            "changed_items": [],
            "latest_version": item["version_state"]["latest_version"],
            "latest_value": item["version_state"]["latest_value"],
            "review_state": {
                "review_ids": [],
                "valid_review_ids": [],
                "has_valid_review": False,
            },
        })
        if item["batch_id"] not in group["batch_ids"]:
            group["batch_ids"].append(item["batch_id"])
        group["changed_items"].append({
            "impact_id": item["impact_id"],
            "batch_id": item["batch_id"],
            "compute_version": item["compute_version"],
            "previous_value": item["previous_value"],
            "current_value": item["current_value"],
            "delta": item["delta"],
            "is_latest": item["version_state"]["is_latest"],
        })
        group["review_state"]["review_ids"].extend(item["review_state"]["review_ids"])
        group["review_state"]["valid_review_ids"].extend(item["review_state"]["valid_review_ids"])
        group["review_state"]["has_valid_review"] = (
            group["review_state"]["has_valid_review"]
            or item["review_state"]["has_valid_review"]
        )

    config = store.find("agg_builder", aggregate_config_id or AGG_CONFIG_ID)
    latest_items = [item for item in items if item["version_state"]["is_latest"]]
    missing_valid_review = [
        {
            "region": item["region"],
            "partition_date": item["partition_date"],
            "partition": item["partition"],
            "impact_id": item["impact_id"],
            "compute_version": item["compute_version"],
            "current_value": item["current_value"],
        }
        for item in latest_items
        if not item["review_state"]["has_valid_review"]
    ]
    return {
        "ok": True,
        "generated_at": _now(),
        "aggregate_config_id": aggregate_config_id or AGG_CONFIG_ID,
        "aggregate_config_name": config.get("配置名称") if config else None,
        "aggregate_table": AGG_TABLE,
        "output_field": "sales_amount",
        "filters": {"region": region, "date": date},
        "summary": {
            "batch_count": len(batches),
            "changed_partition_count": len(items),
            "region_count": len(region_counts),
            "date_count": len(date_counts),
            "total_delta": round(sum(float(item["delta"]) for item in items), 2),
            "valid_review_count": sum(1 for review in all_reviews if _review_valid(review)),
            "review_count": len(all_reviews),
            "pending_review_count": sum(1 for review in all_reviews if review["status"] != "已复核"),
            "historical_item_count": sum(1 for item in items if not item["version_state"]["is_latest"]),
            "latest_partition_count": len(latest_items),
            "partitions_missing_valid_review": len(missing_valid_review),
        },
        "partitions_missing_valid_review": missing_valid_review,
        "region_counts": region_counts,
        "date_counts": date_counts,
        "regions": sorted(region_counts),
        "dates": sorted(date_counts),
        "region_date_groups": list(groups.values()),
        "batches": batches,
        "items": items,
    }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_review(impact: dict[str, Any], rule: dict[str, Any], *, reviewer: str | None = None) -> dict[str, Any]:
    code = REGION_CODES.get(impact["region"], impact["region"])
    review_id = (
        f"QR-{impact['partition_date'].replace('-', '')}-{code}-"
        f"{impact['compute_version']}-R{rule['rule_id']}"
    )
    review = {
        "review_id": review_id,
        "impact_id": impact["impact_id"],
        "batch_id": impact["batch_id"],
        "compute_version": impact["compute_version"],
        "aggregate_table": impact["aggregate_table"],
        "aggregate_config_id": impact["aggregate_config_id"],
        "partition": impact["partition"],
        "region": impact["region"],
        "partition_date": impact["partition_date"],
        "rule_id": rule["rule_id"],
        "rule_name": rule["rule_name"],
        "dimension": rule.get("dimension"),
        "threshold": rule.get("threshold"),
        "computed_value": impact["current_value"],
        "previous_value": impact["previous_value"],
        "delta": impact["delta"],
        "output_field": impact["output_field"],
        "input": copy.deepcopy(impact["input"]),
        "status": "复核中" if reviewer else "待复核",
        "claimed_by": reviewer,
        "claimed_at": _now() if reviewer else None,
        "actual_value": None,
        "verdict": None,
        "remark": None,
        "submitted_at": None,
        "submit_mode": None,
        "conclusion": None,
    }
    REVIEWS.append(review)
    return review


def _seed_default_reviews() -> None:
    """预置华北当天首批（120）与补写批次（130）的复核项。

    首批已由值班员领用、正在复核；补写批次自动生成新的有效复核项。
    """
    for impact in IMPACT_ITEMS:
        if impact["region"] != "华北" or impact["partition_date"] != "2026-11-20":
            continue
        rules = evaluate_rules_for_region("华北")["applicable_rules"]
        primary = next((rule for rule in rules if int(rule["rule_id"]) == 5), None)
        if primary is None:
            continue
        if impact["compute_version"] == "v1":
            _new_review(impact, primary, reviewer="值班员-王敏")
        else:
            _new_review(impact, primary)


_seed_default_reviews()


def summarize_review_state(*, region: str, date: str) -> dict[str, Any]:
    """某个分区当前的有效/历史复核项概况，供补充重算后核对。"""
    reviews = _reviews_for_partition(region, date)
    latest = _latest_for_partition(region, date)
    return {
        "region": region,
        "partition_date": date,
        "latest_version": latest["compute_version"] if latest else None,
        "latest_value": latest["current_value"] if latest else None,
        "review_ids": [review["review_id"] for review in reviews],
        "valid_review_ids": [
            review["review_id"] for review in reviews if _review_valid(review)
        ],
        "historical_review_ids": [
            review["review_id"] for review in reviews if not _review_valid(review)
        ],
    }


def append_batch(
    *,
    region: str,
    partition_date: str,
    input_batch_id: str,
    current_value: Any,
    previous_value: Any | None = None,
    trigger: str = "迟到数据补写",
    operator: str = "值班员",
    changed_rows: int = 0,
    computed_at: str | None = None,
    aggregate_config_id: int = AGG_CONFIG_ID,
) -> dict[str, Any]:
    """登记一轮补充重算，并为适用规则生成新的有效复核项。

    旧版本的复核项不删除、不修改，只是从“有效”降级为“历史版本”，
    保证领到旧项的人仍能查到并补录。
    """
    items = _items_for_partition(region, partition_date)
    latest = items[-1] if items else None
    if previous_value is None:
        previous_value = latest["current_value"] if latest else current_value

    version_number = len(items) + 1
    date_code = partition_date.replace("-", "")
    code = REGION_CODES.get(region, region)
    definition = {
        "batch_id": f"CB-{date_code}-{code}-{version_number:02d}",
        "compute_version": f"v{version_number}",
        "aggregate_table": AGG_TABLE,
        "aggregate_config_id": aggregate_config_id,
        "computed_at": computed_at or _now(),
        "trigger": trigger,
        "operator": operator,
        "input": {
            "input_batch_id": input_batch_id,
            "source_snapshot": f"ods_orders@{input_batch_id}",
            "source_rows": (latest["input"]["source_rows"] if latest else 1520000) + changed_rows,
            "changed_rows": changed_rows,
            "source_partitions": [f"dt={partition_date}"],
            "watermark": input_batch_id,
        },
        "partitions": [{
            "impact_id": f"IMP-{date_code}-{code}{version_number:02d}-{code}",
            "region": region,
            "partition_date": partition_date,
            "previous_value": previous_value,
            "current_value": current_value,
            "changed_rows": changed_rows,
            "output_field": "sales_amount",
        }],
    }
    BATCH_DEFS.append(definition)
    _build_catalog()
    # 重建目录后 REVIEWS 里的对象仍指向旧的影响项字典，按业务键重新绑定。
    for review in REVIEWS:
        impact = _impact_by_id(review["impact_id"])
        if impact is not None:
            review["computed_value"] = impact["current_value"]
            review["previous_value"] = impact["previous_value"]
            review["delta"] = impact["delta"]
            review["input"] = copy.deepcopy(impact["input"])

    created = claim_reviews(impact_ids=[definition["partitions"][0]["impact_id"]],
                            reviewer=None)
    return {
        "ok": True,
        "batch_id": definition["batch_id"],
        "compute_version": definition["compute_version"],
        "region": region,
        "partition_date": partition_date,
        "previous_value": previous_value,
        "current_value": current_value,
        "review_summary": summarize_review_state(region=region, date=partition_date),
        "claim": created,
    }


def claim_reviews(
    *,
    impact_ids: list[str] | None = None,
    batch_ids: list[str] | None = None,
    rule_ids: list[int] | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    """从影响视图领用分区和计算版本，为适用规则生成复核项。

    不传任何筛选条件时只领用各分区的最新版本，避免值班员误领历史项；
    显式传入历史影响项时仍会生成历史复核项，但会提示其不是有效复核项。
    """
    requested_rules = {int(rule_id) for rule_id in (rule_ids or [])}
    unknown_ids: list[str] = []
    if impact_ids:
        selected = []
        for impact_id in impact_ids:
            impact = _impact_by_id(impact_id)
            if impact is None:
                unknown_ids.append(impact_id)
            else:
                selected.append(impact)
    elif batch_ids:
        known_batches = {batch["batch_id"] for batch in BATCHES}
        unknown_ids = [batch_id for batch_id in batch_ids if batch_id not in known_batches]
        selected = [item for item in IMPACT_ITEMS if item["batch_id"] in set(batch_ids)]
    else:
        latest_ids = {
            latest["impact_id"]
            for latest in (
                _latest_for_partition(item["region"], item["partition_date"])
                for item in IMPACT_ITEMS
            )
            if latest is not None
        }
        selected = [item for item in IMPACT_ITEMS if item["impact_id"] in latest_ids]

    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    skipped_rules: list[dict[str, Any]] = []
    for impact in selected:
        evaluation = evaluate_rules_for_region(impact["region"])
        skipped_rules.extend({
            "impact_id": impact["impact_id"],
            **skipped,
        } for skipped in evaluation["skipped_rules"])
        applicable = [
            rule for rule in evaluation["applicable_rules"]
            if not requested_rules or int(rule["rule_id"]) in requested_rules
        ]
        for rule in applicable:
            current = next(
                (review for review in _reviews_for_impact(impact["impact_id"])
                 if int(review["rule_id"]) == int(rule["rule_id"])),
                None,
            )
            if current is not None:
                existing.append(current)
                continue
            created.append(_new_review(impact, rule, reviewer=reviewer))

    views_created = [_review_view(review) for review in created]
    views_existing = [_review_view(review) for review in existing]
    historical_count = sum(1 for view in views_created if not view["valid"])
    message = f"已生成 {len(created)} 条复核项，复用 {len(existing)} 条"
    if historical_count:
        message += f"；其中 {historical_count} 条对应历史计算版本"
    if unknown_ids:
        message += f"；{len(unknown_ids)} 个影响项/批次不存在：{'、'.join(unknown_ids)}"
    if not created and not existing:
        message = "所选影响项没有适用的质量规则，未生成复核项"
        if unknown_ids:
            message = f"未找到影响项：{'、'.join(unknown_ids)}，请刷新影响视图后重试"
    return {
        "ok": not unknown_ids or bool(created or existing),
        "message": message,
        "created": views_created,
        "existing": views_existing,
        "reviews": views_created + views_existing,
        "created_count": len(created),
        "existing_count": len(existing),
        "historical_count": historical_count,
        "unknown_impact_ids": unknown_ids,
        "skipped_rules": skipped_rules,
    }


def list_reviews(
    *,
    region: str | None = None,
    date: str | None = None,
    status: str | None = None,
    valid: bool | None = None,
    review_id: str | None = None,
) -> dict[str, Any]:
    reviews = list(REVIEWS)
    if review_id:
        reviews = [review for review in reviews if review["review_id"] == review_id]
    if region:
        reviews = [review for review in reviews if review["region"] == region]
    if date:
        reviews = [review for review in reviews if review["partition_date"] == date]
    views = [_review_view(review) for review in reviews]
    if status:
        views = [view for view in views if view["status"] == status]
    if valid is not None:
        views = [view for view in views if bool(view["valid"]) is bool(valid)]
    views.sort(key=lambda view: (
        view["partition_date"], view["compute_version"], int(view["rule_id"])))
    return {
        "ok": True,
        "items": views,
        "total": len(views),
        "summary": {
            "total": len(views),
            "valid": sum(1 for view in views if view["valid"]),
            "historical": sum(1 for view in views if not view["valid"]),
            "pending": sum(1 for view in views if view["status"] == "待复核"),
            "reviewing": sum(1 for view in views if view["status"] == "复核中"),
            "submitted": sum(1 for view in views if view["status"] == "已复核"),
        },
    }


def get_review(review_id: str) -> dict[str, Any] | None:
    review = _review_by_id(review_id)
    if review is None:
        return None
    view = _review_view(review)
    view["related_reviews"] = [
        _review_view(item)
        for item in _reviews_for_partition(
            review["region"], review["partition_date"], int(review["rule_id"]))
    ]
    return view


def claim_review(review_id: str, reviewer: str | None = None) -> tuple[dict[str, Any] | None, str]:
    review = _review_by_id(review_id)
    if review is None:
        return None, f"复核项 {review_id} 不存在"
    if review["status"] == "已复核":
        return _review_view(review), "该复核项已提交，可回查版本历史"
    review["status"] = "复核中"
    review["claimed_by"] = reviewer or review.get("claimed_by") or "值班员"
    review["claimed_at"] = review.get("claimed_at") or _now()
    return _review_view(review), "复核项已领用"


def get_partition_history(region: str, partition_date: str, rule_id: int | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "region": region,
        "partition_date": partition_date,
        "partition": _partition_name(region, partition_date),
        "versions": _version_history(region, partition_date, rule_id),
        "reviews": [
            _review_view(review)
            for review in _reviews_for_partition(region, partition_date, rule_id)
        ],
    }


def submit_review(
    review_id: str,
    *,
    actual_value: Any,
    verdict: str | None = None,
    remark: str | None = None,
    reviewer: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    review = _review_by_id(review_id)
    if review is None:
        return None, f"复核项 {review_id} 不存在"
    policy = _submit_policy(review)
    if not policy["can_submit"]:
        return _review_view(review), policy["reason"]

    try:
        numeric_actual = float(actual_value)
        value_matches = abs(numeric_actual - float(review["computed_value"])) < 1e-9
        preserved_value: Any = (
            int(numeric_actual) if numeric_actual.is_integer() else numeric_actual
        )
    except (TypeError, ValueError):
        preserved_value = actual_value
        value_matches = False

    historical = policy["mode"] == "historical"
    final_verdict = verdict or ("一致" if value_matches else "偏差已确认")
    if historical:
        conclusion = (
            f"历史版本 {review['compute_version']} 复核完成：批次输出 {review['computed_value']}，"
            f"实际核验 {preserved_value}，结论：{final_verdict}；"
            f"当前最新版本 {policy['latest_version']} 输出 {policy['latest_value']}。"
        )
    else:
        conclusion = (
            f"当前版本 {review['compute_version']} 复核完成：批次输出 {review['computed_value']}，"
            f"实际核验 {preserved_value}，结论：{final_verdict}。"
        )

    review.update({
        "status": "已复核",
        "actual_value": preserved_value,
        "actual_value_matches_batch": value_matches,
        "verdict": final_verdict,
        "remark": remark,
        "reviewer": reviewer or review.get("claimed_by") or "值班员",
        "submitted_at": _now(),
        "submit_mode": policy["mode"],
        "conclusion": conclusion,
    })
    message = "历史版本复核结果已补录，当前版本复核项保持不变" if historical else "复核结果已提交"
    return get_review(review_id), message
