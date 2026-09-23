"""Train the ETA Random Forest.

Usage:
    python -m ml.train_eta

Steps:
  1. Load finished tasks and shift summaries from the cloud schema.
  2. Time-ordered split: the first 40 days train, the rest is held out.
  3. Fit RandomForestRegressor with the hyperparameters in thresholds.yaml
     on the training split and save the joblib artifact.
  4. Recompute deployment aggregates from all history and store them in
     cloud.eta_aggregate.

Training runs offline only, never at app startup. Run ml.evaluate_eta for
error metrics on the held-out split.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cloud.eta.service import replace_aggregates
from app.config.settings import get_settings
from app.config.thresholds import ETAModelConfig, get_thresholds
from app.core.logging import setup_logging
from app.shared.eta_aggregates import AggregateStats, aggregates_version
from app.shared.eta_model import build_reference, feature_names
from ml.features import Dataset, load_dataset, time_split, training_matrix

log = logging.getLogger("safe2go.train_eta")


def _model_version(x: np.ndarray, y: np.ndarray, params: ETAModelConfig) -> str:
    """Deterministic version: same data and hyperparameters give the same version."""
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(x).tobytes())
    digest.update(np.ascontiguousarray(y).tobytes())
    digest.update(params.model_dump_json().encode())
    return f"rf-{digest.hexdigest()[:12]}"


def train_model(train: Dataset, params: ETAModelConfig) -> dict[str, Any]:
    """Fit the model on the training split and return the artifact dict."""
    matrix = training_matrix(train)
    model = RandomForestRegressor(
        n_estimators=params.n_estimators,
        max_depth=params.max_depth,
        random_state=params.random_state,
        # Single-threaded so repeated runs give bit-identical predictions.
        n_jobs=1,
    )
    model.fit(matrix.x, matrix.y)
    train_aggregates = AggregateStats([t.history for t in train.tasks], train.shifts).to_rows()
    return {
        "model": model,
        "feature_names": feature_names(),
        "model_version": _model_version(matrix.x, matrix.y, params),
        "reference": build_reference([s.inp for s in matrix.samples], matrix.histories),
        "hyperparameters": params.model_dump(),
        "train_rows": len(matrix.y),
        "training_aggregates_version": aggregates_version(train_aggregates),
    }


def save_artifact(artifact: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, path)


async def run() -> None:
    setup_logging("INFO")
    settings = get_settings()
    params = get_thresholds().eta.model

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        data = await load_dataset(session)
        split = time_split(data)

        artifact = train_model(split.train, params)
        artifact["trained_before"] = split.split_date.isoformat()
        save_artifact(artifact, settings.eta_model_path)

        deployed_rows = AggregateStats([t.history for t in data.tasks], data.shifts).to_rows()
        agg_version = await replace_aggregates(session, deployed_rows)
        await session.commit()
    await engine.dispose()

    print(f"Model version:        {artifact['model_version']}")
    print(f"Training rows:        {artifact['train_rows']} (shifts before {split.split_date.date()})")
    print(f"Held-out rows:        {len(split.test.tasks)}")
    print(f"Artifact:             {settings.eta_model_path}")
    print(f"Deployed aggregates:  {len(deployed_rows)} rows, version {agg_version}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
