"""Weather generation for shifts.

weather_actual is the true condition used for telemetry generation.
weather_forecast is a noisy version used as an ETA feature — they differ
in 20-30% of shifts so the model learns to work with imperfect forecasts.
"""
from __future__ import annotations

import numpy as np

from app.shared.enums import WeatherCategory

# Visibility ranges (metres) and temperature ranges (Celsius) per category.
WEATHER_VISIBILITY: dict[WeatherCategory, tuple[float, float]] = {
    WeatherCategory.CLEAR: (400.0, 1000.0),
    WeatherCategory.CLOUDY: (200.0, 500.0),
    WeatherCategory.RAIN: (80.0, 150.0),
    WeatherCategory.FOG: (20.0, 90.0),
    WeatherCategory.STORM: (10.0, 50.0),
}

WEATHER_TEMP: dict[WeatherCategory, tuple[float, float]] = {
    WeatherCategory.CLEAR: (28.0, 42.0),
    WeatherCategory.CLOUDY: (22.0, 35.0),
    WeatherCategory.RAIN: (15.0, 28.0),
    WeatherCategory.FOG: (10.0, 22.0),
    WeatherCategory.STORM: (12.0, 25.0),
}

# Transition weights — makes weather realistic across a 50-day window.
_WEATHER_WEIGHTS = [0.40, 0.30, 0.15, 0.10, 0.05]
_ALL_CATEGORIES = [
    WeatherCategory.CLEAR,
    WeatherCategory.CLOUDY,
    WeatherCategory.RAIN,
    WeatherCategory.FOG,
    WeatherCategory.STORM,
]

# Adjacent-category forecast noise: the forecast is the actual category
# shifted one step in either direction for "wrong" forecasts.
_ADJACENT: dict[WeatherCategory, list[WeatherCategory]] = {
    WeatherCategory.CLEAR: [WeatherCategory.CLOUDY],
    WeatherCategory.CLOUDY: [WeatherCategory.CLEAR, WeatherCategory.RAIN],
    WeatherCategory.RAIN: [WeatherCategory.CLOUDY, WeatherCategory.FOG],
    WeatherCategory.FOG: [WeatherCategory.RAIN, WeatherCategory.STORM],
    WeatherCategory.STORM: [WeatherCategory.FOG, WeatherCategory.RAIN],
}


def sample_weather(rng: np.random.Generator, n: int) -> list[WeatherCategory]:
    """Sample n actual weather categories, roughly Markov-chained for realism."""
    cats: list[WeatherCategory] = []
    current = rng.choice(len(_ALL_CATEGORIES), p=_WEATHER_WEIGHTS)  # type: ignore[arg-type]
    for _ in range(n):
        cats.append(_ALL_CATEGORIES[current])
        # 80% chance of staying, 20% chance of moving to adjacent category.
        if rng.random() < 0.20:
            adjacent_idx = [_ALL_CATEGORIES.index(a) for a in _ADJACENT[_ALL_CATEGORIES[current]]]
            current = int(rng.choice(adjacent_idx))
    return cats


def make_forecast(actual: WeatherCategory, rng: np.random.Generator) -> WeatherCategory:
    """Return a forecast that differs from actual 20-30% of the time."""
    if rng.random() < 0.25:
        alternatives = _ADJACENT[actual]
        return alternatives[int(rng.integers(0, len(alternatives)))]
    return actual


def sample_visibility(category: WeatherCategory, rng: np.random.Generator) -> float:
    lo, hi = WEATHER_VISIBILITY[category]
    return float(rng.uniform(lo, hi))


def sample_temperature(category: WeatherCategory, rng: np.random.Generator) -> float:
    lo, hi = WEATHER_TEMP[category]
    return float(rng.uniform(lo, hi))
