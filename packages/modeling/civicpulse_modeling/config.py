"""Typed modeling configuration (configs/modeling.yaml)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from civicpulse_shared.config import REPO_ROOT

DEFAULT_MODELING_CONFIG = REPO_ROOT / "configs" / "modeling.yaml"


class Period(BaseModel):
    start: dt.date
    end: dt.date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.end is not None and self.end < self.start:
            raise ValueError("period end must be on/after start")
        return self


class ForecastConfig(BaseModel):
    horizon_days: int = Field(ge=1, le=28)
    interval_level: float = Field(gt=0.5, lt=1.0)
    rolling_window_days: int = Field(ge=7)
    origin_step_days: int = Field(ge=1)
    min_history_days: int = Field(ge=28)
    min_conformal_residuals: int = Field(ge=5)
    validation: Period
    test: Period
    lgbm: dict[str, Any]

    @model_validator(mode="after")
    def _periods(self) -> ForecastConfig:
        if self.validation.end is None or self.validation.end >= self.test.start:
            raise ValueError("forecast validation must end before test starts")
        return self


class DelayRiskConfig(BaseModel):
    threshold_days: int = Field(ge=1)
    embargo_days: int = Field(ge=0)
    train: Period
    validation: Period
    test: Period
    min_category_count: int = Field(ge=1)
    permutation_sample: int = Field(ge=100)
    permutation_repeats: int = Field(ge=1)
    lgbm: dict[str, Any]
    logreg: dict[str, Any]

    @model_validator(mode="after")
    def _periods(self) -> DelayRiskConfig:
        if self.train.end is None or self.validation.end is None:
            raise ValueError("train and validation periods need explicit ends")
        if not (self.train.end < self.validation.start and self.validation.end < self.test.start):
            raise ValueError("delay-risk periods must be chronological and non-overlapping")
        return self


class AnomalyEvalConfig(BaseModel):
    start: dt.date
    injections_per_series: int = Field(ge=1)
    min_gap_days: int = Field(ge=1)
    magnitudes: list[float]
    thresholds: list[float]


class AnomalyConfig(BaseModel):
    baseline_weeks: int = Field(ge=2)
    mad_window_days: int = Field(ge=14)
    z_threshold: float = Field(gt=0)
    min_abs_deviation: float = Field(ge=0)
    evaluation: AnomalyEvalConfig


class ModelingConfig(BaseModel):
    seed: int
    boroughs: list[str]
    forecast: ForecastConfig
    delay_risk: DelayRiskConfig
    anomaly: AnomalyConfig


def load_modeling_config(path: Path | None = None) -> ModelingConfig:
    with open(path or DEFAULT_MODELING_CONFIG, encoding="utf-8") as fh:
        return ModelingConfig.model_validate(yaml.safe_load(fh))
