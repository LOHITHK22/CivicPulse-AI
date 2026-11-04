"""Seven-day closure-delay risk: P(request_not_closed_within_7_days | information at creation).

Target
    request_not_closed_within_7_days = 1 if the request has no recorded closure within 7 x 24 h of
    created_at (still open, or closed later), else 0.

What is and is not knowable
* The warehouse is a SNAPSHOT: each request's latest version. The source has no status history, so
  the label uses the snapshot's ``closed_at``. A request closed on day 3, re-opened and closed again
  on day 20 is labeled 1 here even though it looked closed on day 7. This cannot be reconstructed
  and is documented as a limitation; we do NOT invent point-in-time records.
* Label maturity: a label is only final once 7 days have passed. Requests created after
  ``maturity_cutoff = observed_until - 7 days`` are IMMATURE: excluded from training/evaluation
  and only ever scored (that is the production use case).
* Unknown labels: requests whose recorded closure is invalid (closed before created, closed in the
  future, or status Closed without a date) are excluded: their true closure time is unknown.

Features (creation-time only)
* Categorical: category, descriptor, borough, community board, intake channel, location type.
* Calendar: hour, day of week, month, weekend.
* Load: requests in the same category x borough over the previous 7 full days, and earlier the
  same day (counts of requests that already existed at creation time).
* Recent performance: share of requests in the same category x borough (and category x community
  board) created 8-35 days earlier that were not closed within 7 days. Those labels had all matured
  before the creation day, so they were knowable at prediction time.
Status, closure, resolution and due-date fields are never features (they are post-creation).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from civicpulse_modeling import metrics as M
from civicpulse_modeling.config import DelayRiskConfig
from civicpulse_modeling.data import connect
from civicpulse_shared.logging import log
from civicpulse_shared.paths import DataPaths

logger = logging.getLogger(__name__)

TARGET = "request_not_closed_within_7_days"
CATEGORICAL = ("category_key", "descriptor", "borough", "community_board", "channel", "location_type")
NUMERIC = (
    "created_hour",
    "created_dow",
    "created_month",
    "is_weekend",
    "load_prev_7d",
    "load_same_day_before",
    "trailing_delay_rate_borough",
    "trailing_n_borough",
    "trailing_delay_rate_cb",
    "trailing_n_cb",
)
FEATURES = CATEGORICAL + NUMERIC
OTHER = "__other__"
FORBIDDEN_FEATURES = {"status", "status_group", "closed_at", "hours_to_close", "resolution_updated_at", "due_at"}


@dataclass
class Snapshot:
    """When the data was observed. Labels are final only for requests created <= maturity_cutoff."""

    observed_until: dt.datetime  # NYC local, naive
    maturity_cutoff: dt.datetime
    threshold_days: int

    def to_dict(self) -> dict[str, str | int]:
        return {
            "observed_until": self.observed_until.isoformat(),
            "maturity_cutoff": self.maturity_cutoff.isoformat(),
            "threshold_days": self.threshold_days,
        }


def snapshot_from_data(max_created_at: str, source_rows_updated_at: str | None, threshold_days: int) -> Snapshot:
    """observed_until = min(latest created_at, source last update in NYC time). Conservative."""
    obs = dt.datetime.fromisoformat(max_created_at)
    if source_rows_updated_at:
        from zoneinfo import ZoneInfo

        src = dt.datetime.fromisoformat(source_rows_updated_at).astimezone(ZoneInfo("America/New_York"))
        obs = min(obs, src.replace(tzinfo=None))
    return Snapshot(obs, obs - dt.timedelta(days=threshold_days), threshold_days)


FEATURE_SQL = """
WITH base AS (
  SELECT unique_key, created_at, created_date, category_key,
         coalesce(descriptor, 'UNKNOWN') AS descriptor, borough,
         coalesce(community_board, 'UNKNOWN') AS community_board,
         coalesce(channel, 'UNKNOWN') AS channel,
         coalesce(location_type, 'UNKNOWN') AS location_type,
         created_hour, created_dow, created_month,
         (created_dow >= 6)::INTEGER AS is_weekend,
         (dq_closed_before_created OR dq_closed_in_future OR dq_closed_status_without_date) AS label_unknown,
         CASE WHEN closed_at IS NOT NULL AND closed_at <= created_at + to_days(?::INTEGER) THEN 0 ELSE 1 END AS y
  FROM fact
),
-- Per key x day. RANGE frames over the date value: days with no requests simply contribute nothing.
-- Trailing outcome windows end 8 days before the creation day, so every label used had matured.
roll_b AS (
  SELECT category_key, borough, created_date,
    coalesce(sum(n) OVER w_load, 0) AS load_prev_7d,
    sum(n_delayed) OVER w_out / nullif(sum(n_labeled) OVER w_out, 0) AS trailing_delay_rate_borough,
    coalesce(sum(n_labeled) OVER w_out, 0) AS trailing_n_borough
  FROM (SELECT category_key, borough, created_date, count(*) AS n,
               count(*) FILTER (WHERE NOT label_unknown AND y = 1) AS n_delayed,
               count(*) FILTER (WHERE NOT label_unknown) AS n_labeled
        FROM base GROUP BY ALL)
  WINDOW w_load AS (PARTITION BY category_key, borough ORDER BY created_date
                    RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 1 DAY PRECEDING),
         w_out AS (PARTITION BY category_key, borough ORDER BY created_date
                   RANGE BETWEEN INTERVAL 35 DAY PRECEDING AND INTERVAL 8 DAY PRECEDING)
),
roll_cb AS (
  SELECT category_key, community_board, created_date,
    sum(n_delayed) OVER w_out / nullif(sum(n_labeled) OVER w_out, 0) AS trailing_delay_rate_cb,
    coalesce(sum(n_labeled) OVER w_out, 0) AS trailing_n_cb
  FROM (SELECT category_key, community_board, created_date,
               count(*) FILTER (WHERE NOT label_unknown AND y = 1) AS n_delayed,
               count(*) FILTER (WHERE NOT label_unknown) AS n_labeled
        FROM base GROUP BY ALL)
  WINDOW w_out AS (PARTITION BY category_key, community_board ORDER BY created_date
                   RANGE BETWEEN INTERVAL 35 DAY PRECEDING AND INTERVAL 8 DAY PRECEDING)
)
SELECT b.unique_key, b.created_at, b.created_date, b.category_key, b.descriptor, b.borough,
       b.community_board, b.channel, b.location_type, b.created_hour, b.created_dow, b.created_month,
       b.is_weekend,
       rb.load_prev_7d,
       count(*) OVER (PARTITION BY b.category_key, b.borough, b.created_date ORDER BY b.created_at, b.unique_key
                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS load_same_day_before,
       rb.trailing_delay_rate_borough, rb.trailing_n_borough,
       rc.trailing_delay_rate_cb, rc.trailing_n_cb,
       b.label_unknown, b.y
FROM base b
JOIN roll_b rb USING (category_key, borough, created_date)
JOIN roll_cb rc USING (category_key, community_board, created_date)
ORDER BY b.created_at, b.unique_key
"""


def load_frame(paths: DataPaths, threshold_days: int) -> pl.DataFrame:
    """All requests with creation-time features and the (possibly immature) snapshot label."""
    with connect(paths) as con:
        con.execute("SET preserve_insertion_order = true")
        df: pl.DataFrame = con.sql(FEATURE_SQL, params=[threshold_days]).pl()
    return df.with_columns(pl.col("y").cast(pl.Int8))


def assign_splits(df: pl.DataFrame, cfg: DelayRiskConfig, snap: Snapshot) -> pl.DataFrame:
    """Chronological train / validation / test with embargo gaps; immature and unknown labels excluded."""
    emb = dt.timedelta(days=cfg.embargo_days)
    assert cfg.train.end is not None and cfg.validation.end is not None

    def day_start(d: dt.date) -> dt.datetime:
        return dt.datetime.combine(d, dt.time())

    train_end = min(day_start(cfg.train.end) + dt.timedelta(days=1), day_start(cfg.validation.start) - emb)
    val_end = min(day_start(cfg.validation.end) + dt.timedelta(days=1), day_start(cfg.test.start) - emb)
    test_end = snap.maturity_cutoff
    if cfg.test.end is not None:
        test_end = min(test_end, day_start(cfg.test.end) + dt.timedelta(days=1))
    c = pl.col("created_at")
    split = (
        pl.when(c > snap.maturity_cutoff)
        .then(pl.lit("immature"))
        .when(pl.col("label_unknown"))
        .then(pl.lit("label_unknown"))
        .when((c >= day_start(cfg.train.start)) & (c < train_end))
        .then(pl.lit("train"))
        .when((c >= day_start(cfg.validation.start)) & (c < val_end))
        .then(pl.lit("validation"))
        .when((c >= day_start(cfg.test.start)) & (c <= test_end))
        .then(pl.lit("test"))
        .otherwise(pl.lit("embargo"))
    )
    return df.with_columns(split.alias("split"))


# ---------------------------------------------------------------- encoding (train statistics only)


@dataclass
class Encoder:
    """Category vocabularies and numeric medians learned from TRAINING rows only."""

    vocab: dict[str, list[str]] = field(default_factory=dict)
    medians: dict[str, float] = field(default_factory=dict)

    @classmethod
    def fit(cls, train: pl.DataFrame, min_count: int) -> Encoder:
        vocab = {}
        for col in CATEGORICAL:
            counts = train.group_by(col).len().filter(pl.col("len") >= min_count).sort(col)
            vocab[col] = [OTHER, *[str(v) for v in counts[col].to_list()]]
        medians = {}
        for col in NUMERIC:
            m = train[col].cast(pl.Float64).median()
            medians[col] = float(m) if isinstance(m, int | float) else 0.0
        return cls(vocab=vocab, medians=medians)

    def categorical_codes(self, df: pl.DataFrame) -> np.ndarray:
        out = np.zeros((df.height, len(CATEGORICAL)), dtype=np.int32)
        for j, col in enumerate(CATEGORICAL):
            lookup = {v: i for i, v in enumerate(self.vocab[col])}
            out[:, j] = (
                df[col].cast(pl.Utf8).replace_strict(lookup, default=0, return_dtype=pl.Int32).fill_null(0).to_numpy()
            )
        return out

    def numeric(self, df: pl.DataFrame, impute: bool) -> np.ndarray:
        cols = []
        for col in NUMERIC:
            x = df[col].cast(pl.Float64).to_numpy().astype(float)
            if impute:
                x = np.where(np.isnan(x), self.medians[col], x)
            cols.append(x)
        return np.column_stack(cols)

    def lgb_matrix(self, df: pl.DataFrame) -> np.ndarray:
        # LightGBM handles missing numerics natively; categoricals use train vocab codes.
        return np.column_stack([self.categorical_codes(df).astype(float), self.numeric(df, impute=False)])

    def to_dict(self) -> dict[str, Any]:
        return {"vocab": self.vocab, "medians": self.medians}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Encoder:
        return cls(vocab=d["vocab"], medians=d["medians"])


# ---------------------------------------------------------------- logistic regression (portable)


LOG_NUMERIC = {"load_prev_7d", "load_same_day_before", "trailing_n_borough", "trailing_n_cb"}


@dataclass
class LogisticModel:
    """One-hot + standardized-numeric logistic regression, exported as plain JSON coefficients."""

    encoder: Encoder
    num_mean: dict[str, float]
    num_scale: dict[str, float]
    coef_cat: dict[str, list[float]]  # per categorical column, one coefficient per vocab entry
    coef_num: dict[str, float]
    intercept: float

    @staticmethod
    def _numeric_transform(enc: Encoder, df: pl.DataFrame) -> np.ndarray:
        x = enc.numeric(df, impute=True)
        for j, col in enumerate(NUMERIC):
            if col in LOG_NUMERIC:
                x[:, j] = np.log1p(np.maximum(x[:, j], 0))
        return x

    @classmethod
    def fit(cls, enc: Encoder, train: pl.DataFrame, params: dict[str, Any], seed: int) -> LogisticModel:
        from scipy import sparse
        from sklearn.linear_model import LogisticRegression

        codes = enc.categorical_codes(train)
        offsets = np.cumsum([0, *[len(enc.vocab[c]) for c in CATEGORICAL]])
        rows = np.repeat(np.arange(train.height), len(CATEGORICAL))
        cols = (codes + offsets[:-1]).ravel()
        x_cat = sparse.csr_matrix((np.ones(len(cols)), (rows, cols)), shape=(train.height, offsets[-1]))
        x_num = cls._numeric_transform(enc, train)
        mean, scale = x_num.mean(axis=0), x_num.std(axis=0)
        scale[scale == 0] = 1.0
        X = sparse.hstack([x_cat, sparse.csr_matrix((x_num - mean) / scale)]).tocsr()
        y = train["y"].to_numpy()
        lr = LogisticRegression(C=params["C"], max_iter=params["max_iter"], solver="lbfgs", random_state=seed)
        lr.fit(X, y)
        coef = lr.coef_.ravel()
        coef_cat = {c: coef[offsets[j] : offsets[j + 1]].tolist() for j, c in enumerate(CATEGORICAL)}
        coef_num = {c: float(coef[offsets[-1] + j]) for j, c in enumerate(NUMERIC)}
        return cls(
            encoder=enc,
            num_mean=dict(zip(NUMERIC, mean.tolist(), strict=True)),
            num_scale=dict(zip(NUMERIC, scale.tolist(), strict=True)),
            coef_cat=coef_cat,
            coef_num=coef_num,
            intercept=float(lr.intercept_[0]),
        )

    def contributions(self, df: pl.DataFrame) -> np.ndarray:
        """Per-feature log-odds contributions (n_rows, len(FEATURES)); sum + intercept = logit."""
        codes = self.encoder.categorical_codes(df)
        out = np.zeros((df.height, len(FEATURES)))
        for j, c in enumerate(CATEGORICAL):
            out[:, j] = np.asarray(self.coef_cat[c])[codes[:, j]]
        x = self._numeric_transform(self.encoder, df)
        for j, c in enumerate(NUMERIC):
            out[:, len(CATEGORICAL) + j] = (x[:, j] - self.num_mean[c]) / self.num_scale[c] * self.coef_num[c]
        return out

    def predict_proba(self, df: pl.DataFrame) -> np.ndarray:
        z = self.contributions(df).sum(axis=1) + self.intercept
        return 1 / (1 + np.exp(-z))

    def to_dict(self) -> dict[str, Any]:
        return {
            "encoder": self.encoder.to_dict(),
            "num_mean": self.num_mean,
            "num_scale": self.num_scale,
            "coef_cat": self.coef_cat,
            "coef_num": self.coef_num,
            "intercept": self.intercept,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LogisticModel:
        return cls(
            encoder=Encoder.from_dict(d["encoder"]),
            num_mean=d["num_mean"],
            num_scale=d["num_scale"],
            coef_cat=d["coef_cat"],
            coef_num=d["coef_num"],
            intercept=d["intercept"],
        )


# ---------------------------------------------------------------- LightGBM


def train_lgbm(
    enc: Encoder, train: pl.DataFrame, valid: pl.DataFrame, params: dict[str, Any], seed: int
) -> lgb.Booster:
    p = {
        "objective": "binary",
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 4,
        "seed": seed,
        **params,
    }
    n_estimators = p.pop("n_estimators")
    stop = p.pop("early_stopping_rounds")
    cat_idx = list(range(len(CATEGORICAL)))
    dtrain = lgb.Dataset(
        enc.lgb_matrix(train), label=train["y"].to_numpy(), feature_name=list(FEATURES), categorical_feature=cat_idx
    )
    dvalid = lgb.Dataset(enc.lgb_matrix(valid), label=valid["y"].to_numpy(), reference=dtrain)
    return lgb.train(
        p,
        dtrain,
        num_boost_round=n_estimators,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(stop, verbose=False)],
    )


def lgbm_contributions(model: lgb.Booster, enc: Encoder, df: pl.DataFrame) -> np.ndarray:
    """TreeSHAP contributions in log-odds (last column = expected value)."""
    return np.asarray(model.predict(enc.lgb_matrix(df), pred_contrib=True))


# ---------------------------------------------------------------- evaluation


def segment_metrics(df: pl.DataFrame, p: np.ndarray, col: str, min_n: int = 200) -> list[dict[str, Any]]:
    out = []
    y = df["y"].to_numpy()
    seg = df[col].to_numpy()
    for v in sorted(set(seg.tolist())):
        m = seg == v
        if m.sum() < min_n:
            continue
        out.append({col: v} | M.classification_summary(y[m], p[m]))
    return out


def evaluate_split(df: pl.DataFrame, p: np.ndarray, threshold: float) -> dict[str, Any]:
    y = df["y"].to_numpy()
    return {
        "overall": M.classification_summary(y, p),
        "at_threshold": M.threshold_metrics(y, p, threshold),
        "top_10pct": M.precision_at_top(y, p, 0.10),
        "reliability": M.reliability(y, p, 10),
        "by_category": segment_metrics(df, p, "category_key"),
        "by_borough": segment_metrics(df, p, "borough"),
    }


def permutation_importance(score_fn: Any, df: pl.DataFrame, repeats: int, seed: int) -> list[dict[str, float]]:
    """Drop in PR-AUC when one feature column is shuffled (on a held-out sample)."""
    rng = np.random.default_rng(seed)
    y = df["y"].to_numpy()
    base = M.pr_auc(y, score_fn(df))
    out: list[dict[str, Any]] = []
    for col in FEATURES:
        drops = []
        for _ in range(repeats):
            shuffled = df.with_columns(df[col].gather(rng.permutation(df.height)).alias(col))
            drops.append(base - M.pr_auc(y, score_fn(shuffled)))
        out.append({"feature": col, "pr_auc_drop_mean": float(np.mean(drops)), "pr_auc_drop_std": float(np.std(drops))})
    return sorted(out, key=lambda r: -r["pr_auc_drop_mean"])


def top_contributions(contrib: np.ndarray, df: pl.DataFrame, k: int = 5) -> list[list[dict[str, Any]]]:
    """Top-|k| feature contributions per row, with the feature's value for display."""
    out = []
    vals = {c: df[c].to_list() for c in FEATURES}
    for i in range(contrib.shape[0]):
        row = contrib[i, : len(FEATURES)]
        idx = np.argsort(-np.abs(row))[:k]
        out.append(
            [
                {
                    "feature": FEATURES[j],
                    "value": None if vals[FEATURES[j]][i] is None else str(vals[FEATURES[j]][i]),
                    "contribution": round(float(row[j]), 4),
                }
                for j in idx
            ]
        )
    return out


def log_split_sizes(df: pl.DataFrame) -> dict[str, dict[str, Any]]:
    out = {}
    for (s,), g in df.group_by(["split"]):
        out[str(s)] = {
            "n": g.height,
            "positive_rate": float(g["y"].mean()) if g.height else None,  # type: ignore[arg-type]
            "created_from": str(g["created_at"].min()),
            "created_to": str(g["created_at"].max()),
        }
    log(logger, logging.INFO, "delay-risk splits", **{k: v["n"] for k, v in out.items()})
    return out
