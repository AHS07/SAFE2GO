"""Training recommendation service (edge tier).

Maps an incident type or behavior event type to a training module using
the edge copy of anomaly_training_map. At most one recommendation per
module per operator per shift: the database unique constraint enforces
it, and the insert skips duplicates instead of failing.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.edge.behavior import Recommendation
from app.db.models.edge.training import EdgeAnomalyTrainingMap, EdgeTrainingModule

log = logging.getLogger("safe2go.recommendations")


@dataclass(frozen=True)
class RecommendationOutcome:
    module_id: str
    module_title: str
    created: bool       # False when this module was already recommended this shift


async def mapped_module(
    session: AsyncSession, source_type: str, type_value: str
) -> EdgeTrainingModule | None:
    result = await session.execute(
        select(EdgeTrainingModule)
        .join(
            EdgeAnomalyTrainingMap,
            EdgeAnomalyTrainingMap.recommended_module_id == EdgeTrainingModule.module_id,
        )
        .where(EdgeAnomalyTrainingMap.source_type == source_type)
        .where(EdgeAnomalyTrainingMap.type_value == type_value)
    )
    return result.scalar_one_or_none()


async def recommend_for_source(
    session: AsyncSession,
    *,
    operator_id: str,
    shift_id: str,
    source_type: str,
    type_value: str,
    source_id: str,
    created_at: datetime,
) -> RecommendationOutcome | None:
    """Record a recommendation for the mapped module. None if the type is unmapped."""
    module = await mapped_module(session, source_type, type_value)
    if module is None:
        return None

    stmt = (
        insert(Recommendation)
        .values(
            recommendation_id=str(uuid.uuid4()),
            operator_id=operator_id,
            shift_id=shift_id,
            module_id=module.module_id,
            source_type=source_type,
            source_id=source_id,
            created_at=created_at,
        )
        .on_conflict_do_nothing(constraint="uq_recommendation_operator_shift_module")
        .returning(Recommendation.recommendation_id)
    )
    created = (await session.execute(stmt)).scalar_one_or_none() is not None

    if created:
        log.info(
            "Training recommended",
            extra={"operator_id": operator_id, "module_id": module.module_id, "source_type": source_type},
        )
    return RecommendationOutcome(module_id=module.module_id, module_title=module.title, created=created)


async def recommendations_for_shift(
    session: AsyncSession, operator_id: str, shift_id: str
) -> list[tuple[Recommendation, EdgeTrainingModule]]:
    result = await session.execute(
        select(Recommendation, EdgeTrainingModule)
        .join(EdgeTrainingModule, EdgeTrainingModule.module_id == Recommendation.module_id)
        .where(Recommendation.operator_id == operator_id)
        .where(Recommendation.shift_id == shift_id)
        .order_by(Recommendation.created_at.desc())
    )
    return [(rec, module) for rec, module in result.all()]
