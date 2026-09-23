"""Evaluate the ETA model on the held-out time split.

Usage:
    python -m ml.evaluate_eta

Reports MAE and MAPE for the model and, for comparison, for the
historical-average estimate, overall and per task type. Aggregates come from
training rows only. The numbers show that the pipeline works on synthetic
data; they are not a claim about real-world accuracy.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import get_settings
from app.core.logging import setup_logging
from app.shared.eta_model import load_predictor
from ml.features import Matrix, evaluation_matrix, load_dataset, time_split


@dataclass(frozen=True)
class ErrorMetrics:
    rows: int
    mae: float
    mape: float


def error_metrics(actual: Sequence[float], predicted: Sequence[float]) -> ErrorMetrics:
    a = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    return ErrorMetrics(
        rows=len(a),
        mae=float(np.mean(np.abs(a - p))),
        mape=float(np.mean(np.abs(a - p) / a) * 100.0),
    )


def _historical(matrix: Matrix) -> np.ndarray:
    return np.array([
        h.historical_average_minutes(s.inp.target_quantity)
        for s, h in zip(matrix.samples, matrix.histories, strict=True)
    ])


def _line(label: str, m: ErrorMetrics) -> str:
    return f"  {label:<24} rows {m.rows:>5}   MAE {m.mae:7.1f} min   MAPE {m.mape:5.1f}%"


async def run() -> None:
    setup_logging("WARNING")
    settings = get_settings()
    predictor = load_predictor(settings.eta_model_path)
    if not predictor.available:
        print("No ETA model artifact. Run python -m ml.train_eta first.")
        return

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        split = time_split(await load_dataset(session))
    await engine.dispose()

    matrix = evaluation_matrix(split.train, split.test)
    if len(matrix.y) == 0:
        print("No held-out tasks to evaluate.")
        return
    model_pred = predictor.predict_matrix(matrix.x)
    hist_pred = _historical(matrix)

    print("\n" + "=" * 72)
    print("SAFE2GO ETA Evaluation Report")
    print("=" * 72)
    print(f"Model version: {predictor.model_version}")
    print(f"Train: shifts before {split.split_date.date()} ({len(split.train.tasks)} tasks)")
    print(f"Test:  shifts from {split.split_date.date()} ({len(split.test.tasks)} tasks)")
    print()
    print("Overall")
    print(_line("Random Forest", error_metrics(matrix.y, model_pred)))
    print(_line("Historical average", error_metrics(matrix.y, hist_pred)))
    print()
    print("Random Forest by task type")
    task_types = np.array([s.inp.task_type for s in matrix.samples])
    for task_type in sorted(set(task_types)):
        mask = task_types == task_type
        print(_line(task_type, error_metrics(matrix.y[mask], model_pred[mask])))
    print("=" * 72)
    print("Synthetic data: these figures show the pipeline works, not real-world accuracy.\n")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
