"""聚合重算影响视图与质量复核接口。

放在通用资源路由之前注册：`/api/data_quality_rule/reviews` 这类固定段
不能被 `/api/{module}/{entry_id}` 的整数路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import service_for

router = APIRouter(prefix="/api", tags=["重算影响与复核"])


class ClaimPayload(BaseModel):
    aggregate_config_id: int = 1
    impact_ids: list[str] = Field(default_factory=list)
    batch_ids: list[str] = Field(default_factory=list)
    rule_ids: list[int] = Field(default_factory=list)
    reviewer: str | None = None


class RecomputePayload(BaseModel):
    region: str
    date: str
    input_batch_id: str
    current_value: Any
    previous_value: Any | None = None
    trigger: str = "迟到数据补写"
    operator: str = "值班员"
    changed_rows: int = 0


class ReviewClaimPayload(BaseModel):
    reviewer: str | None = None


class ReviewSubmitPayload(BaseModel):
    actual_value: Any = None
    verdict: str | None = None
    remark: str | None = None
    reviewer: str | None = None


def _agg_service():
    service = service_for("agg_builder")
    if service is None:  # pragma: no cover - 注册表固定存在
        raise HTTPException(status_code=404, detail="聚合表构建器未注册")
    return service


def _quality_service():
    service = service_for("data_quality_rule")
    if service is None:  # pragma: no cover - 注册表固定存在
        raise HTTPException(status_code=404, detail="数据质量规则模块未注册")
    return service


@router.get("/agg_builder/{entry_id}/impact", response_model=dict)
def get_recompute_impact(
    entry_id: int,
    region: str | None = Query(default=None),
    date: str | None = Query(default=None),
) -> dict[str, Any]:
    return _agg_service().recompute_impact(entry_id, region=region, date=date)


@router.post("/agg_builder/{entry_id}/impact/claim", response_model=dict)
def claim_recompute_impact(entry_id: int, payload: ClaimPayload) -> dict[str, Any]:
    return _agg_service().claim_impact_reviews(
        entry_id,
        impact_ids=payload.impact_ids,
        batch_ids=payload.batch_ids,
        rule_ids=payload.rule_ids,
        reviewer=payload.reviewer,
    )


@router.post("/agg_builder/{entry_id}/recompute", response_model=dict)
def register_recompute(entry_id: int, payload: RecomputePayload) -> dict[str, Any]:
    return _agg_service().register_recompute(
        entry_id,
        region=payload.region,
        date=payload.date,
        input_batch_id=payload.input_batch_id,
        current_value=payload.current_value,
        previous_value=payload.previous_value,
        trigger=payload.trigger,
        operator=payload.operator,
        changed_rows=payload.changed_rows,
    )


@router.get("/agg_builder/{entry_id}/partitions/history", response_model=dict)
def get_partition_history(
    entry_id: int,
    region: str = Query(...),
    date: str = Query(...),
    rule_id: int | None = Query(default=None),
) -> dict[str, Any]:
    # 先确认聚合配置存在，再回查分区版本历史。
    if _agg_service().get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"聚合配置 {entry_id} 不存在或已归档")
    return _quality_service().agg_partition_history(region, date, rule_id)


@router.get("/data_quality_rule/reviews", response_model=dict)
def list_quality_reviews(
    region: str | None = Query(default=None),
    date: str | None = Query(default=None),
    status: str | None = Query(default=None),
    valid: bool | None = Query(default=None),
    review_id: str | None = Query(default=None),
) -> dict[str, Any]:
    return _quality_service().list_agg_reviews(
        region=region, date=date, status=status, valid=valid, review_id=review_id)


@router.post("/data_quality_rule/reviews/claim", response_model=dict)
def claim_reviews_from_impact(payload: ClaimPayload) -> dict[str, Any]:
    """数据质量模块直接从影响视图领用分区和计算版本。"""
    return _agg_service().claim_impact_reviews(
        payload.aggregate_config_id,
        impact_ids=payload.impact_ids,
        batch_ids=payload.batch_ids,
        rule_ids=payload.rule_ids,
        reviewer=payload.reviewer,
    )


@router.get("/data_quality_rule/reviews/history", response_model=dict)
def get_quality_partition_history(
    region: str = Query(...),
    date: str = Query(...),
    rule_id: int | None = Query(default=None),
) -> dict[str, Any]:
    return _quality_service().agg_partition_history(region, date, rule_id)


@router.get("/data_quality_rule/reviews/{review_id}", response_model=dict)
def get_quality_review(review_id: str) -> dict[str, Any]:
    result = _quality_service().get_agg_review(review_id)
    if not result.get("ok", True):
        raise HTTPException(status_code=404, detail=result.get("message", "复核项不存在"))
    return result


@router.post("/data_quality_rule/reviews/{review_id}/claim", response_model=dict)
def claim_quality_review(review_id: str, payload: ReviewClaimPayload) -> dict[str, Any]:
    return _quality_service().claim_agg_review(review_id, payload.reviewer)


@router.post("/data_quality_rule/reviews/{review_id}/submit", response_model=dict)
def submit_quality_review(review_id: str, payload: ReviewSubmitPayload) -> dict[str, Any]:
    if payload.actual_value is None or str(payload.actual_value).strip() == "":
        return {"ok": False, "message": "实际核验值不能为空", "review": None}
    return _quality_service().submit_agg_review(
        review_id,
        actual_value=payload.actual_value,
        verdict=payload.verdict,
        remark=payload.remark,
        reviewer=payload.reviewer,
    )
