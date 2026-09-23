"""Training catalog publisher (cloud tier).

Loads the training catalog from content files into cloud.training_module
and cloud.anomaly_training_map. Safe to run repeatedly: modules are
upserted and map rows use deterministic IDs.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.cloud.training import AnomalyTrainingMap, TrainingModule
from app.shared.content import TrainingCatalog, load_catalog

log = logging.getLogger("safe2go.training_catalog")

# Fixed namespace so map IDs stay stable across runs and tiers.
_MAP_ID_NAMESPACE = uuid.UUID("6f1d6c2e-3b0a-4f55-9d2e-2a7c1b9e5d40")


def map_id_for(source_type: str, type_value: str) -> str:
    return str(uuid.uuid5(_MAP_ID_NAMESPACE, f"{source_type}:{type_value}"))


async def publish_catalog(session: AsyncSession, catalog: TrainingCatalog | None = None) -> int:
    """Write the catalog to the cloud schema. Returns the number of modules."""
    catalog = catalog or load_catalog()

    for module in catalog.modules:
        values = module.model_dump()
        stmt = insert(TrainingModule).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TrainingModule.module_id],
            set_={k: v for k, v in values.items() if k != "module_id"},
        )
        await session.execute(stmt)

    await session.execute(delete(AnomalyTrainingMap))
    for entry in catalog.recommendation_map:
        session.add(AnomalyTrainingMap(
            map_id=map_id_for(entry.source_type, entry.type_value),
            source_type=entry.source_type,
            type_value=entry.type_value,
            recommended_module_id=entry.module_id,
        ))

    await session.flush()
    log.info(
        "Training catalog published",
        extra={"modules": len(catalog.modules), "map_entries": len(catalog.recommendation_map)},
    )
    return len(catalog.modules)
